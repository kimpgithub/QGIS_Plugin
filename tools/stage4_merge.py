"""Stage 4: sheet_bboxes 기반 크롭 + 모자이크 병합

Stage 2의 sheet_bboxes.json (각 시트의 world bbox)을 사용하여
Stage 3 워핑 결과를 크롭·병합. 사분면 휴리스틱 없음 — 불규칙 그리드 자동 지원.

CLI:
  python -m gis_scan_tools.tools.stage4_merge \\
      --warped warped/ --sheet-bboxes scan_identified/sheet_bboxes.json \\
      --pdf-main pdf_main_geo/ --out merged/
"""
import argparse
import csv
import json
import os
import sys
import time

import cv2
import numpy as np


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from .common import (
        parse_jgw, write_jgw, JGWParams, PRJ_5179, extract_map_region,
        find_main_image,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )
except ImportError:
    from gis_scan_tools.tools.common import (
        parse_jgw, write_jgw, JGWParams, PRJ_5179, extract_map_region,
        find_main_image,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )


def main_map_world_bbox(pdf_jpg, pdf_jgw_path):
    img = _imread(pdf_jpg)
    jgw = parse_jgw(pdf_jgw_path)
    _, (mbx, mby, mbw, mbh) = extract_map_region(img)
    minx = jgw.top_left_x + mbx * jgw.pixel_size_x
    maxx = jgw.top_left_x + (mbx + mbw) * jgw.pixel_size_x
    maxy = jgw.top_left_y + mby * jgw.pixel_size_y
    miny = jgw.top_left_y + (mby + mbh) * jgw.pixel_size_y
    return minx, miny, maxx, maxy


def crop_to_world_bbox(img, jgw, world_bbox, inner_margin_px=0):
    """warped scan을 world bbox로 크롭 + 새 JGW.

    inner_margin_px > 0 이면 bbox 모든 변에서 안쪽으로 그만큼 더 잘라냄.
    화면정의서 S9 — 시트 간 경계 부정확/중복 영역 회피용.
    """
    qx0, qy0, qx1, qy1 = world_bbox
    px0 = (qx0 - jgw.top_left_x) / jgw.pixel_size_x + inner_margin_px
    py0 = (qy1 - jgw.top_left_y) / jgw.pixel_size_y + inner_margin_px
    px1 = (qx1 - jgw.top_left_x) / jgw.pixel_size_x - inner_margin_px
    py1 = (qy0 - jgw.top_left_y) / jgw.pixel_size_y - inner_margin_px
    h, w = img.shape[:2]
    px0i = max(0, int(np.floor(px0)))
    py0i = max(0, int(np.floor(py0)))
    px1i = min(w, int(np.ceil(px1)))
    py1i = min(h, int(np.ceil(py1)))
    if px1i <= px0i or py1i <= py0i:
        return None, None
    crop = img[py0i:py1i, px0i:px1i]
    new_jgw = JGWParams(
        pixel_size_x=jgw.pixel_size_x, rotation_x=0, rotation_y=0,
        pixel_size_y=jgw.pixel_size_y,
        top_left_x=jgw.top_left_x + px0i * jgw.pixel_size_x,
        top_left_y=jgw.top_left_y + py0i * jgw.pixel_size_y)
    return crop, new_jgw


def merge_admin(admin_code, warped_dir, pdf_main_dir, sheet_bboxes,
                out_dir, inner_margin_px=0):
    """단일 행정코드 병합 — 시트별 world bbox로 크롭 후 모자이크."""
    result = {'admin_code': admin_code, 'status': 'OK',
              'message': '', 'sheets': []}

    pdf_jpg = find_main_image(pdf_main_dir, admin_code)
    pdf_jgw_path = os.path.join(pdf_main_dir, f'{admin_code}.jgw')
    if pdf_jpg is None:
        result.update(status='ERROR',
                      message=f'PDF 메인 없음: {admin_code}.{{tif,jpg}}')
        return result

    map_bbox = main_map_world_bbox(pdf_jpg, pdf_jgw_path)
    minx, miny, maxx, maxy = map_bbox

    bboxes = sheet_bboxes.get(admin_code, {})
    if not bboxes:
        result.update(status='ERROR', message='sheet_bboxes에 admin 없음')
        return result

    # 워핑 시트 수집: warped/{code}/{code}_{sheet_id}/{code}_{sheet_id}.jpg
    # (구버전 warped_scan.jpg도 폴백으로 지원)
    sheets = []
    skipped = []
    # 새 구조: warped_dir/{시도}/{시군구}/{admin}_{sheet}/
    # 구버전 호환: warped_dir/{admin}/{admin}_{sheet}/ 도 지원
    code_dir_candidates = [
        os.path.join(warped_dir, admin_code[:2], admin_code[:5]),
        os.path.join(warped_dir, admin_code),
    ]
    code_dir = next((d for d in code_dir_candidates if os.path.isdir(d)), None)
    if code_dir:
        for folder in sorted(os.listdir(code_dir)):
            if not folder.startswith(f'{admin_code}_'):
                continue
            sid = folder[len(admin_code) + 1:]
            if sid not in bboxes:
                skipped.append((sid, 'NO_BBOX'))
                continue
            cand = [
                os.path.join(code_dir, folder, f'{folder}.jpg'),
                os.path.join(code_dir, folder, 'warped_scan.jpg'),
            ]
            found = False
            for sj in cand:
                if os.path.exists(sj):
                    jgw_path = os.path.splitext(sj)[0] + '.jgw'
                    if os.path.exists(jgw_path):
                        sheets.append((sid, sj, jgw_path))
                        found = True
                        break
            if not found:
                skipped.append((sid, 'NO_JPG'))
    if skipped:
        result['skipped'] = [{'sheet': s, 'reason': r} for s, r in skipped]
        print(f'  ⚠ 누락 시트: {skipped}')

    if not sheets:
        result.update(status='ERROR', message='유효 시트 매칭 없음')
        return result

    # 캔버스 설정
    target_ps = abs(parse_jgw(sheets[0][2]).pixel_size_x)
    cw = int(round((maxx - minx) / target_ps))
    ch = int(round((maxy - miny) / target_ps))
    canvas = np.full((ch, cw, 3), 255, np.uint8)

    for sid, sj, jgw_path in sheets:
        img = _imread(sj)
        jgw = parse_jgw(jgw_path)
        wb = bboxes[sid]
        crop, cj = crop_to_world_bbox(img, jgw, wb,
                                       inner_margin_px=inner_margin_px)
        if crop is None:
            result['sheets'].append({'sheet': sid, 'status': 'EMPTY_CROP'})
            continue
        cx_px = int(round((cj.top_left_x - minx) / target_ps))
        cy_px = int(round((maxy - cj.top_left_y) / target_ps))
        ph, pw = crop.shape[:2]
        y1, y2 = max(0, cy_px), min(ch, cy_px + ph)
        x1, x2 = max(0, cx_px), min(cw, cx_px + pw)
        ty1, ty2 = y1 - cy_px, y2 - cy_px
        tx1, tx2 = x1 - cx_px, x2 - cx_px
        canvas[y1:y2, x1:x2] = crop[ty1:ty2, tx1:tx2]
        result['sheets'].append({
            'sheet': sid, 'status': 'OK',
            'world_bbox': list(wb), 'crop_size': [pw, ph]})

    # 출력: {out}/{시도}/{시군구}/{code}_scan_merged.{jpg,jgw,prj}
    sub_out = os.path.join(out_dir, admin_code[:2], admin_code[:5])
    os.makedirs(sub_out, exist_ok=True)
    out_jpg = os.path.join(sub_out, f'{admin_code}_scan_merged.jpg')
    out_jgw = os.path.join(sub_out, f'{admin_code}_scan_merged.jgw')
    out_prj = os.path.join(sub_out, f'{admin_code}_scan_merged.prj')
    _imwrite(out_jpg, canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
    write_jgw(out_jgw, JGWParams(
        pixel_size_x=target_ps, rotation_x=0, rotation_y=0,
        pixel_size_y=-target_ps,
        top_left_x=minx, top_left_y=maxy))
    with open(out_prj, 'w') as f:
        f.write(PRJ_5179)
    result.update(canvas_size=[cw, ch], output=out_jpg)
    return result


def main():
    ap = argparse.ArgumentParser(description='Stage 4: sheet bbox 기반 병합')
    ap.add_argument('--warped', required=True)
    ap.add_argument('--sheet-bboxes', required=True,
                    help='Stage 2 산출 sheet_bboxes.json')
    ap.add_argument('--pdf-main', required=True)
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--inner-margin', type=int, default=0,
                    help='시트 안쪽 여유 (px). 시트 경계 부정확 영역 제거. 기본 0')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    with open(args.sheet_bboxes) as f:
        sheet_bboxes = json.load(f)

    # 새 구조: warped/{시도}/{시군구}/{admin}_{sheet}/
    # 구조 탐색: 어느 레벨이든 8자리 숫자 폴더를 찾으면 그게 admin_code
    admin_codes = set()
    for root, dirs, files in os.walk(args.warped):
        for d in dirs:
            # admin_{sheet} 형식
            parts = d.split('_')
            if len(parts) == 2 and len(parts[0]) == 8 and parts[0].isdigit():
                admin_codes.add(parts[0])
    # 호환: 구버전 루트 admin 폴더도 포함
    for d in os.listdir(args.warped):
        if (os.path.isdir(os.path.join(args.warped, d))
                and len(d) == 8 and d.isdigit()):
            admin_codes.add(d)
    admin_codes = sorted(admin_codes)
    print(f'[Stage 4] 행정코드 {len(admin_codes)}개 병합 시작')

    csv_path = os.path.join(args.out_dir, '_status.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['admin_code', 'status', 'n_sheets', 'canvas_w',
                    'canvas_h', 'message', 'output', 'elapsed_s'])
        n_ok = n_fail = 0
        for i, code in enumerate(admin_codes, 1):
            t0 = time.time()
            print(f'\n[{i}/{len(admin_codes)}] {code}')
            try:
                r = merge_admin(code, args.warped, args.pdf_main,
                                sheet_bboxes, args.out_dir,
                                inner_margin_px=args.inner_margin)
                cs = r.get('canvas_size', [0, 0])
                w.writerow([code, r['status'],
                            sum(1 for s in r.get('sheets', [])
                                if s.get('status') == 'OK'),
                            cs[0], cs[1], r.get('message', ''),
                            r.get('output', ''), f'{time.time()-t0:.1f}'])
                if r['status'] == 'OK':
                    n_ok += 1
                else:
                    n_fail += 1
                with open(os.path.join(args.out_dir,
                                       f'{code}_status.json'), 'w') as sf:
                    json.dump(r, sf, indent=2)
            except Exception as e:
                w.writerow([code, 'ERROR', 0, 0, 0, str(e), '',
                            f'{time.time()-t0:.1f}'])
                n_fail += 1
                print(f'  ERROR: {e}')

    print(f'\n[Stage 4] 완료: OK={n_ok}, FAIL/ERROR={n_fail}')


if __name__ == '__main__':
    main()
