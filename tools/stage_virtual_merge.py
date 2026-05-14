"""가상 메인 georef 병합 (PDF-less).

전제:
  - 분할 스캔 N장의 합성(merged) 이미지 중심 = admin 폴리곤 중심
  - 그리드 레이아웃: N=4→2x2, N=9→3x3 (그 외는 일부 누락 케이스)
  - sheet_id 'N-i' 순서: row-major top-down, 1-indexed
    (i=1 좌상, i=cols 우상, i=N 우하)

처리:
  - stage_extract_map 산출 body crop 들을 admin 별로 그룹
  - 누락 시트는 흰 공간
  - body 평균 사이즈로 통일 → cols×rows 그리드 합성 (--tile-gap px 간격)
  - ps 결정 우선순위:
      (1) --ps 명시 (수동)
      (2) --auto-scale: scan 헤더에서 "1:K" OCR → ps = paper_w/1000 × K / scan_w_px
      (3) admin bbox / canvas 로 anisotropic 산출 (폴백)
  - JGW + bbox SHP 산출, 중심 모드 centroid|bbox

CLI:
  # PDF-less 표준 흐름 (auto-scale)
  python -m gis_scan_tools.tools.stage_virtual_merge \\
      --in stage_extract_out/ --shp data/bnd_adm_pg.shp \\
      --out merged_virtual/ --auto-scale \\
      --extract-csv stage_extract_out/_status.csv
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys

import cv2
import numpy as np


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from .common import (
        write_jgw, JGWParams, PRJ_5179,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )
except ImportError:
    from gis_scan_tools.tools.common import (
        write_jgw, JGWParams, PRJ_5179,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )

# 한국 분할도 인쇄 표준 폭 mm. 명목 SP A0 (920) 보다 5mm 큰 925 가 실측 최적
# (사용자 검증: 화순읍 1:5566, scan_w=10800px → ps=0.4765 ↔ 925mm 일치)
DEFAULT_PAPER_W_MM = 925


def ocr_scale_from_scan(scan_path, debug_dir=None):
    """원본 스캔 헤더의 '출력 축척 1:K' 셀 OCR → K (정수) 반환.

    헤더 좌측 25-40% 폭, 위 2.5-6% 높이 영역 crop → grayscale + threshold(180)
    → tesseract psm=6 kor+eng → regex r'1[:.]?(\\d{1,2},?\\d{3,4})' 매칭.
    실패 시 None.
    """
    img = _imread(scan_path)
    if img is None:
        return None
    H, W = img.shape[:2]
    crop = img[int(H * 0.025):int(H * 0.060), int(W * 0.30):int(W * 0.40)]
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(g, 180, 255, cv2.THRESH_BINARY)
    tmp = os.path.join('/tmp', f'_scale_{os.getpid()}.jpg')
    _imwrite(tmp, bw)
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        _imwrite(os.path.join(debug_dir,
                               os.path.splitext(os.path.basename(scan_path))[0]
                               + '_scale.jpg'),
                 bw)
    try:
        res = subprocess.run(
            ['tesseract', tmp, '-', '--psm', '6', '-l', 'kor+eng'],
            capture_output=True, text=True, timeout=15)
        text = res.stdout.replace(' ', '').replace('\n', ' ')
        m = re.search(r'1[:.]?(\d{1,2},?\d{3,4})', text.replace(',', ''))
        if m:
            return int(m.group(1))
    except Exception:
        pass
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return None


def parse_extract_csv(csv_path):
    """stage_extract_map _status.csv → {admin_code: scan_path} (각 admin 1장).

    OCR scale 추출용 — admin 당 한 시트면 충분 (모든 시트 동일 K 가정).
    """
    by_admin = {}
    with open(csv_path, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            ac = row.get('admin_code')
            sp = row.get('scan_path')
            if ac and sp and ac not in by_admin:
                by_admin[ac] = sp
    return by_admin


FILENAME_PAT = re.compile(r'^(\d{8})_(\d+)-(\d+)\.(jpg|jpeg|png)$', re.I)


def discover_bodies(in_dir):
    """재귀 스캔 → {admin_code: {(N, i): path}}"""
    by_admin = {}
    for root, _, files in os.walk(in_dir):
        for f in sorted(files):
            m = FILENAME_PAT.match(f)
            if not m:
                continue
            admin = m.group(1)
            N = int(m.group(2))
            i = int(m.group(3))
            by_admin.setdefault(admin, {})[(N, i)] = os.path.join(root, f)
    return by_admin


def grid_for_n(n_split):
    """N → (rows, cols). 4→2x2, 9→3x3. 그 외는 None."""
    return {4: (2, 2), 9: (3, 3)}.get(n_split)


def _save_canvas_bbox_shp(out_path, admin_code, center_mode, world_bbox):
    """canvas world bbox 를 폴리곤 SHP 으로 저장 (시각 비교용)."""
    try:
        import geopandas as gpd
        from shapely.geometry import box
        minx, miny, maxx, maxy = world_bbox
        gdf = gpd.GeoDataFrame(
            {'admin_code': [admin_code], 'mode': [center_mode]},
            geometry=[box(minx, miny, maxx, maxy)],
            crs='EPSG:5179')
        gdf.to_file(out_path, encoding='cp949')
        return True
    except Exception as e:
        print(f'  [bbox SHP 실패] {e}')
        return False


def merge_admin_virtual(admin_code, sheets_dict, admin_geom, center_mode,
                         out_dir, ps=None, tile_gap=3):
    """admin 1개의 가상 메인 georef 합성.

    Args:
        admin_code: 8자리
        sheets_dict: {(N, i): jpg_path}
        admin_geom: shapely 폴리곤 (world coord)
        center_mode: 'centroid' or 'bbox'
        out_dir: 출력 루트
        ps: isotropic m/px. None 이면 admin bbox / canvas 로 anisotropic 산출
            (시트가 admin 보다 큰 영역 커버하면 ps 명시 권장. 1:5000 at 300DPI
            → ps≈0.4233)
    """
    Ns = set(k[0] for k in sheets_dict)
    if len(Ns) != 1:
        return {'status': 'ERROR', 'message': f'혼합 N: {Ns}'}
    N = Ns.pop()
    grid = grid_for_n(N)
    if grid is None:
        return {'status': 'ERROR', 'message': f'미지원 N={N} (4/9 만)'}
    rows, cols = grid

    bodies = {}
    for k, p in sheets_dict.items():
        img = _imread(p)
        if img is not None:
            bodies[k] = img
    if not bodies:
        return {'status': 'ERROR', 'message': 'body load 실패'}

    h_avg = int(round(np.mean([b.shape[0] for b in bodies.values()])))
    w_avg = int(round(np.mean([b.shape[1] for b in bodies.values()])))

    canvas_w = cols * w_avg + (cols - 1) * tile_gap
    canvas_h = rows * h_avg + (rows - 1) * tile_gap
    canvas = np.full((canvas_h, canvas_w, 3), 255, np.uint8)

    placed, missing = [], []
    for i in range(1, N + 1):
        if (N, i) not in bodies:
            missing.append(i)
            continue
        b = bodies[(N, i)]
        if b.shape[:2] != (h_avg, w_avg):
            b = cv2.resize(b, (w_avg, h_avg), interpolation=cv2.INTER_AREA)
        row = (i - 1) // cols
        col = (i - 1) % cols
        y0 = row * (h_avg + tile_gap)
        x0 = col * (w_avg + tile_gap)
        canvas[y0:y0 + h_avg, x0:x0 + w_avg] = b
        placed.append(i)

    bnd = admin_geom.bounds
    if center_mode == 'centroid':
        cx, cy = admin_geom.centroid.x, admin_geom.centroid.y
    elif center_mode == 'bbox':
        cx = (bnd[0] + bnd[2]) / 2
        cy = (bnd[1] + bnd[3]) / 2
    else:
        return {'status': 'ERROR', 'message': f'unknown center_mode={center_mode}'}

    if ps is not None:
        ps_x = ps_y = float(ps)
    else:
        # Anisotropic — admin bbox 를 canvas 가 정확히 덮도록 (시트 ↔ admin 비례 가정)
        ps_x = (bnd[2] - bnd[0]) / canvas_w
        ps_y = (bnd[3] - bnd[1]) / canvas_h
    top_left_x = cx - canvas_w / 2 * ps_x
    top_left_y = cy + canvas_h / 2 * ps_y
    world_bbox = (top_left_x, top_left_y - canvas_h * ps_y,
                  top_left_x + canvas_w * ps_x, top_left_y)

    sub_out = os.path.join(out_dir, center_mode,
                            admin_code[:2], admin_code[:5])
    os.makedirs(sub_out, exist_ok=True)
    base = f'{admin_code}_virtual_merged'
    jpg_p = os.path.join(sub_out, f'{base}.jpg')
    jgw_p = os.path.join(sub_out, f'{base}.jgw')
    prj_p = os.path.join(sub_out, f'{base}.prj')
    shp_p = os.path.join(sub_out, f'{base}_bbox.shp')

    _imwrite(jpg_p, canvas, [cv2.IMWRITE_JPEG_QUALITY, 88])
    write_jgw(jgw_p, JGWParams(
        pixel_size_x=ps_x, rotation_x=0.0, rotation_y=0.0,
        pixel_size_y=-ps_y,
        top_left_x=top_left_x, top_left_y=top_left_y))
    with open(prj_p, 'w') as f:
        f.write(PRJ_5179)
    _save_canvas_bbox_shp(shp_p, admin_code, center_mode, world_bbox)

    return {
        'status': 'OK', 'admin_code': admin_code,
        'center_mode': center_mode, 'N': N, 'grid': [rows, cols],
        'placed': placed, 'missing': missing,
        'canvas_size': [canvas_w, canvas_h],
        'pixel_size': [ps_x, ps_y],
        'world_bbox': list(world_bbox),
        'output': jpg_p,
    }


def main():
    ap = argparse.ArgumentParser(
        description='가상 메인 georef 병합 (PDF-less)')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='stage_extract_map 산출 폴더 (body crops)')
    ap.add_argument('--shp', required=True, help='admin SHP (bnd_adm_pg.shp)')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--centers', default='centroid,bbox',
                    help='쉼표 구분 모드 (centroid|bbox)')
    ap.add_argument('--ps', type=float, default=None,
                    help='isotropic m/px (수동 지정). 미지정 + auto-scale 미지정 '
                         '시 admin bbox/canvas 로 anisotropic 산출 (폴백)')
    ap.add_argument('--auto-scale', action='store_true',
                    help='scan 헤더 OCR 로 1:K 추출 → ps 자동 산출. '
                         '--extract-csv 필수')
    ap.add_argument('--extract-csv', default=None,
                    help='stage_extract_map _status.csv (auto-scale 시 필수, '
                         'scan_path 컬럼 사용)')
    ap.add_argument('--paper-w', type=float, default=DEFAULT_PAPER_W_MM,
                    help=f'paper 폭 mm (기본 {DEFAULT_PAPER_W_MM} = 한국 분할도 실측)')
    ap.add_argument('--tile-gap', type=int, default=3,
                    help='타일 사이 흰 픽셀 간격 (기본 3)')
    args = ap.parse_args()

    if args.auto_scale and not args.extract_csv:
        print('ERROR: --auto-scale 은 --extract-csv 필수')
        sys.exit(1)
    extract_map = parse_extract_csv(args.extract_csv) if args.extract_csv else {}

    centers = [c.strip() for c in args.centers.split(',') if c.strip()]
    os.makedirs(args.out_dir, exist_ok=True)

    print(f'[가상 병합] SHP 로드: {args.shp}')
    import geopandas as gpd
    from shapely.geometry import MultiPolygon
    gdf_shp = gpd.read_file(args.shp, encoding='cp949')

    by_admin = discover_bodies(args.in_dir)
    n_total = sum(len(v) for v in by_admin.values())
    print(f'  → {len(by_admin)}개 admin, {n_total}장 body')

    csv_path = os.path.join(args.out_dir, '_status.csv')
    rows_csv = []
    for admin_code, sheets in sorted(by_admin.items()):
        row = gdf_shp[gdf_shp['adm_cd'].astype(str) == admin_code]
        if len(row) == 0:
            print(f'  {admin_code}: SHP 에 없음 — skip')
            rows_csv.append([admin_code, '-', 'SKIP', '', '', '', '', '', '',
                              '', '', 'SHP에 admin_code 없음'])
            continue
        geom = row.iloc[0].geometry
        if isinstance(geom, MultiPolygon):
            geom = max(geom.geoms, key=lambda g: g.area)
        # ps 우선순위: --ps 명시 > --auto-scale OCR > 폴백 anisotropic
        ps_admin = args.ps
        if ps_admin is None and args.auto_scale:
            scan_p = extract_map.get(admin_code)
            if scan_p and os.path.exists(scan_p):
                K = ocr_scale_from_scan(scan_p, debug_dir=os.path.join(
                    args.out_dir, '_scale_ocr_debug'))
                if K:
                    scan_w = _imread(scan_p).shape[1]
                    ps_admin = (args.paper_w / 1000.0) * K / scan_w
                    print(f'  {admin_code} [auto-scale] K=1:{K}, '
                          f'scan_w={scan_w}px, paper={args.paper_w}mm '
                          f'→ ps={ps_admin:.4f}')
                else:
                    print(f'  {admin_code} [auto-scale] OCR 실패 → 폴백')
            else:
                print(f'  {admin_code} [auto-scale] scan_path 없음 → 폴백')

        for mode in centers:
            r = merge_admin_virtual(admin_code, sheets, geom, mode,
                                     args.out_dir, ps=ps_admin,
                                     tile_gap=args.tile_gap)
            cs = r.get('canvas_size', ['', ''])
            ps = r.get('pixel_size', ['', ''])
            rows_csv.append([
                admin_code, mode, r['status'], r.get('N', ''),
                str(r.get('placed', '')), str(r.get('missing', '')),
                cs[0], cs[1], f'{ps[0]:.4f}' if ps[0] else '',
                f'{ps[1]:.4f}' if ps[1] else '',
                r.get('output', ''), r.get('message', ''),
            ])
            print(f'  {admin_code} [{mode}] {r["status"]} '
                  f'placed={r.get("placed", [])}, missing={r.get("missing", [])}, '
                  f'ps=({ps[0]:.3f}, {ps[1]:.3f})' if ps[0] else f'  {admin_code} [{mode}] {r["status"]}')

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['admin_code', 'center_mode', 'status', 'N', 'placed',
                    'missing', 'canvas_w', 'canvas_h', 'ps_x', 'ps_y',
                    'output', 'message'])
        for r in rows_csv:
            w.writerow(r)
    print(f'\n[가상 병합] 완료: {csv_path}')


if __name__ == '__main__':
    main()
