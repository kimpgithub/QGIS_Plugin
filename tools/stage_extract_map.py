"""지도영역 추출 (참조 템플릿 매칭 기반).

Stage 2 식별이 끝난 scan ↔ sheets_geo의 참조 지도를 SIFT 매칭해
스캔 위의 지도영역을 찾아낸다. 결과는 raw 픽셀을 유지한 마스킹된 crop —
워핑(정합)은 후속 단계에서 수행.

알고리즘:
  1. SIFT 매칭 scan ↔ ref (Lowe ratio 0.75)
  2. MAGSAC++ 로 homography H 추정 (cv2.USAC_MAGSAC)
  3. ref 4변 dense 샘플링 (변당 100점, 모서리 2배 가중) → H 투영
     → scan 좌표계의 곡선 polygon
  4. 잔차 95th percentile > 20px 이면 TPS(RBF) 폴백 → 곡률 반영
  5. polygon 안쪽만 남기고 외부 흰색 마스크 + polygon bbox로 crop

입력 (Stage 2 산출):
  --identified  identified/{시도}/{시군구}/{admin}_{sheet}.jpg
  --sheets-geo  sheets_geo/{admin}_{sheet}.jpg  (+ .jgw, .prj)

출력:
  --out  {시도}/{시군구}/{admin}_{sheet}.jpg  + _status.csv
"""
import argparse
import csv
import os
import re
import sys
import time

import cv2
import numpy as np


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from ._legacy.common import (
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )
except ImportError:
    from gis_scan_tools.tools._legacy.common import (
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )


SCAN_TARGET_W = 1600    # SIFT 추출 전 다운스케일 폭 (정확도/속도 절충)
REF_TARGET_W = 1600
N_SAMPLES_PER_SIDE = 100
CORNER_DENSITY_MULT = 2  # 모서리 근처 샘플 밀도 2배
MIN_INLIERS = 30
TPS_FALLBACK_RESIDUAL_PCTILE = 95
TPS_FALLBACK_THRESHOLD_PX = 20   # 위 퍼센타일이 이 값 초과 시 TPS 폴백


FILENAME_PAT = re.compile(
    r'^(\d{8})_(\d+-\d+)(?:_\d+)?\.(jpg|jpeg|png|JPG|JPEG|PNG)$')


def _discover_identified(identified_dir):
    """{시도}/{시군구}/{admin}_{sheet}.{jpg|png} 재귀 스캔."""
    targets = []
    for root, _, files in os.walk(identified_dir):
        for f in sorted(files):
            m = FILENAME_PAT.match(f)
            if not m:
                continue
            targets.append((os.path.join(root, f), m.group(1), m.group(2)))
    return targets


def _sift_preprocess(img, target_w):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = g.shape
    s = target_w / w if w > target_w else 1.0
    if s != 1.0:
        g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16, 16))
    g = clahe.apply(g)
    return g, s


def _compute_sift(img, target_w=1600, nfeatures=5000):
    g, s = _sift_preprocess(img, target_w)
    sift = cv2.SIFT_create(nfeatures=nfeatures, contrastThreshold=0.04)
    kp, des = sift.detectAndCompute(g, None)
    return kp, des, s


def _boundary_sample_points(w, h, n_per_side=100, corner_mult=2):
    """ref 이미지의 4변을 dense 샘플링 — 모서리 근처 2배 가중.

    변당 n_per_side 점이지만 t∈[0,1] 균등이 아니라 모서리 쏠림(cosine).
    반환: (N, 2) float32 — ref 좌표계 점들.
    """
    # cosine-weighted t: 모서리(t=0,1) 근처 밀집
    i = np.arange(n_per_side)
    # 기본 균등
    t_uniform = i / (n_per_side - 1)
    # 모서리 가중: t_new = (t + sin(2πt)/(2π)) — symmetric, endpoints denser
    t_corner = t_uniform - np.sin(2 * np.pi * t_uniform) / (2 * np.pi * corner_mult)
    t = np.clip(t_corner, 0, 1)

    pts = []
    # top: (0..w, 0)
    pts += [(x, 0) for x in t * w]
    # right: (w, 0..h)
    pts += [(w, y) for y in t * h]
    # bottom: (w..0, h)
    pts += [(x, h) for x in (1 - t) * w]
    # left: (0, h..0)
    pts += [(0, y) for y in (1 - t) * h]
    return np.array(pts, dtype=np.float32)


def _rbf_warp(src_pts, dst_pts, query_pts, smoothing=0.0):
    """TPS(=RBF with thin_plate_spline kernel) 로 src→dst 함수 학습 후 query 변환."""
    from scipy.interpolate import RBFInterpolator
    rbf_x = RBFInterpolator(src_pts, dst_pts[:, 0],
                            kernel='thin_plate_spline', smoothing=smoothing)
    rbf_y = RBFInterpolator(src_pts, dst_pts[:, 1],
                            kernel='thin_plate_spline', smoothing=smoothing)
    out = np.empty_like(query_pts)
    out[:, 0] = rbf_x(query_pts)
    out[:, 1] = rbf_y(query_pts)
    return out


def extract_map_from_scan(scan_img, ref_img):
    """scan에서 ref 지도영역 polygon 추출.

    Returns:
        dict {
            'polygon': (N,2) float32  — scan 좌표계 dense polygon
            'H': (3,3)                — homography ref→scan (다운스케일 기준)
            'method': 'homography' | 'tps'
            'inliers': int
            'good_matches': int
            'residual_95': float      — homography 잔차 (95th pctile, px)
            'ref_shape': (H, W)
        }  또는 None (매칭 실패)
    """
    rh, rw = ref_img.shape[:2]
    sh, sw = scan_img.shape[:2]

    kp_r, des_r, sc_r = _compute_sift(ref_img, REF_TARGET_W)
    kp_s, des_s, sc_s = _compute_sift(scan_img, SCAN_TARGET_W)
    if des_r is None or des_s is None or len(des_r) < 50 or len(des_s) < 50:
        return None

    flann = cv2.FlannBasedMatcher(
        {'algorithm': 1, 'trees': 4}, {'checks': 32})
    pairs = flann.knnMatch(des_r, des_s, k=2)
    good = [m for m, n in pairs
            if len(pairs[0]) == 2 and m.distance < 0.75 * n.distance]
    if len(good) < MIN_INLIERS:
        return None

    src_small = np.float32([kp_r[m.queryIdx].pt for m in good])
    dst_small = np.float32([kp_s[m.trainIdx].pt for m in good])

    # MAGSAC++ for robustness
    H_small, mask = cv2.findHomography(
        src_small, dst_small, cv2.USAC_MAGSAC, 5.0)
    if H_small is None or mask is None:
        return None
    inl = mask.ravel().astype(bool)
    n_inl = int(inl.sum())
    if n_inl < MIN_INLIERS:
        return None

    # 다운스케일 H → 풀 해상도 H (ref_full → scan_full)
    S_r = np.diag([sc_r, sc_r, 1.0])
    S_s_inv = np.diag([1.0 / sc_s, 1.0 / sc_s, 1.0])
    H_full = S_s_inv @ H_small @ S_r

    # 잔차 측정 (다운스케일 기준, 픽셀 단위는 scan 다운스케일 스케일)
    # 풀 해상도로 환산: residual_full = residual_small / sc_s
    src_small_h = np.hstack([src_small[inl], np.ones((n_inl, 1))])
    proj = (H_small @ src_small_h.T).T
    proj = proj[:, :2] / proj[:, 2:3]
    res_small = np.linalg.norm(proj - dst_small[inl], axis=1)
    res_full = res_small / sc_s
    residual_95 = float(np.percentile(res_full, 95))

    # dense polygon 샘플
    ref_boundary = _boundary_sample_points(
        rw, rh, N_SAMPLES_PER_SIDE, CORNER_DENSITY_MULT)

    method = 'homography'
    if residual_95 > TPS_FALLBACK_THRESHOLD_PX:
        # TPS 폴백: inlier pair 로 학습, ref boundary를 scan으로 투영
        src_full = src_small[inl] / sc_r
        dst_full = dst_small[inl] / sc_s
        try:
            polygon = _rbf_warp(src_full, dst_full, ref_boundary, smoothing=0.0)
            method = 'tps'
        except Exception:
            # TPS 실패 시 homography 사용
            boundary_h = np.hstack(
                [ref_boundary, np.ones((len(ref_boundary), 1))])
            p = (H_full @ boundary_h.T).T
            polygon = p[:, :2] / p[:, 2:3]
    else:
        boundary_h = np.hstack(
            [ref_boundary, np.ones((len(ref_boundary), 1))])
        p = (H_full @ boundary_h.T).T
        polygon = p[:, :2] / p[:, 2:3]

    return {
        'polygon': polygon.astype(np.float32),
        'H': H_full,
        'method': method,
        'inliers': n_inl,
        'good_matches': len(good),
        'residual_95': residual_95,
        'ref_shape': (rh, rw),
    }


def apply_polygon_crop(scan_img, polygon):
    """polygon 외부 흰색 마스크 + polygon bbox로 crop.

    Returns:
        (cropped_img, bbox) — bbox = (x0, y0, x1, y1)
    """
    sh, sw = scan_img.shape[:2]
    pts = polygon.astype(np.int32).reshape(-1, 1, 2)

    # 마스크 (polygon 내부 255, 외부 0)
    mask = np.zeros((sh, sw), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)

    # 외부를 흰색으로
    masked = scan_img.copy()
    masked[mask == 0] = 255

    # bbox
    xs, ys = polygon[:, 0], polygon[:, 1]
    x0 = int(max(0, np.floor(xs.min())))
    x1 = int(min(sw, np.ceil(xs.max())))
    y0 = int(max(0, np.floor(ys.min())))
    y1 = int(min(sh, np.ceil(ys.max())))
    return masked[y0:y1, x0:x1], (x0, y0, x1, y1)


def main():
    ap = argparse.ArgumentParser(
        description='지도영역 추출 (scan ↔ 참조 템플릿 매칭)')
    ap.add_argument('--identified', required=True,
                    help='Stage 2 산출 identified/ 폴더')
    ap.add_argument('--sheets-geo', required=True,
                    help='Stage 2 산출 sheets_geo/ 폴더 (참조 템플릿)')
    ap.add_argument('--out', dest='out_dir', required=True)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    targets = _discover_identified(args.identified)
    print(f'[지도영역 추출] {len(targets)}장 처리 시작')

    # ref SIFT 캐시 (같은 (admin, sheet) 반복 시)
    ref_cache = {}

    csv_path = os.path.join(args.out_dir, '_status.csv')
    n_ok = n_err = 0
    t_total = time.time()

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scan_path', 'output', 'status', 'admin_code', 'sheet_id',
                    'method', 'inliers', 'good_matches', 'residual_95_px',
                    'orig_size', 'crop_size', 'message', 'elapsed_s'])

        for i, (scan_path, admin, sid) in enumerate(targets, 1):
            t0 = time.time()

            ref_path = os.path.join(args.sheets_geo, f'{admin}_{sid}.jpg')
            if not os.path.exists(ref_path):
                w.writerow([scan_path, '', 'ERROR', admin, sid, '', 0, 0, 0,
                            '', '', f'ref 없음: {ref_path}',
                            f'{time.time()-t0:.2f}'])
                n_err += 1
                continue

            scan = _imread(scan_path)
            if scan is None:
                w.writerow([scan_path, '', 'ERROR', admin, sid, '', 0, 0, 0,
                            '', '', 'scan imread 실패',
                            f'{time.time()-t0:.2f}'])
                n_err += 1
                continue

            key = (admin, sid)
            if key not in ref_cache:
                ref_cache[key] = _imread(ref_path)
            ref = ref_cache[key]

            result = extract_map_from_scan(scan, ref)
            if result is None:
                w.writerow([scan_path, '', 'FAIL', admin, sid, '', 0, 0, 0,
                            f'{scan.shape[1]}x{scan.shape[0]}', '',
                            'SIFT 매칭 실패 (inliers < 30)',
                            f'{time.time()-t0:.2f}'])
                n_err += 1
                continue

            cropped, bbox = apply_polygon_crop(scan, result['polygon'])
            # 출력 경로 — 입력 상대 경로 보존
            rel = os.path.relpath(scan_path, args.identified)
            out_path = os.path.join(args.out_dir, rel)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            _imwrite(out_path, cropped, [cv2.IMWRITE_JPEG_QUALITY, 92])

            w.writerow([
                scan_path, out_path, 'OK', admin, sid,
                result['method'], result['inliers'], result['good_matches'],
                f"{result['residual_95']:.2f}",
                f'{scan.shape[1]}x{scan.shape[0]}',
                f'{cropped.shape[1]}x{cropped.shape[0]}',
                '', f'{time.time()-t0:.2f}'])
            n_ok += 1

            if i % 5 == 0 or i == len(targets):
                print(f'  [{i}/{len(targets)}] OK={n_ok} ERR={n_err} '
                      f'({(time.time()-t_total)/i:.1f}s/장)')

    print(f'\n[지도영역 추출] 완료: OK={n_ok}, ERROR={n_err}')
    print(f'  결과: {csv_path}')


if __name__ == '__main__':
    main()
