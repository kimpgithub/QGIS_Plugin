"""Stage 4: 사분면(또는 N분면) 크롭 + 모자이크 병합

Stage 3 산출(warped/{code}/{scan}/warped_scan.{jpg,jgw,prj})을 받아,
같은 행정코드의 시트들을 모자이크로 병합.

각 시트의 자기 사분면 영역만 크롭한 뒤 메인 PDF 지도영역 캔버스에 배치.
사분면(quadrant) 결정은 시트의 world bbox 중심이 메인 지도영역의 어느 1/N에
속하는지로 자동 판단.

CLI:
  python -m gis_scan_tools.tools.stage4_merge \\
      --warped warped/ --pdf-main pdf_main_geo/ --out merged/
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
    """메인 PDF의 지도영역 world bbox."""
    img = cv2.imread(pdf_jpg)
    jgw = parse_jgw(pdf_jgw_path)
    _, (mbx, mby, mbw, mbh) = extract_map_region(img)
    minx = jgw.top_left_x + mbx * jgw.pixel_size_x
    maxx = jgw.top_left_x + (mbx + mbw) * jgw.pixel_size_x
    maxy = jgw.top_left_y + mby * jgw.pixel_size_y
    miny = jgw.top_left_y + (mby + mbh) * jgw.pixel_size_y
    return minx, miny, maxx, maxy


def assign_quadrants(sheets_world_centers, map_bbox, n_rows=2, n_cols=2):
    """각 시트가 어느 격자(r, c)에 속하는지 자동 할당.

    sheets_world_centers: [(scan_id, world_cx, world_cy), ...]
    map_bbox: (minx, miny, maxx, maxy)
    """
    minx, miny, maxx, maxy = map_bbox
    cell_w = (maxx - minx) / n_cols
    cell_h = (maxy - miny) / n_rows
    assignment = {}
    for sid, cx, cy in sheets_world_centers:
        c = min(n_cols - 1, max(0, int((cx - minx) / cell_w)))
        r = min(n_rows - 1, max(0, int((maxy - cy) / cell_h)))  # row 0 = 북
        assignment[sid] = (r, c)
    return assignment


def quadrant_bbox(r, c, map_bbox, n_rows, n_cols):
    minx, miny, maxx, maxy = map_bbox
    cell_w = (maxx - minx) / n_cols
    cell_h = (maxy - miny) / n_rows
    qx0 = minx + c * cell_w
    qx1 = minx + (c + 1) * cell_w
    qy1 = maxy - r * cell_h          # 북쪽
    qy0 = maxy - (r + 1) * cell_h    # 남쪽
    return qx0, qy0, qx1, qy1


def crop_to_world_bbox(img, jgw, qbbox):
    """warped scan의 일부를 world bbox로 크롭. 새 JGW도 반환."""
    qx0, qy0, qx1, qy1 = qbbox
    px0 = (qx0 - jgw.top_left_x) / jgw.pixel_size_x
    py0 = (qy1 - jgw.top_left_y) / jgw.pixel_size_y  # qy1=north, ps_y<0
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
    crop_jgw = JGWParams(
        pixel_size_x=jgw.pixel_size_x, rotation_x=0, rotation_y=0,
        pixel_size_y=jgw.pixel_size_y,
        top_left_x=jgw.top_left_x + px0i * jgw.pixel_size_x,
        top_left_y=jgw.top_left_y + py0i * jgw.pixel_size_y)
    return crop, crop_jgw


def merge_admin(admin_code, warped_dir, pdf_main_dir, out_dir,
                n_rows=2, n_cols=2):
    """단일 행정코드 병합."""
    result = {'admin_code': admin_code, 'status': 'OK',
              'message': '', 'sheets': []}
    pdf_jpg = os.path.join(pdf_main_dir, f'{admin_code}.jpg')
    pdf_jgw_path = os.path.join(pdf_main_dir, f'{admin_code}.jgw')
    if not os.path.exists(pdf_jpg) or not os.path.exists(pdf_jgw_path):
        result.update(status='ERROR', message='PDF 메인 없음')
        return result

    map_bbox = main_map_world_bbox(pdf_jpg, pdf_jgw_path)

    # warped/{code}/*/warped_scan.jpg 수집
    sheet_dir_glob = os.path.join(warped_dir, admin_code, '*', 'warped_scan.jpg')
    sheet_jpgs = sorted(glob.glob(sheet_dir_glob))
    if not sheet_jpgs:
        result.update(status='ERROR', message='워핑 시트 없음')
        return result

    sheets = []
    for sj in sheet_jpgs:
        sid = os.path.basename(os.path.dirname(sj))
        jgw_path = os.path.splitext(sj)[0] + '.jgw'
        if not os.path.exists(jgw_path):
            continue
        img = cv2.imread(sj)
        jgw = parse_jgw(jgw_path)
        h, w = img.shape[:2]
        cx = jgw.top_left_x + (w / 2) * jgw.pixel_size_x
        cy = jgw.top_left_y + (h / 2) * jgw.pixel_size_y
        sheets.append((sid, img, jgw, cx, cy))

    assignment = assign_quadrants(
        [(s[0], s[3], s[4]) for s in sheets], map_bbox, n_rows, n_cols)

    # 충돌 검사
    occ = {}
    for sid, _, _, _, _ in sheets:
        rc = assignment[sid]
        occ.setdefault(rc, []).append(sid)
    duplicates = {rc: ids for rc, ids in occ.items() if len(ids) > 1}
    if duplicates:
        result['warnings'] = f'사분면 중복 배치: {duplicates}'

    # 캔버스 = 메인 지도영역 전체, ps는 시트의 ps 사용
    target_ps = abs(sheets[0][2].pixel_size_x)
    minx, miny, maxx, maxy = map_bbox
    cw = int(round((maxx - minx) / target_ps))
    ch = int(round((maxy - miny) / target_ps))
    canvas = np.full((ch, cw, 3), 255, np.uint8)

    sheet_meta = []
    for sid, img, jgw, cx, cy in sheets:
        r, c = assignment[sid]
        qb = quadrant_bbox(r, c, map_bbox, n_rows, n_cols)
        crop, cj = crop_to_world_bbox(img, jgw, qb)
        if crop is None:
            sheet_meta.append({'sheet': sid, 'quadrant': [r, c],
                               'status': 'EMPTY_CROP'})
            continue

        # 캔버스 배치
        cx_px = int(round((cj.top_left_x - minx) / target_ps))
        cy_px = int(round((maxy - cj.top_left_y) / target_ps))
        ph, pw = crop.shape[:2]
        y1, y2 = max(0, cy_px), min(ch, cy_px + ph)
        x1, x2 = max(0, cx_px), min(cw, cx_px + pw)
        ty1, ty2 = y1 - cy_px, y2 - cy_px
        tx1, tx2 = x1 - cx_px, x2 - cx_px
        canvas[y1:y2, x1:x2] = crop[ty1:ty2, tx1:tx2]
        sheet_meta.append({'sheet': sid, 'quadrant': [r, c],
                           'crop_size': [pw, ph], 'status': 'OK'})

    result['sheets'] = sheet_meta
    result['canvas_size'] = [cw, ch]
    result['n_rows'] = n_rows
    result['n_cols'] = n_cols

    # 저장
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
    result['output'] = out_jpg
    return result


def main():
    ap = argparse.ArgumentParser(description='Stage 4: 사분면 병합')
    ap.add_argument('--warped', required=True, help='Stage 3 산출 폴더')
    ap.add_argument('--pdf-main', required=True, help='Stage 1 산출 폴더')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--rows', type=int, default=2)
    ap.add_argument('--cols', type=int, default=2)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    # warped/ 하위의 행정코드 폴더 스캔
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
                r = merge_admin(
                    code, args.warped, args.pdf_main, args.out_dir,
                    n_rows=args.rows, n_cols=args.cols)
                cs = r.get('canvas_size', [0, 0])
                w.writerow([code, r['status'], len(r.get('sheets', [])),
                            cs[0], cs[1], r.get('message', ''),
                            r.get('output', ''), f'{time.time()-t0:.1f}'])
                if r['status'] == 'OK':
                    n_ok += 1
                else:
                    n_fail += 1
                # per-admin status.json
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
