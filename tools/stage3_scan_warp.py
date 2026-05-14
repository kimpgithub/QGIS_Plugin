"""Stage 3: 스캔 ↔ 분할 PDF SIFT 매칭 + 단일 H 워핑

흐름:
- 매칭 대상: 분할 PDF의 지도영역 (Stage 2가 sheets_geo/에 JGW와 함께 저장)
- 스케일: scan과 sheet PDF 모두 0.5x로 정규화 (동일 물리 해상도)
- 특징점: SIFT 30,000개
- 매칭 필터: FLANN + Lowe ratio 0.75
- outlier 거부: MAGSAC++ 호모그래피
- 폴리곤 필터: SHP 행정리 폴리곤 안 inlier 만 보존 (sparse admin 시 폴백)
- 워핑: 단일 호모그래피 (cv2.warpPerspective)
  · 분할시트 한 장 안에선 종이 휨이 충분히 선형 → 단일 H 가 TPS 보다 정확
  · TPS smoothing=0+400 GCP 는 GCP 사이 진동(Runge)으로 회귀 → 폐기

CLI:
  python -m gis_scan_tools.tools.stage3_scan_warp \\
      --identified scan_identified/identified \\
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
    from .common import (
        parse_jgw, write_jgw, JGWParams, PRJ_5179,
        build_admin_polygon_mask, extract_map_region_scan,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )
except ImportError:
    from gis_scan_tools.tools.common import (
        parse_jgw, write_jgw, JGWParams, PRJ_5179,
        build_admin_polygon_mask, extract_map_region_scan,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )

# 폴리곤 필터 — in-polygon inlier 가 이 임계 미만이면 폴백(전체 inlier)
POLY_FILTER_MIN_INLIERS = 50


# ============================================================
# 전처리
# ============================================================

def preprocess(img, scale=0.5):
    """SIFT 입력 전처리 — 그레이 + CLAHE + 다운샘플 + 가우시안.

    빨강 마커 마스킹은 제거됨 — H≤15 임계가 주황 (행정경계 인쇄색) 까지
    덮어 정합 신호 손상. MAGSAC 이 마커 outlier 충분히 거름 (실측 inlier 90%+).
    """
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
            # PDF-less 통과 — 호출자가 None 받으면 SHP 폴백으로 분기
            self.cache[key] = None
            return None

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

def _save_warped(warped, sheet_jgw, out_dir, base, save_intermediates,
                  result, method, t_total):
    """warp 결과 저장 (jpg + jgw + prj) + result dict 업데이트."""
    warped_jpg = os.path.join(out_dir, f'{base}.jpg')
    warped_jgw = os.path.join(out_dir, f'{base}.jgw')
    warped_prj = os.path.join(out_dir, f'{base}.prj')
    _imwrite(warped_jpg, warped, [cv2.IMWRITE_JPEG_QUALITY, 92])
    write_jgw(warped_jgw, JGWParams(
        pixel_size_x=sheet_jgw.pixel_size_x, rotation_x=0.0, rotation_y=0.0,
        pixel_size_y=sheet_jgw.pixel_size_y,
        top_left_x=sheet_jgw.top_left_x, top_left_y=sheet_jgw.top_left_y))
    with open(warped_prj, 'w') as f:
        f.write(PRJ_5179)
    if save_intermediates:
        save_thumb(os.path.join(out_dir, '05_warped_scan.jpg'), warped)
    result['warped_jpg'] = warped_jpg
    result['warped_jgw'] = warped_jgw
    result['method'] = method
    result['elapsed'] = time.time() - t_total
    with open(os.path.join(out_dir, 'status.json'), 'w') as f:
        json.dump(result, f, indent=2)
    return result


def _axis_aligned_fallback(scan_img, sheet_img, sheet_jgw, out_dir, base,
                            save_intermediates, result, t_total,
                            fallback_reason):
    """SIFT 매칭 실패 시 axis-aligned 4-corner 매핑으로 warp.

    scan body 4-corner ↔ sheet PDF body 4-corner 직접 perspective transform.
    SIFT 의존성 0 → 희박 본문(다도해/작은 섬 산재) 케이스에서 안정 동작.
    가정: scan body 와 sheet PDF body 가 동일 영역을 다른 비율로 렌더 ↔ 1:1 대응.
    정확도는 SIFT 보다 약간 떨어지나 sheet_bbox 와 1:1 dimension 유지 → 병합 갭 없음.
    """
    sh, sw = scan_img.shape[:2]
    ph, pw = sheet_img.shape[:2]
    src = np.float32([[0, 0], [sw, 0], [sw, sh], [0, sh]])
    dst = np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]])
    H_fb = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(scan_img, H_fb, (pw, ph),
                                  flags=cv2.INTER_CUBIC,
                                  borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(255, 255, 255))
    print(f'  axis-aligned fallback warp ({fallback_reason}): '
          f'{pw}x{ph}')
    result.update(status='OK', message=f'fallback: {fallback_reason}')
    return _save_warped(warped, sheet_jgw, out_dir, base,
                         save_intermediates, result,
                         method='AXIS_ALIGNED_FALLBACK', t_total=t_total)


def _passthrough(scan_img, scan_jpg, out_dir, base, result, t_total):
    """PDF-less passthrough — 스캔 원본 그대로 복사. JGW 없음.

    sheets_geo 가 없는 (admin, sheet) 일 때 호출. 워핑/georef 시도 안 함.
    이후 stage_extract_map 이 HSV 폴백으로 본문 영역 추출. JGW 부여는
    사용자가 QGIS 에서 수동 처리.
    """
    os.makedirs(out_dir, exist_ok=True)
    out_jpg = os.path.join(out_dir, f'{base}.jpg')
    _imwrite(out_jpg, scan_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    sh, sw = scan_img.shape[:2]
    result.update(
        status='PASSTHROUGH',
        message='PDF 없음 — 원본 복사 (stage_extract_map → 수동 georef)',
        output_size=[sw, sh],
        warped_jpg=out_jpg,
    )
    result['method'] = 'NO_PDF_PASSTHROUGH'
    result['elapsed'] = time.time() - t_total
    with open(os.path.join(out_dir, 'status.json'), 'w') as f:
        json.dump(result, f, indent=2)
    return result


def _save_status(out_dir, result, t_total, method):
    result['method'] = method
    result['elapsed'] = time.time() - t_total
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'status.json'), 'w') as f:
        json.dump(result, f, indent=2)
    return result


def match_and_warp(scan_jpg, admin_code, sheet_id, out_dir, sheet_cache,
                   target_ps=None, scan_scale=0.5,
                   save_intermediates=True, output_basename=None,
                   shp_path=None):
    """단일 스캔 처리 — scan ↔ sheet PDF 매칭 + 호모그래피 워핑.

    SIFT 매칭 실패 (희박 본문 등) 시 axis-aligned 4-corner 매핑 폴백.
    sheets_geo 자체가 없으면 (PDF-less) passthrough (원본 복사).
    """
    os.makedirs(out_dir, exist_ok=True)
    t_total = time.time()
    result = {
        'scan': scan_jpg, 'admin_code': admin_code, 'sheet_id': sheet_id,
        'status': 'OK', 'message': '',
    }

    # 1) 스캔 로드
    scan_img = _imread(scan_jpg)
    if scan_img is None:
        result.update(status='ERROR', message='scan 로드 실패')
        return result
    sh, sw = scan_img.shape[:2]

    # 2) sheet PDF SIFT (캐시) — None 이면 PDF-less → passthrough
    base = output_basename or f'{admin_code}_{sheet_id}'
    cached = sheet_cache.get(admin_code, sheet_id)
    if cached is None:
        return _passthrough(scan_img, scan_jpg, out_dir, base, result, t_total)
    g_sheet, kp_p, des_p, sheet_img, sheet_jgw = cached

    if save_intermediates:
        save_thumb(os.path.join(out_dir, '02_scan_raw.jpg'), scan_img)

    # 3) 전처리 (SIFT 경로용)
    g_scan = preprocess(scan_img, scale=scan_scale)
    if save_intermediates:
        _imwrite(os.path.join(out_dir, '03_scan_prep.jpg'), g_scan,
                 [cv2.IMWRITE_JPEG_QUALITY, 85])
    if target_ps is None:
        target_ps = abs(sheet_jgw.pixel_size_x)

    sift = cv2.SIFT_create(nfeatures=30000, contrastThreshold=0.025,
                           edgeThreshold=20, sigma=1.6)
    t = time.time()
    kp_s, des_s = sift.detectAndCompute(g_scan, None)
    print(f'  SIFT scan: {len(kp_s)} ({time.time()-t:.1f}s)')
    if des_s is None or len(kp_s) < 200:
        return _axis_aligned_fallback(
            scan_img, sheet_img, sheet_jgw, out_dir, base,
            save_intermediates, result, t_total,
            fallback_reason=f'스캔 키포인트 부족 ({len(kp_s)})')

    # 3) FLANN + Lowe ratio
    matcher = cv2.FlannBasedMatcher(
        {'algorithm': 1, 'trees': 5}, {'checks': 50})
    pairs = matcher.knnMatch(des_s, des_p, k=2)
    good = [m for m, n in pairs if m.distance < 0.75 * n.distance]
    result['n_good'] = len(good)
    print(f'  good matches: {len(good)}')
    if len(good) < 100:
        return _axis_aligned_fallback(
            scan_img, sheet_img, sheet_jgw, out_dir, base,
            save_intermediates, result, t_total,
            fallback_reason=f'good matches 부족 ({len(good)})')

    # 4) MAGSAC++ 호모그래피 (정규화 좌표계: scan 0.5x ↔ sheet 0.5x)
    src = np.float32([kp_s[m.queryIdx].pt for m in good])
    dst = np.float32([kp_p[m.trainIdx].pt for m in good])
    H, mask = cv2.findHomography(
        src, dst, cv2.USAC_MAGSAC, 3.0,
        maxIters=10000, confidence=0.9999)
    if H is None or mask is None:
        return _axis_aligned_fallback(
            scan_img, sheet_img, sheet_jgw, out_dir, base,
            save_intermediates, result, t_total,
            fallback_reason='호모그래피 추정 실패')
    inl = mask.ravel().astype(bool)
    n_inl = int(inl.sum())
    inlier_pct = n_inl / len(good)
    result['n_inliers'] = n_inl
    result['inlier_pct'] = inlier_pct
    print(f'  MAGSAC inliers: {n_inl}/{len(good)} ({100*inlier_pct:.1f}%)')
    if n_inl < 30 or inlier_pct < 0.05:
        return _axis_aligned_fallback(
            scan_img, sheet_img, sheet_jgw, out_dir, base,
            save_intermediates, result, t_total,
            fallback_reason=f'inliers 부족 ({n_inl}, {100*inlier_pct:.1f}%)')

    # 5) 행정리 폴리곤 안 inlier 만 선별 (정합 정확도 향상)
    #    sheet PDF 좌표계에 폴리곤 마스크를 만들어 dst 점 in/out 검사.
    #    in-polygon inlier 가 너무 적으면 (sparse admin / 매칭 실패 영역) 전체 inlier 폴백.
    #    SHP 로드 실패 (LFS 포인터, 권한 등) 시에도 정합은 계속 — 폴리곤 필터만 비활성.
    sheet_h, sheet_w = sheet_img.shape[:2]
    try:
        poly_mask = build_admin_polygon_mask(
            admin_code, sheet_jgw, (sheet_h, sheet_w), shp_path=shp_path)
    except Exception as e:
        print(f'  폴리곤 마스크 빌드 실패 ({e}) — 폴백')
        poly_mask = np.zeros((sheet_h, sheet_w), dtype=np.uint8)
    if poly_mask.any():
        # dst 는 0.5x 좌표 → 풀 해상도로 환산해 마스크 인덱싱
        dst_full = dst / scan_scale
        xs = np.clip(dst_full[:, 0].astype(int), 0, sheet_w - 1)
        ys = np.clip(dst_full[:, 1].astype(int), 0, sheet_h - 1)
        in_poly = poly_mask[ys, xs] > 0
        inl_in = inl & in_poly
        n_in = int(inl_in.sum())
        if n_in >= POLY_FILTER_MIN_INLIERS:
            inl = inl_in
            print(f'  폴리곤 필터: inlier {n_inl} → {n_in} '
                  f'({100*n_in/n_inl:.1f}% 보존, admin {admin_code})')
            n_inl = n_in
            result['n_inliers_in_polygon'] = n_inl
        else:
            print(f'  폴리곤 필터 폴백: in={n_in} < {POLY_FILTER_MIN_INLIERS}, '
                  f'전체 inlier 사용')
            result['n_inliers_in_polygon'] = n_in
            result['poly_filter_fallback'] = True
    else:
        print(f'  폴리곤 마스크 없음 (admin {admin_code} SHP 미존재) — 폴백')
        result['poly_filter_fallback'] = True

    # 6) 인라이어 시각화
    if save_intermediates:
        good_inl = [m for m, ok in zip(good, inl) if ok]
        vis = cv2.drawMatches(
            g_scan, kp_s, g_sheet, kp_p, good_inl[:300], None,
            flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS)
        save_thumb(os.path.join(out_dir, '04_matches_inliers.jpg'),
                   vis, max_dim=2400)

    # 7) 출력 frame = sheet PDF 그대로 사용
    # scan 4코너 호모그래피 외삽으로 bbox 계산하던 방식은 inlier 가 작은
    # 영역에 클러스터링된 케이스 (다도해 39010320_7-1 등) 에서 H 가 외삽 시
    # degenerate → bbox=0x0 발생. sheet PDF 본문 사이즈/JGW 를 그대로 사용하면
    # H 외삽 의존성 제거, Stage 4 mosaic 도 PDF 좌표계 기준이라 자연 호환.
    S = np.diag([scan_scale, scan_scale, 1.0])
    H_full = np.linalg.inv(S) @ H @ S   # scan(full) → sheet PDF px
    out_h, out_w = sheet_img.shape[:2]
    target_ps = abs(sheet_jgw.pixel_size_x)
    result['output_size'] = [out_w, out_h]
    out_minx = sheet_jgw.top_left_x
    out_maxy = sheet_jgw.top_left_y
    out_maxx = out_minx + out_w * sheet_jgw.pixel_size_x
    out_miny = out_maxy + out_h * sheet_jgw.pixel_size_y  # ps_y 음수
    result['world_bbox'] = [float(out_minx), float(out_miny),
                             float(out_maxx), float(out_maxy)]

    # 8) 단일 H 워핑 — 분할시트 한 장 안에선 종이 휨이 거의 선형 → 단일
    # 호모그래피가 TPS 보다 정확. TPS smoothing=0+400 GCP 는 GCP 사이에서
    # 진동(Runge) 으로 mean abs-diff 23→30 회귀 발생. 단일 H 로 회복.
    t = time.time()
    warped = cv2.warpPerspective(
        scan_img, H_full, (out_w, out_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    print(f'  단일 H 워핑: {time.time()-t:.1f}s')

    # 10) 저장
    result['target_ps'] = target_ps
    return _save_warped(warped, sheet_jgw, out_dir, base,
                         save_intermediates, result,
                         method='SIFT_MAGSAC_H', t_total=t_total)


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
    ap.add_argument('--shp', default=None,
                    help='행정리 폴리곤 SHP. 기본=패키지 data/bnd_adm_pg.shp. '
                         'inlier 를 폴리곤 안으로 한정해 정합 정확도 향상')
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
                if (row['status'] in ('OK', 'OK_NO_PDF')
                        and row['admin_code'] and row.get('sheet_id')):
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

        n_ok = n_pass = n_fail = 0
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
                    output_basename=label,
                    shp_path=args.shp)
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
                elif r['status'] == 'PASSTHROUGH':
                    n_pass += 1
                else:
                    n_fail += 1
            except Exception as e:
                w.writerow([scan, code, sid, 'ERROR',
                            '', '', '', '', '', str(e), '0'])
                n_fail += 1
                print(f'  ERROR: {e}')

    print(f'\n[Stage 3] 완료: OK={n_ok}, PASSTHROUGH={n_pass}, FAIL/ERROR={n_fail}')
    print(f'  결과: {csv_path}')


if __name__ == '__main__':
    main()
