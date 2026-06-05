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
import tempfile

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
    # 크로스플랫폼 임시 파일 (Windows /tmp 부재 회피)
    tmp = os.path.join(tempfile.gettempdir(), f'_scale_{os.getpid()}.jpg')
    _imwrite(tmp, bw)
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        _imwrite(os.path.join(debug_dir,
                               os.path.splitext(os.path.basename(scan_path))[0]
                               + '_scale.jpg'),
                 bw)
    # Stage 2 의 tesseract 자동 탐색 재사용 (Windows 표준 설치 경로 포함)
    try:
        from .stage2_scan_identify import check_tesseract
    except ImportError:
        from gis_scan_tools.tools.stage2_scan_identify import check_tesseract
    tess_cmd, _ = check_tesseract()
    if not tess_cmd:
        return None
    sub_kw = {}
    if sys.platform == 'win32':
        sub_kw['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    try:
        res = subprocess.run(
            [tess_cmd, tmp, '-', '--psm', '6', '-l', 'kor+eng'],
            capture_output=True, text=True, timeout=15, **sub_kw)
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
                         out_dir, ps=None, tile_gap=3,
                         flat_layout=False, basename=None):
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

    if flat_layout:
        sub_out = os.path.join(out_dir, admin_code[:2], admin_code[:5])
    else:
        sub_out = os.path.join(out_dir, center_mode,
                                admin_code[:2], admin_code[:5])
    os.makedirs(sub_out, exist_ok=True)
    base = basename or f'{admin_code}_virtual_merged'
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


# 시트별 SHP 정합 임계. cost = SHP 경계점 ↔ 이미지 주황 스켈레톤 평균거리(px).
SHP_REFINE_COST_MAX = 15.0   # 4-DoF mean cost 이 이상이면 정합 신뢰 불가 → 폴백
SHP_MIN_ORANGE_PX = 500
SHP_PS_TOL = 0.15            # 허용 ps 편차(±15%) — 벗어나면 스케일 발산으로 보고 폴백
TPS_SMOOTH = 10.0            # TPS 평활 (작을수록 대응점에 밀착, 과적합 위험)
TPS_MAX_CTRL = 500          # TPS control 상한 (적합 O(n^3)/평가 O(nq·n) 가드)
TPS_RENDER_DS = 20          # TPS 렌더 희소격자 다운샘플 (잔차가 완만 → 거칠어도 무방)


def _sim_world2px(s, th, ox, oy):
    """4-DoF 유사변환(스케일 s·회전 th·중심 ox/oy) world→px 평가자.
    th=0 이면 fx=(X-ox)/s, fy=(oy-Y)/s (refine_position 과 동일 규약)."""
    cs, sn = np.cos(th), np.sin(th)

    def ev(P):
        dx = P[:, 0] - ox
        dy = P[:, 1] - oy
        return (cs * dx + sn * dy) / s, (sn * dx - cs * dy) / s
    return ev


def _sim_px2world_corners(s, th, ox, oy, bw, bh):
    """4-DoF 역변환으로 본문 4모서리(px)의 world 좌표 → footprint 산출용."""
    cs, sn = np.cos(th), np.sin(th)
    out = []
    for px, py in ((0, 0), (bw, 0), (bw, bh), (0, bh)):
        dx = s * (cs * px + sn * py)
        dy = s * (sn * px - cs * py)
        out.append((ox + dx, oy + dy))
    return out


def _cost_mean(ev, Q, dist_map, shape):
    """SHP 경계점 Q 를 ev 로 px 투영 → dist_map 평균거리(px). 적을수록 정합 우수."""
    from scipy.ndimage import map_coordinates
    H, W = shape
    fx, fy = ev(Q)
    m = 5
    val = (fx >= m) & (fx < W - m) & (fy >= m) & (fy < H - m)
    if int(val.sum()) < 50:
        return 1e9
    d = map_coordinates(dist_map, [fy[val], fx[val]], order=1,
                        mode='constant', cval=30.0)
    return float(np.mean(d))


def _fit_sheet_transform(body, shp_gdf, init_ox, init_oy, ps0):
    """본문 1장 → bnd_adm_pg 주황선 정합 변환 적합.

    파이프라인: 4-DoF(유사변환+회전) Powell → 그 결과로 TPS 잔차보정(평활).
    실데이터 검증상 4-DoF+TPS 가 3-DoF/affine/TPS-단독보다 안정·정밀
    (mean ~1.5px, p90 ~5px @ 0.56 m/px ≈ 1m 미만).

    Returns dict:
      ok      : 정합 신뢰 여부 (False 면 호출자가 4-DoF 합의값으로 폴백)
      params4 : (s, th, ox, oy) — footprint·폴백 합의용 (항상 채움)
      ev      : world→px 평가자 (tps 또는 sim)
      is_tps  : ev 가 TPS 인지 (렌더 시 희소격자 적용)
      cost    : 채택 변환의 mean cost (px)
    """
    try:
        from .common import (
            extract_orange_mask, build_skeleton_and_distmap,
            compute_aoi, clip_shp_to_aoi, sample_points_from_boundaries)
    except ImportError:
        from gis_scan_tools.tools.common import (
            extract_orange_mask, build_skeleton_and_distmap,
            compute_aoi, clip_shp_to_aoi, sample_points_from_boundaries)
    from scipy.optimize import minimize
    from scipy.spatial import cKDTree
    from scipy.interpolate import RBFInterpolator

    bh, bw = body.shape[:2]
    p4 = (ps0, 0.0, init_ox, init_oy)        # 폴백 기본값
    try:
        mask = extract_orange_mask(body)
        if int(np.sum(mask > 0)) < SHP_MIN_ORANGE_PX:
            return dict(ok=False, params4=p4,
                        ev=_sim_world2px(*p4), is_tps=False, cost=None)
        _, skel, dist = build_skeleton_and_distmap(mask)
        aoi = compute_aoi(init_ox, init_oy, ps0, (bh, bw))
        bnds = clip_shp_to_aoi(shp_gdf, aoi, buffer_ratio=0.45)
        Q = np.asarray(sample_points_from_boundaries(bnds, num_points=3000))
        if len(Q) < 50:
            return dict(ok=False, params4=p4,
                        ev=_sim_world2px(*p4), is_tps=False, cost=None)

        # --- 1) 4-DoF 유사변환+회전 (Powell, dist_map mean 최소화) ---
        def cost4(p):
            if p[0] <= 0:
                return 1e10
            return _cost_mean(_sim_world2px(*p), Q, dist, (bh, bw))
        res = minimize(cost4, [ps0, 0.0, init_ox, init_oy], method='Powell',
                       options={'maxiter': 400, 'ftol': 1e-7})
        s, th, ox, oy = (float(v) for v in res.x)
        p4 = (s, th, ox, oy)
        c4 = _cost_mean(_sim_world2px(s, th, ox, oy), Q, dist, (bh, bw))
        lo, hi = ps0 * (1 - SHP_PS_TOL), ps0 * (1 + SHP_PS_TOL)
        if not (c4 <= SHP_REFINE_COST_MAX and lo <= s <= hi):
            return dict(ok=False, params4=p4,
                        ev=_sim_world2px(*p4), is_tps=False, cost=c4)

        # --- 2) TPS 잔차보정 (4-DoF 대응점 → 평활 thin-plate spline) ---
        sim_ev = _sim_world2px(s, th, ox, oy)
        fx, fy = sim_ev(Q)
        val = (fx >= 0) & (fx < bw) & (fy >= 0) & (fy < bh)
        Qv = Q[val]
        dd, idx = cKDTree(skel).query(np.c_[fx[val], fy[val]])
        keep = dd <= np.percentile(dd, 85)        # 잘못된 대응점(상위 15%) 절사
        Qc, Tc = Qv[keep], skel[idx][keep]
        ev, is_tps, cost = sim_ev, False, c4
        if len(Qc) >= 50:
            if len(Qc) > TPS_MAX_CTRL:
                sel = np.linspace(0, len(Qc) - 1, TPS_MAX_CTRL).astype(int)
                Qc, Tc = Qc[sel], Tc[sel]
            try:
                rbf = RBFInterpolator(Qc, Tc, kernel='thin_plate_spline',
                                      smoothing=TPS_SMOOTH)
                tps_ev = lambda P, _r=rbf: (_r(P)[:, 0], _r(P)[:, 1])
                c_tps = _cost_mean(tps_ev, Q, dist, (bh, bw))
                if c_tps < c4:                    # TPS 가 개선될 때만 채택
                    ev, is_tps, cost = tps_ev, True, c_tps
            except Exception as e:                # noqa: BLE001
                print(f'  [TPS 적합 실패 → 4-DoF 유지] {e}')
        return dict(ok=True, params4=p4, ev=ev, is_tps=is_tps, cost=cost)
    except Exception as e:                                   # noqa: BLE001
        print(f'  [SHP refine 예외] {e}')
        return dict(ok=False, params4=p4,
                    ev=_sim_world2px(*p4), is_tps=False, cost=None)


def merge_admin_shp_refined(admin_code, sheets_dict, admin_geom, shp_gdf,
                            out_dir, ps=None, tile_gap=3,
                            flat_layout=False, basename=None):
    """admin 1개 — 분할을 각각 bnd_adm_pg 주황선에 정합 후 월드좌표 모자이크.

    1) centroid-캔버스로 시트별 초기 월드위치 추정 (실제 본문크기×ps 기준 —
       admin bbox 분할이 아니라 인쇄 지도영역 크기를 반영해야 수렴함).
    2) 시트마다 4-DoF(유사변환+회전) → TPS 잔차보정 (_fit_sheet_transform).
    3) 정합 실패 시트는 성공 시트들의 4-DoF 합의(중앙 회전·ps + 중앙 오프셋델타)로
       폴백(타일 일관성 유지).
    4) 시트별 변환(world→px)을 출력 캔버스에 footprint 영역만 remap → 단일 JGW.
       (TPS 는 희소격자 평가 후 bilinear 업샘플 — 메모리/연산 가드)
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

    # 공통 셀 크기로 통일 (타일 정합성) — 평균 본문 크기
    h_avg = int(round(np.mean([b.shape[0] for b in bodies.values()])))
    w_avg = int(round(np.mean([b.shape[1] for b in bodies.values()])))

    bnd = admin_geom.bounds
    if ps is None:
        # 축척 OCR 미제공 시 폴백 — 캔버스(2×2 시트)가 admin bbox 를 덮는다고 가정
        ps = max((bnd[2] - bnd[0]) / (cols * w_avg),
                 (bnd[3] - bnd[1]) / (rows * h_avg))
    ps0 = float(ps)

    # centroid-캔버스 초기 레이아웃 — 실제 시트 크기(w_avg×ps)로 타일 배치 후
    # admin 중심에 정렬. (admin bbox 를 셀로 나누면 시트 크기와 안 맞아 발산)
    cx, cy = admin_geom.centroid.x, admin_geom.centroid.y
    step_x = (w_avg + tile_gap) * ps0
    step_y = (h_avg + tile_gap) * ps0
    canvas_w_world = cols * w_avg * ps0 + (cols - 1) * tile_gap * ps0
    canvas_h_world = rows * h_avg * ps0 + (rows - 1) * tile_gap * ps0
    layout_tlx = cx - canvas_w_world / 2.0
    layout_tly = cy + canvas_h_world / 2.0

    # --- 시트별 변환 적합 (4-DoF → TPS) ---
    sheets = []   # dict per sheet
    refined, fell_back, missing = [], [], []
    for i in range(1, N + 1):
        if (N, i) not in bodies:
            missing.append(i)
            continue
        body = bodies[(N, i)]
        if body.shape[:2] != (h_avg, w_avg):
            body = cv2.resize(body, (w_avg, h_avg), interpolation=cv2.INTER_AREA)
        r = (i - 1) // cols
        c = (i - 1) % cols
        init_ox = layout_tlx + c * step_x
        init_oy = layout_tly - r * step_y
        ft = _fit_sheet_transform(body, shp_gdf, init_ox, init_oy, ps0)
        ft.update(i=i, body=body, init_ox=init_ox, init_oy=init_oy)
        sheets.append(ft)
        if ft['ok']:
            refined.append(i)
            print(f'  [sheet {i}] {"TPS" if ft["is_tps"] else "4DoF"} '
                  f'cost={ft["cost"]:.2f}px')
        else:
            fell_back.append(i)
            c_ = ft['cost']
            print(f'  [sheet {i}] 폴백'
                  + (f' (cost={c_:.2f}px)' if c_ is not None else ' (주황선 부족)'))

    # --- 실패 시트 폴백: 성공 시트 4-DoF 합의(중앙 회전·ps + 중앙 오프셋델타) ---
    succ = [s for s in sheets if s['ok']]
    if succ:
        mth = float(np.median([s['params4'][1] for s in succ]))
        mps = float(np.median([s['params4'][0] for s in succ]))
        mdx = float(np.median([s['params4'][2] - s['init_ox'] for s in succ]))
        mdy = float(np.median([s['params4'][3] - s['init_oy'] for s in succ]))
        for s in sheets:
            if not s['ok']:
                p = (mps, mth, s['init_ox'] + mdx, s['init_oy'] + mdy)
                s['params4'] = p
                s['ev'] = _sim_world2px(*p)
                s['is_tps'] = False

    # --- 출력 캔버스 산정: 모든 시트 footprint(4-DoF 모서리)의 합집합 ---
    out_ps = float(np.median([s['params4'][0] for s in sheets]))
    for s in sheets:                       # footprint 1회 계산해 재사용
        s['corners'] = _sim_px2world_corners(*s['params4'], w_avg, h_avg)
    corners = [p for s in sheets for p in s['corners']]
    minx = min(p[0] for p in corners); maxx = max(p[0] for p in corners)
    miny = min(p[1] for p in corners); maxy = max(p[1] for p in corners)
    canvas_w = max(1, int(np.ceil((maxx - minx) / out_ps)))
    canvas_h = max(1, int(np.ceil((maxy - miny) / out_ps)))
    canvas = np.full((canvas_h, canvas_w, 3), 255, np.uint8)

    # --- 시트별 footprint 영역만 remap (전체 캔버스 그리드 생성 회피) ---
    # world→px 평가는 항상 희소격자(1/ds)에서 한 뒤 bilinear 업샘플:
    #   - sim/affine(폴백 포함): 선형장이라 bilinear 보간이 정확 (오차 0)
    #   - TPS: 잔차가 완만해 거친 격자로도 충분 (전 픽셀 RBF 평가 회피)
    # → 비-TPS 경로의 전체 footprint meshgrid(float64 ~GB) 생성을 제거.
    margin = 60   # TPS 잔차 여유(px)
    ds = TPS_RENDER_DS
    for s in sorted(sheets, key=lambda s: s['i']):
        body, ev = s['body'], s['ev']
        oxs = [(p[0] - minx) / out_ps for p in s['corners']]
        oys = [(maxy - p[1]) / out_ps for p in s['corners']]
        px0 = max(0, int(np.floor(min(oxs))) - margin)
        py0 = max(0, int(np.floor(min(oys))) - margin)
        px1 = min(canvas_w, int(np.ceil(max(oxs))) + margin)
        py1 = min(canvas_h, int(np.ceil(max(oys))) + margin)
        if px1 <= px0 or py1 <= py0:
            continue
        sub_w, sub_h = px1 - px0, py1 - py0
        gw, gh = max(2, sub_w // ds), max(2, sub_h // ds)
        # 희소격자 world 좌표 (끝점만 필요 — 전체 arange 회피)
        sxs = minx + (px0 + 0.5 + np.linspace(0, sub_w - 1, gw)) * out_ps
        sys_ = maxy - (py0 + 0.5 + np.linspace(0, sub_h - 1, gh)) * out_ps
        GX, GY = np.meshgrid(sxs, sys_)
        fxs, fys = ev(np.c_[GX.ravel(), GY.ravel()])
        mapx = cv2.resize(fxs.reshape(gh, gw).astype(np.float32), (sub_w, sub_h))
        mapy = cv2.resize(fys.reshape(gh, gw).astype(np.float32), (sub_w, sub_h))
        warp = cv2.remap(body, mapx, mapy, cv2.INTER_LINEAR,
                         borderValue=(255, 255, 255))
        # 비-흰색 & 본문 범위 내 픽셀만 합성 (min(2): 채널×bool 3배 배열 회피)
        m = ((mapx >= 0) & (mapx < w_avg) & (mapy >= 0) & (mapy < h_avg)
             & (warp.min(axis=2) <= 245))
        canvas[py0:py1, px0:px1][m] = warp[m]
        del mapx, mapy, warp, m, GX, GY, fxs, fys   # 피크 메모리 캡

    if flat_layout:
        sub_out = os.path.join(out_dir, admin_code[:2], admin_code[:5])
    else:
        sub_out = os.path.join(out_dir, 'shp_refined',
                                admin_code[:2], admin_code[:5])
    os.makedirs(sub_out, exist_ok=True)
    base = basename or f'{admin_code}_scan_merged'
    jpg_p = os.path.join(sub_out, f'{base}.jpg')
    jgw_p = os.path.join(sub_out, f'{base}.jgw')
    prj_p = os.path.join(sub_out, f'{base}.prj')
    shp_p = os.path.join(sub_out, f'{base}_bbox.shp')

    _imwrite(jpg_p, canvas, [cv2.IMWRITE_JPEG_QUALITY, 88])
    write_jgw(jgw_p, JGWParams(
        pixel_size_x=out_ps, rotation_x=0.0, rotation_y=0.0,
        pixel_size_y=-out_ps, top_left_x=minx, top_left_y=maxy))
    with open(prj_p, 'w') as f:
        f.write(PRJ_5179)
    _save_canvas_bbox_shp(shp_p, admin_code, 'shp_refined',
                          (minx, miny, maxx, maxy))

    return {
        'status': 'OK', 'admin_code': admin_code,
        'center_mode': 'shp_refined', 'N': N, 'grid': [rows, cols],
        'placed': sorted(refined + fell_back),
        'refined': refined, 'fell_back': fell_back, 'missing': missing,
        'canvas_size': [canvas_w, canvas_h],
        'pixel_size': [out_ps, out_ps],
        'world_bbox': [minx, miny, maxx, maxy],
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
