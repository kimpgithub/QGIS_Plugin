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
import glob
import json
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from gis_scan_tools.tools._legacy.common import (
        parse_jgw, write_jgw, JGWParams, PRJ_5179, extract_map_region,
    )
except ImportError:
    from .common import (
        parse_jgw, write_jgw, JGWParams, PRJ_5179, extract_map_region,
    )


def main_map_world_bbox(pdf_jpg, pdf_jgw_path):
    img = cv2.imread(pdf_jpg)
    jgw = parse_jgw(pdf_jgw_path)
    _, (mbx, mby, mbw, mbh) = extract_map_region(img)
    minx = jgw.top_left_x + mbx * jgw.pixel_size_x
    maxx = jgw.top_left_x + (mbx + mbw) * jgw.pixel_size_x
    maxy = jgw.top_left_y + mby * jgw.pixel_size_y
    miny = jgw.top_left_y + (mby + mbh) * jgw.pixel_size_y
    return minx, miny, maxx, maxy


def crop_to_world_bbox(img, jgw, world_bbox):
    """warped scan을 world bbox로 크롭 + 새 JGW."""
    qx0, qy0, qx1, qy1 = world_bbox
    px0 = (qx0 - jgw.top_left_x) / jgw.pixel_size_x
    py0 = (qy1 - jgw.top_left_y) / jgw.pixel_size_y
    px1 = (qx1 - jgw.top_left_x) / jgw.pixel_size_x
    py1 = (qy0 - jgw.top_left_y) / jgw.pixel_size_y
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
                out_dir):
    """단일 행정코드 병합 — 시트별 world bbox로 크롭 후 모자이크."""
    result = {'admin_code': admin_code, 'status': 'OK',
              'message': '', 'sheets': []}

    pdf_jpg = os.path.join(pdf_main_dir, f'{admin_code}.jpg')
    pdf_jgw_path = os.path.join(pdf_main_dir, f'{admin_code}.jgw')
    if not os.path.exists(pdf_jpg):
        result.update(status='ERROR', message='PDF 메인 없음')
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
    code_dir = os.path.join(warped_dir, admin_code)
    if os.path.isdir(code_dir):
        for folder in sorted(os.listdir(code_dir)):
            if not folder.startswith(f'{admin_code}_'):
                continue
            sid = folder[len(admin_code) + 1:]
            if sid not in bboxes:
                continue
            cand = [
                os.path.join(code_dir, folder, f'{folder}.jpg'),
                os.path.join(code_dir, folder, 'warped_scan.jpg'),
            ]
            for sj in cand:
                if os.path.exists(sj):
                    jgw_path = os.path.splitext(sj)[0] + '.jgw'
                    if os.path.exists(jgw_path):
                        sheets.append((sid, sj, jgw_path))
                        break

    if not sheets:
        result.update(status='ERROR', message='유효 시트 매칭 없음')
        return result

    # 캔버스 설정
    target_ps = abs(parse_jgw(sheets[0][2]).pixel_size_x)
    cw = int(round((maxx - minx) / target_ps))
    ch = int(round((maxy - miny) / target_ps))
    canvas = np.full((ch, cw, 3), 255, np.uint8)

    for sid, sj, jgw_path in sheets:
        img = cv2.imread(sj)
        jgw = parse_jgw(jgw_path)
        wb = bboxes[sid]
        crop, cj = crop_to_world_bbox(img, jgw, wb)
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

    os.makedirs(out_dir, exist_ok=True)
    out_jpg = os.path.join(out_dir, f'{admin_code}_scan_merged.jpg')
    out_jgw = os.path.join(out_dir, f'{admin_code}_scan_merged.jgw')
    out_prj = os.path.join(out_dir, f'{admin_code}_scan_merged.prj')
    cv2.imwrite(out_jpg, canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
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
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    with open(args.sheet_bboxes) as f:
        sheet_bboxes = json.load(f)

    admin_codes = sorted([
        d for d in os.listdir(args.warped)
        if os.path.isdir(os.path.join(args.warped, d))
        and len(d) == 8 and d.isdigit()
    ])
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
                                sheet_bboxes, args.out_dir)
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
