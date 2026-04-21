"""Stage 1: PDF 메인 좌표 생성 — PDF 메타(축척+그리드) 우선, SIFT/Powell 폴백.

PDF 메타 정합:
  · PDF 텍스트의 축척(1:N) → 픽셀 크기 ps = 0.0254 × N / DPI
  · sheet 그리드 외곽 중심 → admin bbox center 정렬 → TL world 좌표
  · 정확도 ≤4m, 즉시 (~10ms), 다도해/복잡 형상 admin도 회수

검증 (제주 12 admin):
  · 10개 OK admin: SIFT 결과와 0~4m 일치
  · 2개 실패 admin (39010320 추자면, 39020110): SIFT 정합 실패 → PDF 메타로 자동 회수

출력 포맷: GeoTIFF (LZW 압축 + 타일 + EPSG:5179 + affine 내장)
  → QGIS에서 환경변수 없이 바로 열림 (libjpeg 524MB 제약 우회)
  → .jgw 사이드카도 함께 작성 (downstream 호환)

CLI:
  python -m gis_scan_tools.tools.stage1_pdf_georef \\
      --in pdf_main/ --shp bnd_adm_pg.shp --out pdf_main_geo/

산출: pdf_main_geo/{code}.{tif,jgw,prj} + _status.csv
폴백 시 _gcp.vrt, _tps.vrt 추가 (SIFT/Powell 경로, .jpg 출력)
"""
import argparse
import csv
import glob
import os
import re
import sys
import time
from collections import Counter

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from ._legacy.shp_georeferencer import SHPGeoreferencer
    from ._legacy.common import (
        write_jgw, JGWParams, PRJ_5179,
        LABEL_OFFSET_X_PT, LABEL_OFFSET_Y_PT,
    )
except ImportError:
    from gis_scan_tools.tools._legacy.shp_georeferencer import SHPGeoreferencer
    from gis_scan_tools.tools._legacy.common import (
        write_jgw, JGWParams, PRJ_5179,
        LABEL_OFFSET_X_PT, LABEL_OFFSET_Y_PT,
    )
# 200 DPI (기본) — 62 Mpx로 libjpeg 500MB 제약(JPG) 우회.
# Stage 3 SIFT 매칭엔 충분 (분할 PDF와 동일 DPI 사용 시 정합 OK).
# --render-dpi로 300 등으로 조정 가능.
RENDER_DPI = 200


def _extract_pdf_meta(pdf_path):
    """PDF 텍스트에서 admin_code, scale, sheet 그리드 정보 추출.

    Returns dict 또는 None.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    text = page.get_text()
    words = page.get_text("words")
    doc.close()

    # admin_code: 괄호 안 8자리 우선
    cm = re.search(r'\(\s*(\d{8})\s*\)', text)
    if not cm:
        cm = re.search(r'\b(\d{8})\b', text)
    if not cm:
        return None
    admin_code = cm.group(1)

    # 축척 1:N (콤마 허용)
    sm = re.search(r'1\s*:\s*(\d{1,3}(?:,\d{3})+|\d{4,7})', text)
    if not sm:
        return None
    scale = int(sm.group(1).replace(',', ''))

    # 분할수: 라벨 prefix 통계
    prefixes = []
    for w in words:
        m = re.fullmatch(r'(\d+)-\d+', w[4])
        if m:
            prefixes.append(m.group(1))
    if not prefixes:
        return None
    # "1-1", "115-5" 같은 범례 텍스트 제외 위해 most common
    n_split = int(Counter(prefixes).most_common(1)[0][0])

    # sheet 그리드 외곽 (라벨 보정 적용)
    target_re = re.compile(rf'^{n_split}-\d+$')
    labels = {}
    for w in words:
        if target_re.fullmatch(w[4]):
            labels[w[4]] = (w[0], w[1])
    if len(labels) < 2:
        return None
    xs = sorted({round(b[0]) for b in labels.values()})
    ys = sorted({round(b[1]) for b in labels.values()})
    cell_w = min(xs[i + 1] - xs[i] for i in range(len(xs) - 1)) if len(xs) > 1 else 0
    cell_h = min(ys[i + 1] - ys[i] for i in range(len(ys) - 1)) if len(ys) > 1 else 0
    if not cell_w or not cell_h:
        return None
    gx0 = xs[0] + LABEL_OFFSET_X_PT
    gy0 = ys[0] + LABEL_OFFSET_Y_PT
    gx1 = xs[-1] + cell_w + LABEL_OFFSET_X_PT
    gy1 = ys[-1] + cell_h + LABEL_OFFSET_Y_PT

    return {
        'admin_code': admin_code,
        'scale': scale,
        'n_split': n_split,
        'grid_cx_pt': (gx0 + gx1) / 2,
        'grid_cy_pt': (gy0 + gy1) / 2,
    }


def _extract_orange_vector_points(pdf_path, n_split):
    """PDF 주황(행정경계) vector path 중 sheet 그리드 내부에 완전히
    포함된 점들만 수집 (범례/테두리/인덱스박스 제외).

    Returns:
        np.array shape (N, 2) — PDF pt 좌표 또는 None
    """
    import numpy as np
    doc = fitz.open(pdf_path); page = doc[0]
    target_re = re.compile(rf'^{n_split}-\d+$')
    labels = {}
    for w in page.get_text("words"):
        if target_re.fullmatch(w[4]):
            labels[w[4]] = (w[0], w[1])
    if len(labels) < 2:
        doc.close(); return None
    xs = sorted({round(b[0]) for b in labels.values()})
    ys = sorted({round(b[1]) for b in labels.values()})
    cw = min(xs[i+1]-xs[i] for i in range(len(xs)-1)) if len(xs) > 1 else 0
    ch = min(ys[i+1]-ys[i] for i in range(len(ys)-1)) if len(ys) > 1 else 0
    gx0, gy0 = xs[0], ys[0]
    gx1, gy1 = xs[-1] + cw, ys[-1] + ch

    pts = []
    for d in page.get_drawings():
        c = d.get('color')
        if not c or not (c[0] > 0.7 and 0.3 < c[1] < 0.7 and c[2] < 0.3):
            continue
        # 모든 점이 그리드 내부에 있어야 채택 (범례·테두리 필터)
        ok = True
        for item in d.get('items', []):
            for pt in item[1:]:
                if hasattr(pt, 'x'):
                    if not (gx0 <= pt.x <= gx1 and gy0 <= pt.y <= gy1):
                        ok = False; break
            if not ok: break
        if not ok:
            continue
        for item in d.get('items', []):
            for pt in item[1:]:
                if hasattr(pt, 'x'):
                    pts.append((pt.x, pt.y))
    doc.close()
    return np.array(pts, dtype=np.float64) if pts else None


def _sample_shp_aoi_points(gdf, admin_code, buffer_ratio=0.3, n_points=3000):
    """admin + 인접 admin 경계선 균등 샘플링.

    PDF 주황 vector가 중심 admin + 인접 admin 모두 포함하므로
    SHP도 같은 범위를 샘플링해야 chamfer distance가 의미있음.
    """
    import numpy as np
    from shapely.geometry import MultiPolygon, box as _box
    central = gdf[gdf['adm_cd'].astype(str) == admin_code]
    if central.empty:
        return None
    c_geom = central.iloc[0].geometry
    cx0, cy0, cx1, cy1 = c_geom.bounds
    bx = (cx1-cx0) * buffer_ratio; by = (cy1-cy0) * buffer_ratio
    aoi_box = _box(cx0-bx, cy0-by, cx1+bx, cy1+by)
    cands = gdf[gdf.geometry.intersects(aoi_box)]
    boundaries = []
    for _, row in cands.iterrows():
        geom = row.geometry
        polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
        for p in polys:
            clip = p.boundary.intersection(aoi_box)
            if not clip.is_empty:
                boundaries.append(clip)
    total_len = sum(b.length for b in boundaries
                    if hasattr(b, 'length') and b.length > 0)
    if total_len == 0:
        return None
    pts = []
    for b in boundaries:
        if not hasattr(b, 'length') or b.length == 0:
            continue
        n = max(5, int(n_points * b.length / total_len))
        if b.geom_type == 'LineString':
            dists = np.linspace(0, b.length, n)
            pts.extend([[p.x, p.y] for p in (b.interpolate(d) for d in dists)])
        elif b.geom_type == 'MultiLineString':
            for line in b.geoms:
                nl = max(3, int(n * line.length / max(b.length, 1)))
                dists = np.linspace(0, line.length, nl)
                pts.extend([[p.x, p.y] for p in (line.interpolate(d) for d in dists)])
    return np.array(pts, dtype=np.float64) if pts else None


def _powell_refine_tl(pdf_pts_pt, shp_pts_world, init_tl_x, init_tl_y, ps_mpt,
                      max_shift_m=80.0):
    """Powell minimize — PDF 주황 vector ↔ SHP 경계 chamfer distance 최소화.

    variables: (tl_x, tl_y). ps는 PDF 축척 텍스트 정확성 신뢰 → 고정.
    truncated mean (상위 20% 제외) 으로 아웃라이어 강건.
    이동량 > max_shift_m 이면 초기값 유지 (오수렴 방지).

    Returns:
        (tl_x, tl_y, cost, niter, accepted)
    """
    import numpy as np
    from scipy.optimize import minimize
    from scipy.spatial import cKDTree
    shp_tree = cKDTree(shp_pts_world)

    def objective(params):
        tl_x, tl_y = params
        wx = tl_x + pdf_pts_pt[:, 0] * ps_mpt
        wy = tl_y - pdf_pts_pt[:, 1] * ps_mpt
        pdf_world = np.column_stack([wx, wy])
        d, _ = shp_tree.query(pdf_world, k=1)
        # truncated mean 80% (아웃라이어 20% 제외)
        trim = max(1, int(len(d) * 0.8))
        return float(np.mean(np.sort(d)[:trim]))

    initial_cost = objective([init_tl_x, init_tl_y])
    try:
        res = minimize(objective, [init_tl_x, init_tl_y], method='Powell',
                       options={'xtol': 0.01, 'ftol': 0.01, 'maxiter': 50})
        nx, ny = float(res.x[0]), float(res.x[1])
        final_cost = float(res.fun)
        niter = int(res.nit)
    except Exception:
        return init_tl_x, init_tl_y, initial_cost, 0, False

    moved = ((nx - init_tl_x)**2 + (ny - init_tl_y)**2) ** 0.5
    if moved > max_shift_m or final_cost > initial_cost:
        return init_tl_x, init_tl_y, initial_cost, niter, False
    return nx, ny, final_cost, niter, True


def _render_pdf_to_geotiff(pdf_path, out_tif, tl_x, tl_y, ps,
                            dpi=RENDER_DPI, crs='EPSG:5179'):
    """PDF 1페이지 → GeoTIFF (LZW 압축 + 타일 + affine 내장).

    QGIS/GDAL 네이티브 라스터 포맷. libjpeg 500MB 디코드 제약 없음.
    """
    import numpy as np
    import rasterio
    from rasterio.transform import Affine

    doc = fitz.open(pdf_path)
    pix = doc[0].get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]   # alpha 제거
    elif pix.n == 1:
        arr = np.repeat(arr, 3, axis=2)   # gray → RGB
    w, h = pix.width, pix.height
    doc.close()

    transform = Affine(ps, 0, tl_x, 0, -ps, tl_y)
    with rasterio.open(
        out_tif, 'w', driver='GTiff',
        height=h, width=w, count=3, dtype='uint8',
        crs=crs, transform=transform,
        compress='LZW', tiled=True, blockxsize=256, blockysize=256,
        photometric='RGB', predictor=2,
        bigtiff='IF_SAFER',
    ) as dst:
        dst.write(np.moveaxis(arr, -1, 0))   # H,W,3 → 3,H,W
    return w, h


def georef_from_pdf_meta(pdf_path, gdf, out_dir, base_name=None,
                          dpi=RENDER_DPI, refine=True):
    """PDF 메타(축척+그리드) + SHP admin bbox로 즉시 JGW 생성.

    Returns:
        result dict (admin_code, pixel_size, cost=0, method='pdf_meta')
        또는 None (메타 추출 실패 / SHP에 admin 없음)
    """
    meta = _extract_pdf_meta(pdf_path)
    if meta is None:
        return None
    code = meta['admin_code']

    g = gdf[gdf['adm_cd'].astype(str) == code]
    if g.empty:
        return None
    geom = g.iloc[0].geometry
    swx0, swy0, swx1, swy1 = geom.bounds
    bcx, bcy = (swx0 + swx1) / 2, (swy0 + swy1) / 2

    ps = 0.0254 * meta['scale'] / dpi

    # TL: 그리드 center 픽셀이 admin bbox center world에 align (초기값)
    px_per_pt = dpi / 72.0
    gcx_px = meta['grid_cx_pt'] * px_per_pt
    gcy_px = meta['grid_cy_pt'] * px_per_pt
    tl_x = bcx - gcx_px * ps
    tl_y = bcy + gcy_px * ps

    # Powell refinement — PDF 주황 vector ↔ SHP 경계 chamfer 최소화
    refine_info = {}
    if refine:
        pdf_pts = _extract_orange_vector_points(pdf_path, meta['n_split'])
        shp_pts = _sample_shp_aoi_points(gdf, code)
        if pdf_pts is not None and shp_pts is not None \
                and len(pdf_pts) >= 100 and len(shp_pts) >= 100:
            ps_mpt = ps * px_per_pt   # PDF pt → world 미터
            new_tl_x, new_tl_y, cost, niter, accepted = _powell_refine_tl(
                pdf_pts, shp_pts, tl_x, tl_y, ps_mpt)
            moved = ((new_tl_x - tl_x) ** 2 + (new_tl_y - tl_y) ** 2) ** 0.5
            refine_info = dict(cost=round(cost, 2), niter=niter,
                                moved_m=round(moved, 2),
                                accepted=accepted,
                                n_pdf_pts=len(pdf_pts),
                                n_shp_pts=len(shp_pts))
            if accepted:
                tl_x, tl_y = new_tl_x, new_tl_y

    # PDF → GeoTIFF 렌더링 (affine 내장, QGIS 네이티브)
    base = base_name or code
    out_tif = os.path.join(out_dir, f'{base}.tif')
    img_w, _ = _render_pdf_to_geotiff(
        pdf_path, out_tif, tl_x, tl_y, ps, dpi=dpi)

    # .jgw + .prj 사이드카 — downstream 코드와 구버전 호환
    out_jgw = os.path.join(out_dir, f'{base}.jgw')
    out_prj = os.path.join(out_dir, f'{base}.prj')
    write_jgw(out_jgw, JGWParams(
        pixel_size_x=ps, rotation_x=0.0, rotation_y=0.0,
        pixel_size_y=-ps, top_left_x=tl_x, top_left_y=tl_y))
    with open(out_prj, 'w') as f:
        f.write(PRJ_5179)

    return {
        'admin_code': code,
        'cost': refine_info.get('cost', 0.0),
        'pixel_size': ps,
        'gcp_count': 0,
        'scale': meta['scale'],
        'method': 'pdf_meta+powell' if refine_info.get('accepted') else 'pdf_meta',
        'refine': refine_info,
    }


def main():
    ap = argparse.ArgumentParser(description='Stage 1: PDF 메인 좌표 생성')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='메인 PDF/JPG 폴더 (재귀)')
    ap.add_argument('--shp', required=True, help='행정경계 SHP')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--cost-threshold', type=float, default=30.0,
                    help='WARN 판정 cost 임계 — PDF 메타+Powell은 chamfer '
                         'distance(m). 30m 미만이면 OK (기본)')
    ap.add_argument('--no-pdf-meta', action='store_true',
                    help='PDF 메타 기반 정합 비활성화 (SIFT/Powell만 사용)')
    ap.add_argument('--render-dpi', type=int, default=RENDER_DPI,
                    help=f'PDF 렌더링 DPI (기본 {RENDER_DPI}). '
                         f'200: QGIS JPG 호환, 300: 고해상도')
    ap.add_argument('--no-refine', action='store_true',
                    help='Powell refinement 비활성화 (PDF 메타 초기값만 사용)')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    inputs = []
    for ext in ('*.pdf', '*.jpg', '*.jpeg', '*.tif', '*.tiff'):
        inputs += glob.glob(os.path.join(args.in_dir, '**', ext), recursive=True)
    inputs = sorted(set(
        p for p in inputs
        if 'checkpoint' not in p
        and re.match(r'^\d{8}\.', os.path.basename(p))
    ))
    print(f'[Stage 1] 메인 PDF/이미지 {len(inputs)}개 처리 시작 (분할 _N-i 제외)')

    # PDF 메타용 SHP (geopandas)
    gdf = None
    if not args.no_pdf_meta:
        try:
            import geopandas as gpd
            try:
                gdf = gpd.read_file(args.shp, encoding='cp949')
            except Exception:
                gdf = gpd.read_file(args.shp)
            print(f'  [SHP 로드] {len(gdf)}개 행정구역 (PDF 메타 정합용)')
        except Exception as e:
            print(f'  [SHP 로드 실패→PDF 메타 비활성] {e}')
            gdf = None

    # SIFT/Powell 폴백 (메타 추출 실패 시 또는 .jpg/.tif 입력)
    g_sift = SHPGeoreferencer(args.shp)

    csv_path = os.path.join(args.out_dir, '_status.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['input', 'status', 'admin_code', 'cost_px',
                    'gcp_count', 'pixel_size', 'method',
                    'message', 'elapsed_s'])

        n_ok = n_fail = 0
        n_meta = n_sift = 0
        for i, src in enumerate(inputs, 1):
            t0 = time.time()
            print(f'\n[{i}/{len(inputs)}] {os.path.basename(src)}')

            r = None
            method = ''
            err_meta = ''

            # 1순위: PDF 메타 (PDF 입력만)
            if gdf is not None and src.lower().endswith('.pdf'):
                try:
                    r = georef_from_pdf_meta(src, gdf, args.out_dir,
                                              dpi=args.render_dpi,
                                              refine=not args.no_refine)
                    if r:
                        method = r.get('method', 'pdf_meta')
                        n_meta += 1
                        msg = (f'  → {method}: 1:{r["scale"]:,}, '
                               f'ps={r["pixel_size"]:.4f} m/px '
                               f'({(time.time()-t0)*1000:.0f}ms)')
                        rinfo = r.get('refine', {})
                        if rinfo:
                            msg += (f' | refine cost={rinfo["cost"]:.2f}m '
                                    f'moved={rinfo["moved_m"]:.1f}m '
                                    f'iter={rinfo["niter"]}')
                            if not rinfo.get('accepted'):
                                msg += ' [REJECTED→초기값]'
                        print(msg)
                    else:
                        err_meta = '메타 추출 실패 (admin_code/scale/grid 누락)'
                except Exception as e:
                    err_meta = f'pdf_meta 예외: {e}'
                    print(f'  [PDF 메타 실패→SIFT 폴백] {e}')

            # 폴백: SIFT/Powell
            if r is None:
                try:
                    r = g_sift.georeference_image(src, args.out_dir)
                    method = 'sift'
                    n_sift += 1
                except Exception as e:
                    msg = f'{err_meta} | sift: {e}' if err_meta else f'sift: {e}'
                    w.writerow([src, 'ERROR', '', '', '', '', '',
                                msg, f'{time.time()-t0:.1f}'])
                    n_fail += 1
                    print(f'  → ERROR: {e}')
                    continue

            cost = float(r.get('cost', 999))
            status = 'OK' if cost <= args.cost_threshold else 'WARN'
            w.writerow([src, status, r.get('admin_code', ''),
                        f'{cost:.3f}', r.get('gcp_count', 0),
                        f'{float(r.get("pixel_size", 0)):.4f}',
                        method, '', f'{time.time()-t0:.1f}'])
            if status == 'OK':
                n_ok += 1
            else:
                n_fail += 1
            print(f'  → {status} cost={cost:.2f}px ({method})')

    print(f'\n[Stage 1] 완료: OK={n_ok}, FAIL/ERROR={n_fail}')
    print(f'  방식 분포: PDF 메타 {n_meta}개 / SIFT {n_sift}개')
    print(f'  결과: {csv_path}')


if __name__ == '__main__':
    main()
