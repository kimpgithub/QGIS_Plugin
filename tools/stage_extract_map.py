"""지도영역 추출 — HSV "어두운 무채색" 매칭 기반 (참조 PDF 불필요).

스캔의 인쇄 프레임선은 본질적으로 "저채도 + 중간 어두움" 픽셀 — 스캐너
캘리브레이션·용지 색감 변동에 강건한 속성이다.
  1) (S < max_saturation) AND (V ≤ v_max) 픽셀 마스크 생성
     → 빨강 수기·주황 경계·흰 배경 자동 제외, 프레임/표선/검은 글자만 통과
  2) 행/열 프로파일에서 매칭 비율이 임계 이상인 군집 = 프레임 라인
     글자는 행 폭의 일부만 차지해 임계 미달, 라인은 행 전체를 가로질러 통과
  3) 위쪽 header_zone 안 가장 아래 라인 = 헤더/지도 분리선 → map_top
     아래쪽 header_zone 안 가장 위 라인 = 외곽 하단 → map_bot
     col 첫/마지막 라인 = 좌/우 외곽
  4) 검출선보다 inset px 안쪽으로 잘라 잔여 검정 줄 회피

기존 SIFT + MAGSAC + TPS 폴백 방식은 참조 PDF(sheets_geo)가 필요했고
다도해/빈 시트에서 키포인트 부족으로 불안정했음. 새 방식은 참조 불필요·
시트당 < 0.5s· 빈 시트도 정상 처리.

입력:
  --identified  Stage 2 산출 identified/{시도}/{시군구}/{admin}_{sheet}.jpg

출력:
  --out  {시도}/{시군구}/{admin}_{sheet}.jpg  + _status.csv
"""
import argparse
import csv
import os
import re
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from ._legacy.common import (
        extract_map_region_scan,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )
except ImportError:
    from gis_scan_tools.tools._legacy.common import (
        extract_map_region_scan,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )


FILENAME_PAT = re.compile(
    r'^(\d{8})_(\d+-\d+)(?:_\d+)?\.(jpg|jpeg|png|JPG|JPEG|PNG)$')


def _discover_identified(identified_dir):
    """{시도}/{시군구}/{admin}_{sheet}.{jpg|png} 재귀 스캔."""
    targets = []
    for root, _, files in os.walk(identified_dir):
        for f in sorted(files):
            m = FILENAME_PAT.match(f)
            if not m:
                continue
            targets.append((os.path.join(root, f), m.group(1), m.group(2)))
    return targets


def main():
    ap = argparse.ArgumentParser(
        description='지도영역 추출 (HSV "어두운 무채색" 매칭)')
    ap.add_argument('--identified', required=True,
                    help='Stage 2 산출 identified/ 폴더')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--max-saturation', type=int, default=30,
                    help='이 값 미만 채도(0~255)만 매칭 (회색조 게이트)')
    ap.add_argument('--v-max', type=int, default=130,
                    help='이 값 이하 명도(0~255)만 매칭 (흰 배경 제외)')
    ap.add_argument('--inset', type=int, default=8,
                    help='검출선 안쪽으로 자를 px (검정 잔여 회피)')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    targets = _discover_identified(args.identified)
    print(f'[지도영역 추출] {len(targets)}장 처리 시작 '
          f'(S<{args.max_saturation}, V≤{args.v_max}, inset={args.inset})')

    csv_path = os.path.join(args.out_dir, '_status.csv')
    n_ok = n_err = 0
    t_total = time.time()

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scan_path', 'output', 'status', 'admin_code', 'sheet_id',
                    'orig_size', 'crop_size', 'bbox_xywh',
                    'message', 'elapsed_s'])

        for i, (scan_path, admin, sid) in enumerate(targets, 1):
            t0 = time.time()

            scan = _imread(scan_path)
            if scan is None:
                w.writerow([scan_path, '', 'ERROR', admin, sid,
                            '', '', '', 'imread 실패',
                            f'{time.time()-t0:.2f}'])
                n_err += 1
                continue

            try:
                cropped, bbox = extract_map_region_scan(
                    scan,
                    max_saturation=args.max_saturation,
                    v_max=args.v_max,
                    inset=args.inset)
            except ValueError as e:
                w.writerow([scan_path, '', 'FAIL', admin, sid,
                            f'{scan.shape[1]}x{scan.shape[0]}', '', '',
                            str(e), f'{time.time()-t0:.2f}'])
                n_err += 1
                continue

            rel = os.path.relpath(scan_path, args.identified)
            out_path = os.path.join(args.out_dir, rel)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            _imwrite(out_path, cropped, [cv2.IMWRITE_JPEG_QUALITY, 92])

            w.writerow([
                scan_path, out_path, 'OK', admin, sid,
                f'{scan.shape[1]}x{scan.shape[0]}',
                f'{cropped.shape[1]}x{cropped.shape[0]}',
                f'{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}',
                '', f'{time.time()-t0:.2f}'])
            n_ok += 1

            if i % 5 == 0 or i == len(targets):
                print(f'  [{i}/{len(targets)}] OK={n_ok} ERR={n_err} '
                      f'({(time.time()-t_total)/i:.2f}s/장)')

    print(f'\n[지도영역 추출] 완료: OK={n_ok}, ERROR={n_err}')
    print(f'  결과: {csv_path}')


if __name__ == '__main__':
    main()
