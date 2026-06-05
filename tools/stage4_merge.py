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


def _virtual_merge_admin(admin_code, out_dir, ctx):
    """PDF-less admin → stage_virtual_merge 호출, 결과를 Stage 4 형식으로 정규화."""
    try:
        from .stage_virtual_merge import (
            discover_bodies, merge_admin_virtual, merge_admin_shp_refined,
            ocr_scale_from_scan)
    except ImportError:
        from gis_scan_tools.tools.stage_virtual_merge import (
            discover_bodies, merge_admin_virtual, merge_admin_shp_refined,
            ocr_scale_from_scan)
    from shapely.geometry import MultiPolygon

    by_admin = discover_bodies(ctx['extract_dir'])
    sheets = by_admin.get(admin_code)
    if not sheets:
        return {'admin_code': admin_code, 'status': 'ERROR',
                'message': f'extract_dir 에 {admin_code} body 없음', 'sheets': []}
    row = ctx['shp_gdf'][ctx['shp_gdf']['adm_cd'].astype(str) == admin_code]
    if len(row) == 0:
        return {'admin_code': admin_code, 'status': 'ERROR',
                'message': f'SHP 에 {admin_code} 없음', 'sheets': []}
    geom = row.iloc[0].geometry
    if isinstance(geom, MultiPolygon):
        geom = max(geom.geoms, key=lambda g: g.area)

    ps = None
    if ctx.get('auto_scale'):
        scan_p = ctx.get('extract_csv_map', {}).get(admin_code)
        if scan_p and os.path.exists(scan_p):
            K = ocr_scale_from_scan(scan_p)
            if K:
                scan_w = _imread(scan_p).shape[1]
                ps = (ctx['paper_w_mm'] / 1000.0) * K / scan_w
                print(f'  [virtual auto-scale] K=1:{K}, ps={ps:.4f}')

    # 시트별 SHP 정합(bnd_adm_pg 주황선 매칭) → 월드좌표 모자이크.
    # 실패 시 기존 가상 그리드 합성으로 폴백.
    try:
        r = merge_admin_shp_refined(
            admin_code, sheets, geom, ctx['shp_gdf'],
            out_dir, ps=ps, tile_gap=ctx['tile_gap'],
            flat_layout=True, basename=f'{admin_code}_scan_merged')
        if r.get('status') != 'OK':
            raise RuntimeError(r.get('message', 'shp_refined 실패'))
    except Exception as e:  # noqa: BLE001
        print(f'  [shp_refined 폴백 → virtual grid] {e}')
        r = merge_admin_virtual(
            admin_code, sheets, geom, ctx['center_mode'],
            out_dir, ps=ps, tile_gap=ctx['tile_gap'],
            flat_layout=True, basename=f'{admin_code}_scan_merged')
    # Stage 4 csv 호환 필드
    r.setdefault('canvas_size', r.get('canvas_size', [0, 0]))
    r.setdefault('output', r.get('output', ''))
    if r.get('status') == 'OK':
        r['status'] = 'OK_VIRTUAL'
        r['sheets'] = [{'sheet': str(s), 'status': 'OK'}
                       for s in r.get('placed', [])]
        if 'refined' in r:
            r['message'] = (f"SHP정합 {len(r['refined'])}/"
                            f"{len(r['refined']) + len(r['fell_back'])}장"
                            + (f", 폴백 {r['fell_back']}" if r['fell_back'] else ''))
    return r


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
                out_dir, inner_margin_px=0,
                virtual_ctx=None):
    """단일 행정코드 병합 — 시트별 world bbox로 크롭 후 모자이크.

    PDF-less 케이스 (sheet_bboxes 없음 + pdf_main 없음):
      - virtual_ctx 제공 시: stage_virtual_merge 호출로 가상 georef 합성
      - virtual_ctx None: SKIPPED

    virtual_ctx (dict):
      extract_dir: stage_extract_map 산출 root
      shp_gdf: geopandas DataFrame (admin SHP)
      extract_csv_map: {admin_code: scan_path} (auto-scale OCR 용)
      paper_w_mm, tile_gap, center_mode, auto_scale
    """
    result = {'admin_code': admin_code, 'status': 'OK',
              'message': '', 'sheets': []}

    bboxes = sheet_bboxes.get(admin_code, {})
    pdf_jpg = find_main_image(pdf_main_dir, admin_code) if os.path.isdir(pdf_main_dir or '') else None

    # PDF-less: virtual merge 폴백 (제공된 경우)
    if not bboxes and pdf_jpg is None:
        if virtual_ctx is not None:
            return _virtual_merge_admin(admin_code, out_dir, virtual_ctx)
        result.update(status='SKIPPED',
                       message='PDF 없음 (Stage 3 PASSTHROUGH) — 병합 skip')
        return result

    if pdf_jpg is None:
        result.update(status='ERROR',
                      message=f'PDF 메인 없음: {admin_code}.{{tif,jpg}}')
        return result
    if not bboxes:
        result.update(status='ERROR', message='sheet_bboxes에 admin 없음')
        return result

    pdf_jgw_path = os.path.join(pdf_main_dir, f'{admin_code}.jgw')
    map_bbox = main_map_world_bbox(pdf_jpg, pdf_jgw_path)
    minx, miny, maxx, maxy = map_bbox

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
                    help='Stage 2 산출 sheet_bboxes.json (PDF-less 면 빈 dict)')
    ap.add_argument('--pdf-main', default='',
                    help='Stage 1 산출 폴더. PDF-less 면 미지정 → virtual merge 폴백')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--inner-margin', type=int, default=0,
                    help='시트 안쪽 여유 (px). 시트 경계 부정확 영역 제거. 기본 0')
    # PDF-less virtual merge 폴백 인자 (모두 같이 줘야 활성화)
    ap.add_argument('--extract-dir', default='',
                    help='stage_extract_map 산출 root (PDF-less admin 가상 병합용)')
    ap.add_argument('--shp', default='',
                    help='admin SHP (PDF-less admin 가상 병합용)')
    ap.add_argument('--extract-csv', default='',
                    help='stage_extract_map _status.csv (auto-scale 용 scan_path 매핑)')
    ap.add_argument('--auto-scale', action='store_true',
                    help='scan 헤더 OCR 로 ps 자동 산출 (--extract-csv 필요)')
    ap.add_argument('--paper-w', type=float, default=925,
                    help='paper 폭 mm (auto-scale 용, 기본 925 = 한국 분할도)')
    ap.add_argument('--tile-gap', type=int, default=3,
                    help='가상 병합 시 타일 사이 흰 픽셀 간격')
    ap.add_argument('--center-mode', default='bbox', choices=['bbox', 'centroid'],
                    help='가상 병합 canvas 중심 모드')
    args = ap.parse_args()

    # virtual_ctx — extract_dir + shp 가 있어야 활성화
    virtual_ctx = None
    if args.extract_dir and args.shp and os.path.isdir(args.extract_dir):
        import geopandas as gpd
        print(f'[Stage 4] PDF-less virtual merge 활성화: SHP 로드 {args.shp}')
        gdf = gpd.read_file(args.shp, encoding='cp949')
        extract_csv_map = {}
        if args.extract_csv and os.path.exists(args.extract_csv):
            with open(args.extract_csv, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    ac = row.get('admin_code')
                    sp = row.get('scan_path')
                    if ac and sp and ac not in extract_csv_map:
                        extract_csv_map[ac] = sp
        virtual_ctx = {
            'extract_dir': args.extract_dir,
            'shp_gdf': gdf,
            'extract_csv_map': extract_csv_map,
            'paper_w_mm': args.paper_w,
            'tile_gap': args.tile_gap,
            'center_mode': args.center_mode,
            'auto_scale': args.auto_scale,
        }

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
        n_ok = n_skip = n_fail = 0
        for i, code in enumerate(admin_codes, 1):
            t0 = time.time()
            print(f'\n[{i}/{len(admin_codes)}] {code}')
            try:
                r = merge_admin(code, args.warped, args.pdf_main,
                                sheet_bboxes, args.out_dir,
                                inner_margin_px=args.inner_margin,
                                virtual_ctx=virtual_ctx)
                cs = r.get('canvas_size', [0, 0])
                w.writerow([code, r['status'],
                            sum(1 for s in r.get('sheets', [])
                                if s.get('status') == 'OK'),
                            cs[0], cs[1], r.get('message', ''),
                            r.get('output', ''), f'{time.time()-t0:.1f}'])
                if r['status'] in ('OK', 'OK_VIRTUAL'):
                    n_ok += 1
                elif r['status'] == 'SKIPPED':
                    n_skip += 1
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

    print(f'\n[Stage 4] 완료: OK={n_ok}, SKIPPED={n_skip}, FAIL/ERROR={n_fail}')


if __name__ == '__main__':
    main()
