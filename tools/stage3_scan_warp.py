"""Stage 3: 스캔 ↔ 분할 PDF SIFT 매칭 + TPS 워핑 (비선형)

최적 조합:
- 매칭 대상: 분할 PDF의 지도영역 (Stage 2가 sheets_geo/에 JGW와 함께 저장)
- 스케일: scan과 sheet PDF 모두 0.5x로 정규화 (동일 물리 해상도)
- 특징점: SIFT 50,000개
- 매칭 필터: FLANN + Lowe ratio 0.75
- outlier 거부: MAGSAC++ 호모그래피 (inlier 식별용으로만 사용)
- 워핑: TPS (Thin-Plate Spline) via gdalwarp -tps
  · inlier GCPs → 공간 균등 샘플 400개 → GDAL VRT
  · 종이 접힘/휨 같은 비선형 왜곡 흡수

CLI:
  python -m gis_scan_tools.tools.stage3_scan_warp \\
      --identification scan_identified/_identification.csv \\
      --sheets-geo scan_identified/sheets_geo \\
      --out warped/
"""
import argparse
import csv
import json
import os
import re
import sys
import time

import cv2
import numpy as np


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from ._legacy.common import (
        parse_jgw, write_jgw, JGWParams, PRJ_5179,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )
except ImportError:
    from gis_scan_tools.tools._legacy.common import (
        parse_jgw, write_jgw, JGWParams, PRJ_5179,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )


# ============================================================
# 전처리
# ============================================================

def _mask_red(bgr):
    """스캔의 빨강 마커(수기 수정 표시)를 흰색으로 덮음."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, (0, 80, 80), (15, 255, 255))
    m2 = cv2.inRange(hsv, (165, 80, 80), (180, 255, 255))
    red = m1 | m2
    if red.any():
        out = bgr.copy()
        out[red > 0] = (255, 255, 255)
        return out
    return bgr


def preprocess(img, scale=0.5, strip_red=False):
    if strip_red and img.ndim == 3:
        img = _mask_red(img)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16, 16))
    g = clahe.apply(g)
    if scale != 1.0:
        g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    g = cv2.GaussianBlur(g, (0, 0), 0.8)
    return g


def save_thumb(path, img, max_dim=2000, q=85):
    if max(img.shape[:2]) > max_dim:
        s = max_dim / max(img.shape[:2])
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    _imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, q])


# ============================================================
# Sheet SIFT 캐시 ((admin, sheet)별 1회) + 디스크 pickle
# ============================================================

class SheetSiftCache:
    """Stage 2 산출 sheets_geo/ 폴더의 분할 PDF 이미지 SIFT 캐시."""

    def __init__(self, sheets_geo_dir, scan_scale=0.5,
                 nfeatures=30000, contrast=0.025, edge=20,
                 disk_cache_dir=None):
        self.sheets_geo_dir = sheets_geo_dir
        self.scan_scale = scan_scale
        self.cache = {}
        self.sift_params = dict(
            nfeatures=nfeatures, contrastThreshold=contrast,
            edgeThreshold=edge, sigma=1.6)
        self.disk_cache_dir = disk_cache_dir
        if disk_cache_dir:
            os.makedirs(disk_cache_dir, exist_ok=True)

    def get(self, admin_code, sheet_id):
        key = (admin_code, sheet_id)
        if key in self.cache:
            return self.cache[key]

        jpg = os.path.join(
            self.sheets_geo_dir, f'{admin_code}_{sheet_id}.jpg')
        jgw_p = os.path.join(
            self.sheets_geo_dir, f'{admin_code}_{sheet_id}.jgw')
        if not os.path.exists(jpg) or not os.path.exists(jgw_p):
            raise RuntimeError(
                f'sheet geo 없음: {jpg} (Stage 2를 최신 버전으로 재실행 필요)')

        sheet_img = _imread(jpg)
        sheet_jgw = parse_jgw(jgw_p)
        g_sheet = preprocess(sheet_img, scale=self.scan_scale)

        # 디스크 캐시
        cache_pkl = None
        if self.disk_cache_dir:
            cache_pkl = os.path.join(
                self.disk_cache_dir,
                f'sheet_sift_{admin_code}_{sheet_id}.pkl')
            if os.path.exists(cache_pkl):
                try:
                    import pickle
                    with open(cache_pkl, 'rb') as f:
                        data = pickle.load(f)
                    kp = [cv2.KeyPoint(x=p[0], y=p[1], size=p[2], angle=p[3])
                          for p in data['kp']]
                    self.cache[key] = (g_sheet, kp, data['des'],
                                       sheet_img, sheet_jgw)
                    return self.cache[key]
                except Exception:
                    pass

        sift = cv2.SIFT_create(**self.sift_params)
        t = time.time()
        kp, des = sift.detectAndCompute(g_sheet, None)
        print(f'  [sheet SIFT] {admin_code}_{sheet_id}: '
              f'{len(kp)}개 ({time.time()-t:.1f}s)')
        self.cache[key] = (g_sheet, kp, des, sheet_img, sheet_jgw)

        if cache_pkl:
            try:
                import pickle
                with open(cache_pkl, 'wb') as f:
                    pickle.dump({
                        'kp': [(k.pt[0], k.pt[1], k.size, k.angle) for k in kp],
                        'des': des,
                    }, f)
            except Exception:
                pass
        return self.cache[key]


# ============================================================
# 매칭 + 워핑 (단일 경로: 호모그래피)
# ============================================================

def match_and_warp(scan_jpg, admin_code, sheet_id, out_dir, sheet_cache,
                   target_ps=None, scan_scale=0.5,
                   save_intermediates=True, output_basename=None):
    """단일 스캔 처리 — scan ↔ sheet PDF 매칭 + 호모그래피 워핑."""
    os.makedirs(out_dir, exist_ok=True)
    t_total = time.time()
    result = {
        'scan': scan_jpg, 'admin_code': admin_code, 'sheet_id': sheet_id,
        'status': 'OK', 'message': '',
    }

    # 1) 스캔 로드 + 빨강 마스킹 + 전처리
    scan_img = _imread(scan_jpg)
    if scan_img is None:
        result.update(status='ERROR', message='scan 로드 실패')
        return result
    sh, sw = scan_img.shape[:2]

    if save_intermediates:
        save_thumb(os.path.join(out_dir, '02_scan_raw.jpg'), scan_img)

    g_scan = preprocess(scan_img, scale=scan_scale, strip_red=True)
    if save_intermediates:
        _imwrite(os.path.join(out_dir, '03_scan_prep.jpg'), g_scan,
                 [cv2.IMWRITE_JPEG_QUALITY, 85])

    sift = cv2.SIFT_create(nfeatures=30000, contrastThreshold=0.025,
                           edgeThreshold=20, sigma=1.6)
    t = time.time()
    kp_s, des_s = sift.detectAndCompute(g_scan, None)
    print(f'  SIFT scan: {len(kp_s)} ({time.time()-t:.1f}s)')
    if des_s is None or len(kp_s) < 200:
        result.update(status='FAIL',
                      message=f'스캔 키포인트 부족: {len(kp_s)}')
        return result

    # 2) sheet PDF SIFT (캐시)
    g_sheet, kp_p, des_p, sheet_img, sheet_jgw = sheet_cache.get(
        admin_code, sheet_id)
    if target_ps is None:
        target_ps = abs(sheet_jgw.pixel_size_x)  # sheet native = scan native

    # 3) FLANN + Lowe ratio
    matcher = cv2.FlannBasedMatcher(
        {'algorithm': 1, 'trees': 5}, {'checks': 50})
    pairs = matcher.knnMatch(des_s, des_p, k=2)
    good = [m for m, n in pairs if m.distance < 0.75 * n.distance]
    result['n_good'] = len(good)
    print(f'  good matches: {len(good)}')
    if len(good) < 100:
        result.update(status='FAIL',
                      message=f'good matches 부족: {len(good)}')
        return result

    # 4) MAGSAC++ 호모그래피 (정규화 좌표계: scan 0.5x ↔ sheet 0.5x)
    src = np.float32([kp_s[m.queryIdx].pt for m in good])
    dst = np.float32([kp_p[m.trainIdx].pt for m in good])
    H, mask = cv2.findHomography(
        src, dst, cv2.USAC_MAGSAC, 3.0,
        maxIters=10000, confidence=0.9999)
    if H is None or mask is None:
        result.update(status='FAIL', message='호모그래피 추정 실패')
        return result
    inl = mask.ravel().astype(bool)
    n_inl = int(inl.sum())
    inlier_pct = n_inl / len(good)
    result['n_inliers'] = n_inl
    result['inlier_pct'] = inlier_pct
    print(f'  MAGSAC inliers: {n_inl}/{len(good)} ({100*inlier_pct:.1f}%)')
    if n_inl < 30:
        result.update(status='FAIL',
                      message=f'inliers 부족: {n_inl}')
        return result
    if inlier_pct < 0.05:
        result.update(status='FAIL',
                      message=f'inlier 비율 과소: {100*inlier_pct:.1f}%')
        return result

    # 5) 인라이어 시각화
    if save_intermediates:
        good_inl = [m for m, ok in zip(good, inl) if ok]
        vis = cv2.drawMatches(
            g_scan, kp_s, g_sheet, kp_p, good_inl[:300], None,
            flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS)
        save_thumb(os.path.join(out_dir, '04_matches_inliers.jpg'),
                   vis, max_dim=2400)

    # 6) Inlier GCPs를 원본 해상도 + world 좌표로 변환
    scan_full_inl = src[inl] / scan_scale  # scan 원본 픽셀
    sheet_full_inl = dst[inl] / scan_scale  # sheet PDF 원본 픽셀
    world_x = sheet_jgw.top_left_x + sheet_full_inl[:, 0] * sheet_jgw.pixel_size_x
    world_y = sheet_jgw.top_left_y + sheet_full_inl[:, 1] * sheet_jgw.pixel_size_y
    world_pts = np.column_stack([world_x, world_y])

    # 7) 출력 raster bbox 계산 — inlier GCPs world 범위 + 여유
    # 호모그래피로 대충 코너 추정해도 되지만, inlier world 범위가 더 안전
    corners_sheet = np.float32(
        [[0, 0], [sw, 0], [sw, sh], [0, sh]]).reshape(-1, 1, 2)
    S = np.diag([scan_scale, scan_scale, 1.0])
    H_full = np.linalg.inv(S) @ H @ S
    corners_dst = cv2.perspectiveTransform(
        corners_sheet, H_full).reshape(-1, 2)
    cw_x = sheet_jgw.top_left_x + corners_dst[:, 0] * sheet_jgw.pixel_size_x
    cw_y = sheet_jgw.top_left_y + corners_dst[:, 1] * sheet_jgw.pixel_size_y
    out_minx, out_maxx = float(cw_x.min()), float(cw_x.max())
    out_miny, out_maxy = float(cw_y.min()), float(cw_y.max())
    out_w = int(np.ceil((out_maxx - out_minx) / target_ps))
    out_h = int(np.ceil((out_maxy - out_miny) / target_ps))
    result['output_size'] = [out_w, out_h]
    result['world_bbox'] = [out_minx, out_miny, out_maxx, out_maxy]
    if out_w <= 0 or out_h <= 0:
        result.update(status='FAIL',
                      message=f'출력 크기 비정상: {out_w}x{out_h}')
        return result

    # 8) GCP 서브샘플링 + 중복 제거 (TPS 안정성)
    scan_pts = scan_full_inl
    # 공간 균등 400개 (TPS O(N^3) 완화)
    MAX_GCPS = 400
    if len(scan_pts) > MAX_GCPS:
        idx = np.linspace(0, len(scan_pts) - 1, MAX_GCPS).astype(int)
        scan_pts = scan_pts[idx]
        world_pts = world_pts[idx]
    # GDAL은 동일 좌표 GCP 있으면 matrix singular → dedup
    seen_s = set(); seen_w = set()
    keep = []
    for (sx, sy), (wx, wy) in zip(scan_pts, world_pts):
        sk = (round(sx, 1), round(sy, 1))
        wk = (round(wx, 2), round(wy, 2))
        if sk in seen_s or wk in seen_w:
            continue
        seen_s.add(sk); seen_w.add(wk)
        keep.append(((sx, sy), (wx, wy)))
    if len(keep) < 10:
        result.update(status='FAIL',
                      message=f'TPS GCP 부족 (dedup 후 {len(keep)})')
        return result
    result['n_gcps_tps'] = len(keep)

    # 9) TPS 워핑 — 희소 격자 평가 + 조밀 보간 (gdalwarp의 1/10 속도)
    # Opt #1: RBFInterpolator는 d-value 입력(N,2)을 한 번에 처리 — 커널 분해 1회로 단축
    t = time.time()
    from scipy.interpolate import RBFInterpolator
    cw = np.array([w for _, w in keep], dtype=np.float64)   # world (N,2)
    cs = np.array([s for s, _ in keep], dtype=np.float64)   # scan_px (N,2)

    rbf = RBFInterpolator(cw, cs, kernel='thin_plate_spline', smoothing=0.0)

    # 희소 격자에서 평가 (16픽셀 간격)
    STEP = 16
    gy = np.arange(0, out_h + STEP, STEP)
    gx = np.arange(0, out_w + STEP, STEP)
    GX, GY = np.meshgrid(gx, gy)
    world_xs = (out_minx + GX * target_ps).ravel()
    world_ys = (out_maxy - GY * target_ps).ravel()
    coords_wp = np.column_stack([world_xs, world_ys])
    mapped = rbf(coords_wp)   # (M, 2) — x,y 동시 평가
    sx_grid = mapped[:, 0].reshape(GX.shape).astype(np.float32)
    sy_grid = mapped[:, 1].reshape(GY.shape).astype(np.float32)

    # 격자 → 전체 크기 업샘플
    map_x = cv2.resize(sx_grid, (out_w, out_h),
                       interpolation=cv2.INTER_LINEAR)
    map_y = cv2.resize(sy_grid, (out_w, out_h),
                       interpolation=cv2.INTER_LINEAR)

    # cv2.remap
    warped = cv2.remap(
        scan_img, map_x, map_y,
        interpolation=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    print(f'  TPS 워핑: {time.time()-t:.1f}s (GCP={len(keep)}, 격자={STEP}px)')

    # 10) 저장
    base = output_basename or f'{admin_code}_{sheet_id}'
    warped_jpg = os.path.join(out_dir, f'{base}.jpg')
    warped_jgw = os.path.join(out_dir, f'{base}.jgw')
    warped_prj = os.path.join(out_dir, f'{base}.prj')
    _imwrite(warped_jpg, warped, [cv2.IMWRITE_JPEG_QUALITY, 92])
    write_jgw(warped_jgw, JGWParams(
        pixel_size_x=target_ps, rotation_x=0.0, rotation_y=0.0,
        pixel_size_y=-target_ps,
        top_left_x=out_minx, top_left_y=out_maxy))
    with open(warped_prj, 'w') as f:
        f.write(PRJ_5179)
    if save_intermediates:
        save_thumb(os.path.join(out_dir, '05_warped_scan.jpg'), warped)

    result['warped_jpg'] = warped_jpg
    result['warped_jgw'] = warped_jgw
    result['target_ps'] = target_ps
    result['elapsed'] = time.time() - t_total
    with open(os.path.join(out_dir, 'status.json'), 'w') as f:
        json.dump(result, f, indent=2)
    return result


# ============================================================
# CLI
# ============================================================

def _discover_identified(identified_dir):
    """identified/{시도}/{시군구}/{admin}_{sheet}[.suffix].{ext} 재귀 스캔.

    파일명 패턴: {8자리}_{N-i}[.optional_suffix].{jpg|jpeg|png}
    _2, _3 같은 중복 방지 suffix가 붙어있어도 허용 (Stage 2 자동 rename).

    사용자 수동 보강: 실패 스캔을 표준명으로 rename해서 이 폴더에 두면 자동 픽업.
    """
    pat = re.compile(
        r'^(\d{8})_(\d+-\d+)(?:_\d+)?\.(jpg|jpeg|png|JPG|JPEG|PNG)$')
    targets = []
    for root, _, files in os.walk(identified_dir):
        for f in sorted(files):
            m = pat.match(f)
            if not m:
                continue
            admin, sid = m.group(1), m.group(2)
            targets.append((os.path.join(root, f), admin, sid))
    return targets


def main():
    ap = argparse.ArgumentParser(
        description='Stage 3: 스캔 ↔ 분할 PDF 매칭 + TPS 워핑')
    ap.add_argument('--identified', default=None,
                    help='Stage 2 산출 identified/ 폴더 '
                         '({시도}/{시군구}/{admin}_{sheet}.jpg 패턴). '
                         '폴더 기반 입력 발견 — 사용자 수동 보강 파일도 자동 인식')
    ap.add_argument('--identification', default=None,
                    help='(레거시) Stage 2 _identification.csv — '
                         '--identified 미지정 시 폴백')
    ap.add_argument('--sheets-geo', required=True,
                    help='Stage 2 산출 sheets_geo 폴더 (분할 PDF + JGW)')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--no-intermediates', action='store_true',
                    help='중간 시각화 파일 저장 안 함 (속도)')
    ap.add_argument('--target-ps', type=float, default=None,
                    help='출력 픽셀크기 (m/px). 기본=sheet PDF ps')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    # 입력 발견: identified/ 폴더 우선, 없으면 CSV 폴백
    targets = []
    if args.identified and os.path.isdir(args.identified):
        targets = _discover_identified(args.identified)
        print(f'[Stage 3] identified/ 폴더 스캔: {len(targets)}장 '
              f'({args.identified})')
    elif args.identification and os.path.exists(args.identification):
        with open(args.identification, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if row['status'] == 'OK' and row['admin_code'] and row.get('sheet_id'):
                    targets.append((row['scan_path'], row['admin_code'],
                                    row['sheet_id']))
        print(f'[Stage 3] CSV 폴백: {len(targets)}장 '
              f'({args.identification})')
    else:
        print('ERROR: --identified 또는 --identification 지정 필요')
        sys.exit(1)

    if not targets:
        print('ERROR: 처리 대상 0장')
        sys.exit(1)

    cache = SheetSiftCache(
        args.sheets_geo,
        disk_cache_dir=os.path.join(args.out_dir, '_sift_cache'))

    csv_path = os.path.join(args.out_dir, '_status.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scan_path', 'admin_code', 'sheet_id', 'status',
                    'n_inliers', 'n_good', 'inlier_pct',
                    'output_w', 'output_h', 'message', 'elapsed_s'])

        n_ok = n_fail = 0
        for i, (scan, code, sid) in enumerate(targets, 1):
            label = f'{code}_{sid}'
            print(f'\n[{i}/{len(targets)}] {label} | {os.path.basename(scan)}')

            # 출력: out/{시도}/{시군구}/{code}_{sheet}/
            sub_out = os.path.join(
                args.out_dir, code[:2], code[:5], label)

            try:
                r = match_and_warp(
                    scan, code, sid, sub_out, cache,
                    target_ps=args.target_ps,
                    save_intermediates=not args.no_intermediates,
                    output_basename=label)
                osz = r.get('output_size', [0, 0])
                inl_pct = r.get('inlier_pct', 0)
                w.writerow([
                    scan, code, sid, r['status'],
                    r.get('n_inliers', ''),
                    r.get('n_good', ''),
                    f'{100*inl_pct:.1f}%',
                    osz[0], osz[1], r.get('message', ''),
                    f'{r.get("elapsed", 0):.1f}',
                ])
                if r['status'] == 'OK':
                    n_ok += 1
                else:
                    n_fail += 1
            except Exception as e:
                w.writerow([scan, code, sid, 'ERROR',
                            '', '', '', '', '', str(e), '0'])
                n_fail += 1
                print(f'  ERROR: {e}')

    print(f'\n[Stage 3] 완료: OK={n_ok}, FAIL/ERROR={n_fail}')
    print(f'  결과: {csv_path}')


if __name__ == '__main__':
    main()
