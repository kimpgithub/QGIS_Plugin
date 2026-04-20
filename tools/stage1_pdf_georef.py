"""Stage 1: PDF 메인 좌표 생성 — PDF 메타(축척+그리드) 우선, SIFT/Powell 폴백.

PDF 메타 정합:
  · PDF 텍스트의 축척(1:N) → 픽셀 크기 ps = 0.0254 × N / DPI
  · sheet 그리드 외곽 중심 → admin bbox center 정렬 → TL world 좌표
  · 정확도 ≤4m, 즉시 (~10ms), 다도해/복잡 형상 admin도 회수

검증 (제주 12 admin):
  · 10개 OK admin: SIFT 결과와 0~4m 일치
  · 2개 실패 admin (39010320 추자면, 39020110): SIFT 정합 실패 → PDF 메타로 자동 회수

CLI:
  python -m gis_scan_tools.tools.stage1_pdf_georef \\
      --in pdf_main/ --shp bnd_adm_pg.shp --out pdf_main_geo/

산출: pdf_main_geo/{code}.{jpg,jgw,prj} + _status.csv
폴백 시 _gcp.vrt, _tps.vrt 추가 (SIFT/Powell 경로)
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
    from ._legacy.common import write_jgw, JGWParams, PRJ_5179
except ImportError:
    from gis_scan_tools.tools._legacy.shp_georeferencer import SHPGeoreferencer
    from gis_scan_tools.tools._legacy.common import write_jgw, JGWParams, PRJ_5179


# Stage 2와 동일 — PDF 라벨 좌상단이 셀 좌상단보다 안쪽으로 들어간 양 (PDF pt)
LABEL_OFFSET_X_PT = -13.82
LABEL_OFFSET_Y_PT = -2.76
RENDER_DPI = 300


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


def _render_pdf_to_jpg(pdf_path, out_jpg, dpi=RENDER_DPI):
    doc = fitz.open(pdf_path)
    pix = doc[0].get_pixmap(dpi=dpi)
    pix.save(out_jpg)
    doc.close()
    return pix.width, pix.height


def georef_from_pdf_meta(pdf_path, gdf, out_dir, base_name=None):
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

    ps = 0.0254 * meta['scale'] / RENDER_DPI

    # PDF → JPG 렌더링 (후속 stage 입력용)
    base = base_name or code
    out_jpg = os.path.join(out_dir, f'{base}.jpg')
    img_w, _ = _render_pdf_to_jpg(pdf_path, out_jpg, dpi=RENDER_DPI)

    # TL: 그리드 center 픽셀이 admin bbox center world에 align
    px_per_pt = RENDER_DPI / 72.0
    gcx_px = meta['grid_cx_pt'] * px_per_pt
    gcy_px = meta['grid_cy_pt'] * px_per_pt
    tl_x = bcx - gcx_px * ps
    tl_y = bcy + gcy_px * ps

    # JGW + PRJ
    out_jgw = os.path.join(out_dir, f'{base}.jgw')
    out_prj = os.path.join(out_dir, f'{base}.prj')
    write_jgw(out_jgw, JGWParams(
        pixel_size_x=ps, rotation_x=0.0, rotation_y=0.0,
        pixel_size_y=-ps, top_left_x=tl_x, top_left_y=tl_y))
    with open(out_prj, 'w') as f:
        f.write(PRJ_5179)

    return {
        'admin_code': code,
        'cost': 0.0,
        'pixel_size': ps,
        'gcp_count': 0,
        'scale': meta['scale'],
        'method': 'pdf_meta',
    }


def main():
    ap = argparse.ArgumentParser(description='Stage 1: PDF 메인 좌표 생성')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='메인 PDF/JPG 폴더 (재귀)')
    ap.add_argument('--shp', required=True, help='행정경계 SHP')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--cost-threshold', type=float, default=2.0,
                    help='실패 판정 cost 임계값(px). PDF 메타는 항상 cost=0.')
    ap.add_argument('--no-pdf-meta', action='store_true',
                    help='PDF 메타 기반 정합 비활성화 (SIFT/Powell만 사용)')
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
                    r = georef_from_pdf_meta(src, gdf, args.out_dir)
                    if r:
                        method = 'pdf_meta'
                        n_meta += 1
                        print(f'  → PDF 메타: 1:{r["scale"]:,}, '
                              f'ps={r["pixel_size"]:.4f} m/px '
                              f'({(time.time()-t0)*1000:.0f}ms)')
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
