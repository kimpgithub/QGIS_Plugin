"""화면정의서 S7: 지도영역 자동 추출 (스캔 → 프레임 안쪽 영역만 잘라 저장).

스캔 폴더 재귀 → 각 jpg/jpeg/png에서 검정 프레임 라인 검출 →
프레임 안쪽 지도영역만 추출 → 시도/시군구 폴더 구조 보존하여 저장.

용도:
- 검수: 작업자가 잘린 결과로 이미지 품질/정렬 확인
- 후속 활용: Stage 3 매칭이 외곽 노이즈 영향 받지 않도록 정제된 입력

CLI:
  python -m gis_scan_tools.tools.stage_extract_map \\
      --in scan/ --out map_only/ [--margin 3]
"""
import argparse
import csv
import glob
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from ._legacy.common import extract_map_region
except ImportError:
    from gis_scan_tools.tools._legacy.common import extract_map_region


def _imread(path):
    try:
        d = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(d, cv2.IMREAD_COLOR) if d.size else None
    except Exception:
        return None


def _imwrite(path, img, q=92):
    ext = os.path.splitext(path)[1] or '.jpg'
    ok, buf = cv2.imencode(ext, img, [cv2.IMWRITE_JPEG_QUALITY, q])
    if not ok:
        return False
    buf.tofile(path)
    return True


def main():
    ap = argparse.ArgumentParser(
        description='지도영역 자동 추출 (화면정의서 S7)')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='스캔 폴더 (재귀, 시도/시군구 구조 보존)')
    ap.add_argument('--out', dest='out_dir', required=True,
                    help='출력 폴더 (입력 구조 그대로 복제)')
    ap.add_argument('--margin', type=int, default=3,
                    help='프레임 안쪽 여유 (px, 기본 3)')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    inputs = []
    for ext in ('*.jpg', '*.jpeg', '*.JPG', '*.JPEG', '*.png', '*.PNG'):
        inputs += glob.glob(os.path.join(args.in_dir, '**', ext),
                            recursive=True)
    inputs = sorted(set(p for p in inputs if 'checkpoint' not in p))
    print(f'[지도영역 추출] {len(inputs)}장 처리 시작')

    csv_path = os.path.join(args.out_dir, '_status.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['input', 'output', 'status', 'orig_size',
                    'crop_size', 'message', 'elapsed_s'])
        n_ok = n_err = 0
        t_total = time.time()
        for i, src in enumerate(inputs, 1):
            t0 = time.time()
            rel = os.path.relpath(src, args.in_dir)
            dst = os.path.join(args.out_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            img = _imread(src)
            if img is None:
                w.writerow([src, '', 'ERROR', '', '', 'imread 실패',
                            f'{time.time()-t0:.2f}'])
                n_err += 1
                print(f'  [{i}/{len(inputs)}] ERROR (load): '
                      f'{os.path.basename(src)}')
                continue
            try:
                crop, _ = extract_map_region(img, margin=args.margin)
                _imwrite(dst, crop)
                oh, ow = img.shape[:2]
                ch, cw = crop.shape[:2]
                w.writerow([src, dst, 'OK', f'{ow}x{oh}',
                            f'{cw}x{ch}', '', f'{time.time()-t0:.2f}'])
                n_ok += 1
            except Exception as e:
                w.writerow([src, '', 'ERROR', '', '', str(e),
                            f'{time.time()-t0:.2f}'])
                n_err += 1
                print(f'  [{i}/{len(inputs)}] ERROR: {e}')
            if i % 5 == 0 or i == len(inputs):
                print(f'  [{i}/{len(inputs)}] OK={n_ok} ERR={n_err} '
                      f'({(time.time()-t_total)/i:.1f}s/장)')

    print(f'\n[지도영역 추출] 완료: OK={n_ok}, ERROR={n_err}')
    print(f'  결과: {csv_path}')


if __name__ == '__main__':
    main()
