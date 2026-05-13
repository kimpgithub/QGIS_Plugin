"""Stage 1: PDF 메인 좌표 생성 — GeoTIFF + JGW.

정합 전략 (빠른→느린):
  1) largest_polygon_bbox: PDF 주황 path 중 가장 큰 것 bbox ↔
     SHP admin 가장 큰 polygon bbox. 12 admin 검증 sub-2m.
  2) grid_center:  sheet 그리드 중심 ↔ admin bbox/centroid 정렬 (폴백).
  3) SIFT (`SHPGeoreferencer`): 텍스트 메타 추출 실패 시.

핵심 최적화 (Phase 1j+):
  · PDF 1회 open — text/drawings/pixmap 통합 파싱
  · rasterio band별 write (moveaxis 제거, 메모리 50% 감소)
  · 이전 ICP/Powell 경로 제거 — largest_polygon_bbox가 모든 admin 안정

출력: {code}.tif (GeoTIFF LZW 타일 EPSG:5179 + affine 내장) + .jgw + .prj

CLI:
  python -m gis_scan_tools.tools.stage1_pdf_georef \\
      --in pdf_main/ --shp bnd_adm_pg.shp --out pdf_main_geo/
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
import time
from collections import Counter

import fitz
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from .shp_georeferencer import SHPGeoreferencer
    from .common import (
        write_jgw, JGWParams, PRJ_5179,
        LABEL_OFFSET_X_PT, LABEL_OFFSET_Y_PT,
        extract_map_region,
    )
except ImportError:
    from gis_scan_tools.tools.shp_georeferencer import SHPGeoreferencer
    from gis_scan_tools.tools.common import (
        write_jgw, JGWParams, PRJ_5179,
        LABEL_OFFSET_X_PT, LABEL_OFFSET_Y_PT,
        extract_map_region,
    )


RENDER_DPI = 200   # 62 Mpx — libjpeg 500MB 우회, Stage 3 SIFT에 충분


# ============================================================
# PDF 1회 파싱
# ============================================================

def _parse_pdf(pdf_path, render_dpi=None):
    """PDF 1회 open으로 필요 정보 모두 파싱.

    Returns dict:
        text, words, drawings, page_size_pt: PDF 구조 정보
        pixmap, pixmap_size: render_dpi 지정 시 RGB numpy 배열 (H,W,3)
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    info = dict(
        page_size_pt=(page.rect.width, page.rect.height),
        text=page.get_text(),
        words=page.get_text("words"),
        drawings=page.get_drawings(),
    )
    if render_dpi is not None:
        pix = page.get_pixmap(dpi=render_dpi)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        if pix.n == 4:
            arr = arr[:, :, :3]       # alpha 제거 (view)
        elif pix.n == 1:
            arr = np.repeat(arr, 3, axis=2)   # gray → RGB
        # copy so we can close doc safely (pix.samples is backed by mupdf buf)
        info['pixmap'] = arr.copy()
        info['pixmap_size'] = (pix.width, pix.height)
    doc.close()
    return info


def _extract_meta(words, text):
    """PDF 구조 정보에서 admin_code, scale, n_split, grid 정보 추출."""
    cm = re.search(r'\(\s*(\d{8})\s*\)', text) or re.search(r'\b(\d{8})\b', text)
    if not cm:
        return None
    admin_code = cm.group(1)

    sm = re.search(r'1\s*:\s*(\d{1,3}(?:,\d{3})+|\d{4,7})', text)
    if not sm:
        return None
    scale = int(sm.group(1).replace(',', ''))

    # 분할수 — 라벨 prefix most common
    prefixes = [m.group(1) for w in words
                for m in [re.fullmatch(r'(\d+)-\d+', w[4])] if m]
    if not prefixes:
        return None
    n_split = int(Counter(prefixes).most_common(1)[0][0])

    # sheet 그리드 외곽 (라벨 보정 적용)
    target_re = re.compile(rf'^{n_split}-\d+$')
    labels = {w[4]: (w[0], w[1]) for w in words if target_re.fullmatch(w[4])}
    if len(labels) < 2:
        return None
    xs = sorted({round(b[0]) for b in labels.values()})
    ys = sorted({round(b[1]) for b in labels.values()})
    cell_w = min(xs[i+1] - xs[i] for i in range(len(xs)-1)) if len(xs) > 1 else 0
    cell_h = min(ys[i+1] - ys[i] for i in range(len(ys)-1)) if len(ys) > 1 else 0
    if not cell_w or not cell_h:
        return None
    gx0 = xs[0] + LABEL_OFFSET_X_PT
    gy0 = ys[0] + LABEL_OFFSET_Y_PT
    gx1 = xs[-1] + cell_w + LABEL_OFFSET_X_PT
    gy1 = ys[-1] + cell_h + LABEL_OFFSET_Y_PT

    # 라벨 원본 bbox (보정 전) — 주황 path 필터용
    raw_bbox = (xs[0], ys[0], xs[-1] + cell_w, ys[-1] + cell_h)

    return dict(
        admin_code=admin_code,
        scale=scale,
        n_split=n_split,
        grid_cx_pt=(gx0 + gx1) / 2,
        grid_cy_pt=(gy0 + gy1) / 2,
        grid_bbox_raw=raw_bbox,   # 주황 path 필터용 (라벨 보정 적용 X)
    )


def _extract_orange_paths_in_grid(drawings, grid_bbox_raw):
    """주황색 drawings 중 그리드 내부 전체 포함된 path만 추출.

    Returns:
        list of dicts: [{'pts': (N,2), 'bbox': (x0,y0,x1,y1), 'area': float}, ...]
        area 내림차순 정렬됨.
    """
    gx0, gy0, gx1, gy1 = grid_bbox_raw
    paths = []
    for d in drawings:
        c = d.get('color')
        if not c or not (c[0] > 0.7 and 0.3 < c[1] < 0.7 and c[2] < 0.3):
            continue
        pts = [(pt.x, pt.y) for it in d.get('items', [])
               for pt in it[1:] if hasattr(pt, 'x')]
        if not pts:
            continue
        pts = np.array(pts, dtype=np.float64)
        if not ((pts[:, 0] >= gx0) & (pts[:, 0] <= gx1) &
                (pts[:, 1] >= gy0) & (pts[:, 1] <= gy1)).all():
            continue
        px0, py0 = float(pts[:, 0].min()), float(pts[:, 1].min())
        px1, py1 = float(pts[:, 0].max()), float(pts[:, 1].max())
        paths.append({
            'pts': pts,
            'bbox': (px0, py0, px1, py1),
            'area': (px1 - px0) * (py1 - py0),
        })
    paths.sort(key=lambda p: -p['area'])
    return paths


def _largest_polygon_bbox(geom):
    """SHP geometry에서 가장 큰 polygon의 bbox."""
    from shapely.geometry import MultiPolygon
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    return max(polys, key=lambda p: p.area).bounds


# Vector ICP — 다도해 multi-polygon 정합용 (commit 0944f28 복구)
ICP_MAX_POINTS = 5000           # PDF/SHP 점군 상한 (perf 가드)
ICP_MAX_ITER = 20               # 수렴 최대 반복
ICP_CONV_THRESHOLD = 0.01       # cost 변화 임계 (m)
ICP_MAD_K = 3.0                 # outlier 거부 임계 (median + K*MAD)
ICP_MIN_INLIER_RATIO = 0.3      # 정합 신뢰 임계
ICP_MAX_PS_CHANGE_PCT = 5.0     # ps 변화 안전 임계 (PDF text 매우 정확)


def _sample_shp_polygon_points(geom, max_points=ICP_MAX_POINTS):
    """SHP multi-polygon 의 외곽 + 내부 ring 점들 샘플링.

    다도해 다수 polygon 케이스 — 모든 polygon 의 점을 합쳐 ICP 입력으로 사용.
    max_points 초과 시 균등 random 샘플 (재현 가능 seed).
    """
    from shapely.geometry import MultiPolygon
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    pts = []
    for p in polys:
        pts.extend(p.exterior.coords)
        for h in p.interiors:
            pts.extend(h.coords)
    pts = np.array(pts, dtype=np.float64)
    if len(pts) > max_points:
        idx = np.random.RandomState(42).choice(
            len(pts), max_points, replace=False)
        pts = pts[idx]
    return pts


def _collect_pdf_points(paths, max_points=ICP_MAX_POINTS):
    """주황 path 의 모든 점을 합쳐 ICP 입력으로 사용. 상한 초과 시 random 샘플."""
    if not paths:
        return None
    pts_list = [p['pts'] for p in paths]
    pts = np.vstack(pts_list)
    if len(pts) > max_points:
        idx = np.random.RandomState(42).choice(
            len(pts), max_points, replace=False)
        pts = pts[idx]
    return pts


def _icp_scale_tl(pdf_pts_pt, shp_pts_world, init_ps_mpt,
                   init_tl_x, init_tl_y,
                   max_iter=ICP_MAX_ITER,
                   conv_threshold=ICP_CONV_THRESHOLD,
                   max_shift_m=None):
    """Vector ICP — PDF 주황 점군 ↔ SHP 경계 점군 매칭으로 scale + TL 추정.

    회전 0 가정 (지도 종이는 N-up). 자유도: (ps_mpt, tl_x, tl_y) = 3.
    각 iter:
      1. 현 affine 으로 PDF→world 변환
      2. 각 변환 점에 대해 SHP 에서 nearest 찾기 (cKDTree, O(N log N))
      3. MAD 기반 outlier 거부 (median + K*MAD)
      4. inlier 쌍으로 isotropic scale + translation 재추정 (closed-form)
      5. cost 변화 < threshold 면 수렴

    부분 일치(다도해 등): outlier 거부로 자동 처리. 다수 polygon 점군 사용.

    Perf:
      · cKDTree 빌드 O(N log N), iter 당 query O(M log N)
      · 점군 상한 ICP_MAX_POINTS (5000) 로 최악 시간 < 1s

    Returns:
        (ps, tl_x, tl_y, final_cost, niter, inlier_ratio, accepted,
         ps_change_pct, moved_m)
    """
    from scipy.spatial import cKDTree
    Q_tree = cKDTree(shp_pts_world)
    P = pdf_pts_pt.astype(np.float64)
    ps, tx, ty = init_ps_mpt, init_tl_x, init_tl_y
    prev_cost = float('inf')
    final_cost = float('inf')
    niter = 0
    inlier_ratio = 1.0

    for it in range(max_iter):
        niter = it + 1
        wx = tx + P[:, 0] * ps
        wy = ty - P[:, 1] * ps
        P_world = np.column_stack([wx, wy])

        dists, indices = Q_tree.query(P_world, k=1)
        med = float(np.median(dists))
        mad = float(np.median(np.abs(dists - med)))
        thresh = med + ICP_MAD_K * mad
        mask = dists < thresh
        inlier_ratio = float(mask.sum()) / len(P)
        if inlier_ratio < ICP_MIN_INLIER_RATIO:
            break

        P_in = np.column_stack([P[mask, 0], -P[mask, 1]])  # y 부호 반전
        Q_in = shp_pts_world[indices[mask]]
        P_mean = P_in.mean(axis=0)
        Q_mean = Q_in.mean(axis=0)
        Pc = P_in - P_mean
        Qc = Q_in - Q_mean
        num = float(np.sum(Pc * Qc))
        den = float(np.sum(Pc * Pc))
        if den <= 0:
            break
        new_ps = num / den
        new_tx = Q_mean[0] - new_ps * P_mean[0]
        new_ty = Q_mean[1] - new_ps * P_mean[1]

        ps, tx, ty = new_ps, new_tx, new_ty
        cost = float(np.median(dists[mask]))
        final_cost = cost
        if abs(prev_cost - cost) < conv_threshold:
            break
        prev_cost = cost

    moved = float(np.hypot(tx - init_tl_x, ty - init_tl_y))
    ps_change_pct = abs(ps - init_ps_mpt) / max(init_ps_mpt, 1e-9) * 100

    accepted = True
    if ps_change_pct > ICP_MAX_PS_CHANGE_PCT:
        accepted = False
    if max_shift_m is not None and moved > max_shift_m:
        accepted = False
    if inlier_ratio < ICP_MIN_INLIER_RATIO:
        accepted = False

    return (ps, tx, ty, final_cost, niter, inlier_ratio, accepted,
            ps_change_pct, moved)


# ============================================================
# GeoTIFF 쓰기
# ============================================================

def _write_geotiff(arr, out_tif, tl_x, tl_y, ps, crs='EPSG:5179'):
    """RGB (H,W,3) numpy 배열 → GeoTIFF (LZW + 타일 + affine 내장).

    band별 write로 moveaxis 메모리 복사 회피.
    """
    import rasterio
    from rasterio.transform import Affine
    h, w = arr.shape[:2]
    transform = Affine(ps, 0, tl_x, 0, -ps, tl_y)
    with rasterio.open(
        out_tif, 'w', driver='GTiff',
        height=h, width=w, count=3, dtype='uint8',
        crs=crs, transform=transform,
        compress='LZW', tiled=True, blockxsize=256, blockysize=256,
        photometric='RGB', predictor=2,
        bigtiff='IF_SAFER',
    ) as dst:
        for i in range(3):
            dst.write(arr[:, :, i], i + 1)


# ============================================================
# 메인 정합 함수
# ============================================================

def georef_from_pdf_meta(pdf_path, gdf, out_dir, base_name=None,
                          dpi=RENDER_DPI):
    """PDF 1회 파싱 + largest polygon bbox 매칭 + GeoTIFF.

    Returns dict with admin_code/pixel_size/method, 또는 None (메타 추출 실패).
    """
    # 1. PDF 한 번에 파싱 (text + drawings + pixmap)
    pdf = _parse_pdf(pdf_path, render_dpi=dpi)

    # 2. 메타 추출
    meta = _extract_meta(pdf['words'], pdf['text'])
    if meta is None:
        return None
    code = meta['admin_code']

    # 3. SHP에서 admin 찾기
    g = gdf[gdf['adm_cd'].astype(str) == code]
    if g.empty:
        return None
    geom = g.iloc[0].geometry
    swx0, swy0, swx1, swy1 = geom.bounds

    # 다도해 감지 — fill_ratio 낮으면 centroid 사용
    bbox_area = (swx1 - swx0) * (swy1 - swy0)
    fill_ratio = geom.area / bbox_area if bbox_area > 0 else 1.0
    if fill_ratio < 0.3:
        bcx, bcy = geom.centroid.x, geom.centroid.y
    else:
        bcx, bcy = (swx0 + swx1) / 2, (swy0 + swy1) / 2

    # 4. 주황 path 추출 (한 번)
    paths = _extract_orange_paths_in_grid(pdf['drawings'],
                                           meta['grid_bbox_raw'])

    ps = 0.0254 * meta['scale'] / dpi
    px_per_pt = dpi / 72.0

    # 5. 1순위: 가장 큰 PDF path bbox ↔ SHP 가장 큰 polygon bbox 매칭
    method = None
    method_info = {}
    if paths:
        pdf_bb = paths[0]['bbox']
        shp_bb = _largest_polygon_bbox(geom)
        px0, py0, px1, py1 = pdf_bb
        sx0, sy0, sx1, sy1 = shp_bb
        pdf_w = px1 - px0
        pdf_h = py1 - py0
        if pdf_w > 1 and pdf_h > 1:
            ps_mpt_x = (sx1 - sx0) / pdf_w
            ps_mpt_y = (sy1 - sy0) / pdf_h
            ps_mpt_meas = (ps_mpt_x + ps_mpt_y) / 2
            ps_mpt_text = ps * px_per_pt
            ratio_mismatch_pct = abs(ps_mpt_meas - ps_mpt_text) / \
                ps_mpt_text * 100
            if ratio_mismatch_pct < 5.0:
                ps_mpt = ps_mpt_meas
                tl_x = sx0 - px0 * ps_mpt
                tl_y = sy1 + py0 * ps_mpt
                ps = ps_mpt / px_per_pt
                method = 'largest_polygon_bbox'
                method_info = dict(
                    pdf_path_area=paths[0]['area'],
                    n_paths=len(paths),
                    ratio_mismatch_pct=round(ratio_mismatch_pct, 3),
                )

    # 6. 폴백: 그리드 center ↔ admin bbox/centroid
    if method is None:
        gcx_px = meta['grid_cx_pt'] * px_per_pt
        gcy_px = meta['grid_cy_pt'] * px_per_pt
        tl_x = bcx - gcx_px * ps
        tl_y = bcy + gcy_px * ps
        method = 'grid_center'
        method_info = dict(fill_ratio=round(fill_ratio, 3),
                            reason='largest_polygon_bbox 미적용')

    # 6b. 다도해 (fill_ratio<0.3) — Vector ICP refinement
    #     largest_polygon_bbox 가 ratio_mismatch 통과해도 single-polygon 매칭이라
    #     wrong island 매칭 위험. grid_center 폴백은 centroid 정렬이라 paper
    #     center 와 어긋남. ICP 로 모든 PDF 주황 점 ↔ SHP 모든 polygon 점 정합.
    if fill_ratio < 0.3 and paths:
        pdf_pts_pt = _collect_pdf_points(paths)
        shp_pts_world = _sample_shp_polygon_points(geom)
        if pdf_pts_pt is not None and len(pdf_pts_pt) >= 50 \
                and len(shp_pts_world) >= 50:
            init_ps_mpt = ps * px_per_pt
            # 최대 이동 한계: admin bbox 크기의 30% (centroid 폴백 큰 오차 대응)
            admin_w = swx1 - swx0
            admin_h = swy1 - swy0
            max_shift = max(admin_w, admin_h) * 0.3
            (icp_ps_mpt, icp_tl_x, icp_tl_y, icp_cost, icp_niter,
             icp_inl, icp_ok, icp_ps_change, icp_moved) = _icp_scale_tl(
                pdf_pts_pt, shp_pts_world, init_ps_mpt, tl_x, tl_y,
                max_shift_m=max_shift)
            if icp_ok:
                ps = icp_ps_mpt / px_per_pt
                tl_x = icp_tl_x
                tl_y = icp_tl_y
                method = f'icp_multipoly (prev:{method})'
                method_info.update(
                    icp_cost_m=round(icp_cost, 2),
                    icp_niter=icp_niter,
                    icp_inlier_ratio=round(icp_inl, 3),
                    icp_ps_change_pct=round(icp_ps_change, 3),
                    icp_moved_m=round(icp_moved, 1),
                    icp_n_pdf_pts=int(len(pdf_pts_pt)),
                    icp_n_shp_pts=int(len(shp_pts_world)),
                )
            else:
                method_info.update(
                    icp_rejected=True,
                    icp_inlier_ratio=round(icp_inl, 3),
                    icp_ps_change_pct=round(icp_ps_change, 3),
                    icp_moved_m=round(icp_moved, 1),
                )

    # 7. GeoTIFF 쓰기 (파싱한 pixmap 재사용)
    base = base_name or code
    out_tif = os.path.join(out_dir, f'{base}.tif')
    _write_geotiff(pdf['pixmap'], out_tif, tl_x, tl_y, ps, crs='EPSG:5179')

    # 8. JGW + PRJ 사이드카 (downstream 호환)
    out_jgw = os.path.join(out_dir, f'{base}.jgw')
    out_prj = os.path.join(out_dir, f'{base}.prj')
    write_jgw(out_jgw, JGWParams(
        pixel_size_x=ps, rotation_x=0.0, rotation_y=0.0,
        pixel_size_y=-ps, top_left_x=tl_x, top_left_y=tl_y))
    with open(out_prj, 'w') as f:
        f.write(PRJ_5179)

    # 9. body bbox 사이드카 (Stage 2가 TIF 재읽기 없이 활용)
    #    pixmap은 이미 메모리에 있으므로 추가 비용 ≈ 0
    try:
        _, body_bbox_pix = extract_map_region(pdf['pixmap'], margin=0)
        bx, by, bw, bh = body_bbox_pix
        bminx = tl_x + bx * ps
        bmaxx = tl_x + (bx + bw) * ps
        bmaxy = tl_y - by * ps            # pixel_size_y = -ps
        bminy = tl_y - (by + bh) * ps
        if bminx > bmaxx: bminx, bmaxx = bmaxx, bminx
        if bminy > bmaxy: bminy, bmaxy = bmaxy, bminy
        out_body = os.path.join(out_dir, f'{base}.body.json')
        with open(out_body, 'w') as f:
            json.dump({'body_world_bbox': [bminx, bminy, bmaxx, bmaxy]}, f)
    except Exception:
        pass   # 폴백: Stage 2가 TIF 읽어 재계산

    return {
        'admin_code': code,
        'cost': 0.0,
        'pixel_size': ps,
        'gcp_count': 0,
        'scale': meta['scale'],
        'method': method,
        'method_info': method_info,
    }


# ============================================================
# CLI
# ============================================================

def main():
    ap = argparse.ArgumentParser(description='Stage 1: PDF 메인 좌표 생성')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='메인 PDF/JPG 폴더 (재귀)')
    ap.add_argument('--shp', required=True, help='행정경계 SHP')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--cost-threshold', type=float, default=30.0,
                    help='WARN 판정 cost 임계. PDF 메타는 항상 cost=0.')
    ap.add_argument('--no-pdf-meta', action='store_true',
                    help='PDF 메타 정합 비활성화 (SIFT/Powell 강제)')
    ap.add_argument('--render-dpi', type=int, default=RENDER_DPI,
                    help=f'PDF 렌더 DPI (기본 {RENDER_DPI})')
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

            if gdf is not None and src.lower().endswith('.pdf'):
                try:
                    r = georef_from_pdf_meta(src, gdf, args.out_dir,
                                              dpi=args.render_dpi)
                    if r:
                        method = r.get('method', 'pdf_meta')
                        n_meta += 1
                        mi = r.get('method_info', {})
                        extras = ''
                        if 'ratio_mismatch_pct' in mi:
                            extras = f' mismatch={mi["ratio_mismatch_pct"]:.2f}%'
                        elif 'fill_ratio' in mi:
                            extras = f' fill_ratio={mi["fill_ratio"]:.2f}'
                        print(f'  → {method}: 1:{r["scale"]:,}, '
                              f'ps={r["pixel_size"]:.4f} m/px '
                              f'({(time.time()-t0)*1000:.0f}ms){extras}')
                    else:
                        err_meta = '메타 추출 실패'
                except Exception as e:
                    err_meta = f'pdf_meta 예외: {e}'
                    print(f'  [PDF 메타 실패→SIFT 폴백] {e}')

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
