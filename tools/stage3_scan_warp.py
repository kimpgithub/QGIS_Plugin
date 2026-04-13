"""Stage 3: 스캔 ↔ 메인 PDF SIFT 매칭 + 호모그래피 워핑

Stage 2의 식별 결과(_identification.csv)를 받아, 각 스캔을 해당 PDF에 매칭하고
세계좌표계로 워핑한 결과를 저장.

핵심:
- main 이미지의 지도영역만 SIFT 대상으로 (범례 매칭 차단)
- 두 이미지를 동일 물리 해상도(메인 ps)로 정규화 후 매칭
- 메인 SIFT는 1회만 계산 후 캐시 (행정코드별)
- 호모그래피 + cv2.warpPerspective로 직접 세계좌표 raster 생성

CLI:
  python -m gis_scan_tools.tools.stage3_scan_warp \\
      --identification scan_identified/_identification.csv \\
      --pdf-main pdf_main_geo/ --out warped/
"""
import argparse
import csv
import json
import os
import sys
import time

import cv2
import numpy as np


def _imread(path):
    """Unicode 경로 안전 imread."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _imwrite(path, img, params=None):
    ext = os.path.splitext(path)[1] or '.jpg'
    ok, buf = cv2.imencode(ext, img, params or [])
    if not ok:
        return False
    buf.tofile(path)
    return True


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from gis_scan_tools.tools._legacy.common import (
        parse_jgw, write_jgw, JGWParams, PRJ_5179, extract_map_region,
    )
except ImportError:
    from .common import (
        parse_jgw, write_jgw, JGWParams, PRJ_5179, extract_map_region,
    )


def preprocess(img, scale=0.5):
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
# 메인 SIFT 캐시 (행정코드별 1회)
# ============================================================

class MainSiftCache:
    def __init__(self, nfeatures=80000, contrast=0.025, edge=20):
        self.cache = {}  # admin_code → (g_main, kp, des, main_bbox, main_jgw)
        self.sift_params = dict(
            nfeatures=nfeatures, contrastThreshold=contrast,
            edgeThreshold=edge, sigma=1.6)

    def get(self, admin_code, pdf_jpg, pdf_jgw_path):
        if admin_code in self.cache:
            return self.cache[admin_code]
        main_img = _imread(pdf_jpg)
        if main_img is None:
            raise RuntimeError(f'PDF 이미지 로드 실패: {pdf_jpg}')
        main_jgw = parse_jgw(pdf_jgw_path)
        _, main_bbox = extract_map_region(main_img)
        mbx, mby, mbw, mbh = main_bbox
        main_map = main_img[mby:mby + mbh, mbx:mbx + mbw]
        g_main = preprocess(main_map, scale=1.0)
        sift = cv2.SIFT_create(**self.sift_params)
        t = time.time()
        kp, des = sift.detectAndCompute(g_main, None)
        print(f'  [메인 SIFT 1회] {admin_code}: {len(kp)}개 ({time.time()-t:.1f}s)')
        self.cache[admin_code] = (g_main, kp, des, main_bbox, main_jgw)
        return self.cache[admin_code]


# ============================================================
# 매칭 + 워핑
# ============================================================

def match_and_warp(scan_jpg, admin_code, pdf_jpg, pdf_jgw_path,
                   out_dir, cache, target_ps=None,
                   scan_scale=0.5, save_intermediates=True,
                   output_basename=None):
    """단일 스캔 처리. 결과 dict 반환.

    Args:
        target_ps: 출력 픽셀크기. None이면 메인 ps의 절반(=스캔 native).
    """
    os.makedirs(out_dir, exist_ok=True)
    t_total = time.time()
    result = {
        'scan': scan_jpg, 'admin_code': admin_code,
        'status': 'OK', 'message': '',
    }

    # 1) 입력 + 메인 SIFT 캐시
    scan_img = _imread(scan_jpg)
    if scan_img is None:
        result.update(status='ERROR', message='scan 로드 실패')
        return result
    g_main, kp_main, des_main, main_bbox, main_jgw = cache.get(
        admin_code, pdf_jpg, pdf_jgw_path)
    mbx, mby, _, _ = main_bbox
    sh, sw = scan_img.shape[:2]
    if target_ps is None:
        target_ps = abs(main_jgw.pixel_size_x) / 2.0  # 스캔 native (절반)

    if save_intermediates:
        save_thumb(os.path.join(out_dir, '02_scan_raw.jpg'), scan_img)

    # 2) 스캔 SIFT
    g_scan = preprocess(scan_img, scale=scan_scale)
    if save_intermediates:
        _imwrite(os.path.join(out_dir, '03_scan_prep.jpg'), g_scan,
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
    sift = cv2.SIFT_create(nfeatures=50000, contrastThreshold=0.025,
                           edgeThreshold=20, sigma=1.6)
    t = time.time()
    kp1, des1 = sift.detectAndCompute(g_scan, None)
    result['scan_kp'] = len(kp1)
    result['main_kp'] = len(kp_main)
    print(f'  SIFT scan: {len(kp1)} ({time.time()-t:.1f}s)')

    if des1 is None or len(kp1) < 200:
        result.update(status='FAIL', message=f'스캔 키포인트 부족: {len(kp1)}')
        return result

    # 3) FLANN + Lowe ratio
    matcher = cv2.FlannBasedMatcher({'algorithm': 1, 'trees': 5}, {'checks': 50})
    pairs = matcher.knnMatch(des1, des_main, k=2)
    good = [m for m, n in pairs if m.distance < 0.75 * n.distance]
    result['n_good'] = len(good)
    print(f'  good matches: {len(good)}')
    if len(good) < 100:
        result.update(status='FAIL', message=f'good matches 부족: {len(good)}')
        return result

    # 4) MAGSAC++ 호모그래피
    src = np.float32([kp1[m.queryIdx].pt for m in good])
    dst = np.float32([kp_main[m.trainIdx].pt for m in good])
    H, mask = cv2.findHomography(
        src, dst, cv2.USAC_MAGSAC, 3.0, maxIters=10000, confidence=0.9999)
    if H is None or mask is None:
        result.update(status='FAIL', message='호모그래피 추정 실패')
        return result
    inl = mask.ravel().astype(bool)
    n_inl = int(inl.sum())
    cond = float(np.linalg.cond(H))
    result['n_inliers'] = n_inl
    result['cond_H'] = cond
    print(f'  MAGSAC inliers: {n_inl}/{len(good)} ({100*n_inl/len(good):.1f}%) cond={cond:.1e}')
    if n_inl < 30 or cond > 1e10:
        result.update(status='FAIL',
                      message=f'정합 불안정: inliers={n_inl}, cond(H)={cond:.1e}')
        return result

    # 4b) inlier 시각화
    if save_intermediates:
        good_inl = [m for m, ok in zip(good, inl) if ok]
        vis = cv2.drawMatches(g_scan, kp1, g_main, kp_main, good_inl[:300], None,
                              flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS)
        save_thumb(os.path.join(out_dir, '04_matches_inliers.jpg'), vis,
                   max_dim=2400)

    # 5) H를 원본 해상도(scan_full → main_full)로 변환
    Tin = np.diag([scan_scale, scan_scale, 1.0])
    Tout = np.array([[1.0, 0, mbx], [0, 1.0, mby], [0, 0, 1]], np.float64)
    H_full = Tout @ H @ Tin

    # 잔차 평가
    scan_full = src[inl] / scan_scale
    main_full = dst[inl] + np.array([mbx, mby])
    proj = cv2.perspectiveTransform(
        scan_full.reshape(-1, 1, 2), H_full).reshape(-1, 2)
    resid = np.linalg.norm(proj - main_full, axis=1)
    main_ps = abs(main_jgw.pixel_size_x)
    result['residual_main_px'] = {
        'median': float(np.median(resid)),
        'max': float(resid.max()),
    }
    result['residual_world_m'] = {
        'median': float(np.median(resid)) * main_ps,
        'max': float(resid.max()) * main_ps,
    }
    print(f'  잔차: med={result["residual_world_m"]["median"]:.2f}m, '
          f'max={result["residual_world_m"]["max"]:.2f}m')

    # 6) 출력 raster bbox 계산 (scan 4 corners → main → world)
    corners_scan = np.float32([[0, 0], [sw, 0], [sw, sh], [0, sh]]).reshape(-1, 1, 2)
    corners_main = cv2.perspectiveTransform(corners_scan, H_full).reshape(-1, 2)
    cw_x = main_jgw.top_left_x + corners_main[:, 0] * main_jgw.pixel_size_x
    cw_y = main_jgw.top_left_y + corners_main[:, 1] * main_jgw.pixel_size_y
    out_minx, out_maxx = float(cw_x.min()), float(cw_x.max())
    out_miny, out_maxy = float(cw_y.min()), float(cw_y.max())
    out_w = int(np.ceil((out_maxx - out_minx) / target_ps))
    out_h = int(np.ceil((out_maxy - out_miny) / target_ps))
    result['output_size'] = [out_w, out_h]
    result['world_bbox'] = [out_minx, out_miny, out_maxx, out_maxy]

    if out_w <= 0 or out_h <= 0:
        result.update(status='FAIL', message=f'출력 크기 비정상: {out_w}x{out_h}')
        return result

    # 7) 합성 변환: output_pixel → scan_pixel
    A_ow = np.array([[target_ps, 0, out_minx],
                     [0, -target_ps, out_maxy],
                     [0, 0, 1]], np.float64)
    A_wm = np.array([[1.0 / main_jgw.pixel_size_x, 0,
                      -main_jgw.top_left_x / main_jgw.pixel_size_x],
                     [0, 1.0 / main_jgw.pixel_size_y,
                      -main_jgw.top_left_y / main_jgw.pixel_size_y],
                     [0, 0, 1]], np.float64)
    M = np.linalg.inv(H_full) @ A_wm @ A_ow

    # 8) warpPerspective
    t = time.time()
    warped = cv2.warpPerspective(
        scan_img, M, (out_w, out_h),
        flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    print(f'  warpPerspective: {time.time()-t:.1f}s')

    # 9) 저장 (파일명: output_basename.{jpg,jgw,prj}, 기본 'warped_scan')
    base = output_basename or 'warped_scan'
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

def main():
    ap = argparse.ArgumentParser(description='Stage 3: 스캔 매칭 + 워핑')
    ap.add_argument('--identification', required=True,
                    help='Stage 2 산출 _identification.csv')
    ap.add_argument('--pdf-main', required=True,
                    help='Stage 1 산출 폴더 (pdf_main_geo)')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--no-intermediates', action='store_true',
                    help='중간 시각화 파일 저장 안 함 (속도)')
    ap.add_argument('--target-ps', type=float, default=None,
                    help='출력 픽셀크기 (m/px). 기본=메인 ps의 절반')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    # 식별 결과 로드 (OK인 것만)
    targets = []
    with open(args.identification, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['status'] == 'OK' and row['admin_code']:
                targets.append((row['scan_path'], row['admin_code'],
                                row.get('sheet_id', '')))
    print(f'[Stage 3] 처리 대상 {len(targets)}장')

    cache = MainSiftCache()

    csv_path = os.path.join(args.out_dir, '_status.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scan_path', 'admin_code', 'status',
                    'n_inliers', 'residual_med_m', 'residual_max_m',
                    'output_w', 'output_h', 'message', 'elapsed_s'])

        n_ok = n_fail = 0
        for i, (scan, code, sheet_id) in enumerate(targets, 1):
            label = f'{code}_{sheet_id}' if sheet_id else code
            print(f'\n[{i}/{len(targets)}] {label} | {os.path.basename(scan)}')
            pdf_jpg = os.path.join(args.pdf_main, f'{code}.jpg')
            pdf_jgw = os.path.join(args.pdf_main, f'{code}.jgw')
            if not os.path.exists(pdf_jpg) or not os.path.exists(pdf_jgw):
                w.writerow([scan, code, 'ERROR', '', '', '', '', '',
                            f'PDF 메인 없음: {pdf_jpg}', '0'])
                n_fail += 1
                continue

            # 출력 디렉토리: out/{code}/{code}_{sheet_id}/
            sub_name = f'{code}_{sheet_id}' if sheet_id else \
                os.path.splitext(os.path.basename(scan))[0]
            sub_out = os.path.join(args.out_dir, code, sub_name)

            try:
                r = match_and_warp(
                    scan, code, pdf_jpg, pdf_jgw, sub_out, cache,
                    target_ps=args.target_ps,
                    save_intermediates=not args.no_intermediates,
                    output_basename=sub_name)
                resw = r.get('residual_world_m', {})
                osz = r.get('output_size', [0, 0])
                w.writerow([
                    scan, code, r['status'], r.get('n_inliers', ''),
                    f'{resw.get("median", 0):.2f}',
                    f'{resw.get("max", 0):.2f}',
                    osz[0], osz[1], r.get('message', ''),
                    f'{r.get("elapsed", 0):.1f}',
                ])
                if r['status'] == 'OK':
                    n_ok += 1
                else:
                    n_fail += 1
            except Exception as e:
                w.writerow([scan, code, 'ERROR', '', '', '', '', '',
                            str(e), '0'])
                n_fail += 1
                print(f'  ERROR: {e}')

    print(f'\n[Stage 3] 완료: OK={n_ok}, FAIL/ERROR={n_fail}')
    print(f'  결과: {csv_path}')


if __name__ == '__main__':
    main()
