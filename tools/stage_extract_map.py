"""지도영역 추출 — ORB 매칭 기본, HSV 폴백.

기본 흐름 (--sheet-cache 지정 시):
  1. _sheet_cache/{admin}_{sheet}.body.jpg (Stage 2 가 저장한 PDF 본문 800px
     템플릿) 로드.
  2. scan 800px 다운스케일 → ORB detect+compute 양쪽.
  3. BFMatcher cross-check + RANSAC homography.
  4. PDF body 4 코너 → scan 좌표 perspective transform → axis-aligned bbox.
  5. 원본 해상도 환산 후 크롭.
  스캔 회전·skew 강건. 시트당 ~0.5~1s. 인라이어 부족 시 HSV 폴백.

폴백 — HSV "어두운 무채색" 게이트 (참조 PDF 불필요):
  1) (S < max_saturation) AND (V ≤ v_max) 픽셀 마스크
  2) 행/열 프로파일 임계 이상 군집 = 프레임 라인
  3) 위쪽/아래쪽 header_zone 안 가장 가까운 라인 = map_top/map_bot
     col 첫/마지막 라인 = 좌/우 외곽
  4) 검출선보다 inset px 안쪽으로 자름

종횡비 가드 (--sheet-bboxes 지정 시):
  Stage 2 산출 sheet_bboxes.json 의 world bbox 종횡비와 검출 본문 종횡비
  비교. 편차가 임계(--aspect-tol) 초과로 좁게 잘렸으면 마진 큰 쪽으로 확장.
  HSV 폴백 시 신뢰성 보강용.

입력:
  --identified   Stage 2 산출 identified/{시도}/{시군구}/{admin}_{sheet}.jpg
  --sheet-cache  Stage 2 _sheet_cache/ — body 템플릿 (.body.jpg) 로드용

출력:
  --out  {시도}/{시군구}/{admin}_{sheet}.jpg  + _status.csv
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
        extract_map_region_scan,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )
except ImportError:
    from gis_scan_tools.tools.common import (
        extract_map_region_scan,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )


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


ORB_SCAN_W = 800           # ORB 매칭용 스캔 다운스케일 폭
ORB_NFEATURES = 5000
ORB_MIN_INLIERS = 30
ORB_RANSAC_THR = 5.0
# Post-warp trim 파라미터 (ORB 코너 외삽 오차 보정)
TRIM_LABEL_QUADRANT = 0.20       # 좌상단 분석 영역 비율 (이 안에서 라벨 검출)
TRIM_LABEL_V_MAX = 100           # 검은 잉크 임계 (밝기)
TRIM_LABEL_S_MAX = 60            # 검은 잉크 임계 (채도)
TRIM_LABEL_EROSION_FRAC = 0.001  # warped 폭 대비 erosion 커널 (라벨 두께 필터)
TRIM_LABEL_MIN_AREA_FRAC = 1e-5  # 검출 글리프 최소 면적 (warped 전체 대비)


def _trim_via_label(warped):
    """좌상단 시트라벨 (예: '4-1') 글리프를 anchor 로 본문 좌상단 코너 정밀 검출.

    PDF body 템플릿은 본문 좌상단이 이미지 (0,0). 시트라벨은 그 코너 바로
    안쪽 (~수십 px) 에 위치하는 굵은 검정 글리프. 두께 필터 + 가장 큰
    connected component 로 라벨 위치를 robust 하게 검출.

    Returns:
        (label_top, label_left, label_h, label_w) — 검출 성공
        None — 실패 (라벨 미검출)
    """
    h, w = warped.shape[:2]
    qh = int(h * TRIM_LABEL_QUADRANT)
    qw = int(w * TRIM_LABEL_QUADRANT)
    region = warped[:qh, :qw]
    if region.ndim == 3:
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2]; sat = hsv[:, :, 1]
        black = ((v <= TRIM_LABEL_V_MAX) &
                 (sat <= TRIM_LABEL_S_MAX)).astype(np.uint8) * 255
    else:
        black = (region <= TRIM_LABEL_V_MAX).astype(np.uint8) * 255
    er = max(5, int(w * TRIM_LABEL_EROSION_FRAC))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                   (er * 2 + 1, er * 2 + 1))
    thick = cv2.morphologyEx(black, cv2.MORPH_OPEN, k)
    n, _, stats, _ = cv2.connectedComponentsWithStats(thick)
    if n <= 1:
        return None
    min_area = int(qh * qw * TRIM_LABEL_MIN_AREA_FRAC)
    cands = [i for i in range(1, n)
             if stats[i, cv2.CC_STAT_AREA] >= min_area]
    if not cands:
        return None
    # 라벨은 'N-i' 등 다중 글리프로 분리됨 (예: '4', '-', '3' 3개 CC).
    # 가장 큰 CC (앵커, 보통 'N' 또는 'i') 의 행 위치를 기준으로 같은 줄에
    # 있는 CC 들 union → 라벨 전체 bbox.
    anchor = max(cands, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    a_top = stats[anchor, cv2.CC_STAT_TOP]
    a_h = stats[anchor, cv2.CC_STAT_HEIGHT]
    a_cy = a_top + a_h / 2
    # 같은 행 (수직 중심이 anchor 글리프 높이 범위 안) + 면적 anchor 의 10% 이상
    same_row = []
    for i in cands:
        cy = stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT] / 2
        if abs(cy - a_cy) <= a_h * 0.6 and \
           stats[i, cv2.CC_STAT_AREA] >= stats[anchor, cv2.CC_STAT_AREA] * 0.1:
            same_row.append(i)
    if not same_row:
        same_row = [anchor]
    # union bbox
    label_top = min(stats[i, cv2.CC_STAT_TOP] for i in same_row)
    label_left = min(stats[i, cv2.CC_STAT_LEFT] for i in same_row)
    label_bot = max(stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT]
                     for i in same_row)
    label_right = max(stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH]
                       for i in same_row)
    return (int(label_top), int(label_left),
            int(label_bot - label_top), int(label_right - label_left))


def _trim_to_frame(warped):
    """라벨 anchor 기반 좌·상 trim. (라벨이 본문 좌상단에 위치한다는 사전 정보 활용)

    1. 라벨 검출 → (label_top, label_left)
    2. 본문 좌상단 코너 = 라벨 좌상단 (라벨이 본문 안쪽 시작점이라 가정)
    3. warped 를 (label_top, label_left) 에서 시작하도록 trim
    4. 우·하단은 미수정 (라벨 anchor 없음, ORB warp 결과 그대로)
    """
    label = _trim_via_label(warped)
    if label is None:
        return warped, None
    label_top, label_left, label_h, label_w = label
    # 라벨은 본문 좌상단 코너에서 동일 거리만큼 떨어져 위치 (사용자 측정 25%).
    # 검출되는 CC 가 단일 글자('4')라 width < height 이므로 양 방향 동일하게
    # label_h 기준으로 margin 산출.
    margin = int(label_h * 0.25)
    top = max(0, label_top - margin)
    left = max(0, label_left - margin)
    h, w = warped.shape[:2]
    return warped[top:, left:], (top, h, left, w)


def orb_extract_body(scan, body_template_path):
    """ORB 매칭으로 scan 내 PDF body 영역 검출 + perspective warp.

    PDF body 4 코너를 scan 좌표로 projection 한 quadrilateral 을 그대로
    perspective warp 의 src 로 사용 → 기울어진 스캔도 deskew + crop 동시.
    출력 크기는 4 변 길이 평균으로 결정 (원본 해상도 보존에 가깝게).

    Returns:
        (warped_img, axis_bbox_xywh, n_inliers, output_aspect, proj_quad)
            — 성공. proj_quad: 원본 해상도 4 코너 (디버그 시각화용)
        None — 실패 (템플릿 없음/매칭 부족/homography 실패)
    """
    body = cv2.imread(body_template_path, cv2.IMREAD_GRAYSCALE)
    if body is None:
        return None
    th, tw = body.shape

    sh, sw = scan.shape[:2]
    sc = ORB_SCAN_W / sw
    scan_s = cv2.resize(scan, None, fx=sc, fy=sc,
                        interpolation=cv2.INTER_AREA)
    scan_g = (cv2.cvtColor(scan_s, cv2.COLOR_BGR2GRAY)
              if scan_s.ndim == 3 else scan_s)

    orb = cv2.ORB_create(nfeatures=ORB_NFEATURES)
    kp1, des1 = orb.detectAndCompute(body, None)
    kp2, des2 = orb.detectAndCompute(scan_g, None)
    if des1 is None or des2 is None or len(kp1) < ORB_MIN_INLIERS:
        return None

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(des1, des2)
    if len(matches) < ORB_MIN_INLIERS:
        return None

    src = np.float32([kp1[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, ORB_RANSAC_THR)
    if H is None:
        return None
    inliers = int(mask.sum())
    if inliers < ORB_MIN_INLIERS:
        return None

    # PDF body 4 코너 → scan 다운스케일 좌표 → 원본 해상도 환산
    corners = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]).reshape(-1, 1, 2)
    proj = cv2.perspectiveTransform(corners, H).reshape(-1, 2) / sc
    # axis-aligned bbox (status 기록용)
    minx, miny = proj.min(axis=0)
    maxx, maxy = proj.max(axis=0)
    ax_bbox = (max(0, int(minx)), max(0, int(miny)),
               min(sw, int(maxx)) - max(0, int(minx)),
               min(sh, int(maxy)) - max(0, int(miny)))

    # 출력 크기 = 4 변 길이 평균
    p_tl, p_tr, p_br, p_bl = proj
    width = (np.linalg.norm(p_tr - p_tl) +
             np.linalg.norm(p_br - p_bl)) / 2
    height = (np.linalg.norm(p_bl - p_tl) +
              np.linalg.norm(p_br - p_tr)) / 2
    out_w = int(round(width))
    out_h = int(round(height))
    if out_w <= 0 or out_h <= 0:
        return None

    src_quad = proj.astype(np.float32)
    dst_quad = np.float32([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]])
    M = cv2.getPerspectiveTransform(src_quad, dst_quad)
    warped = cv2.warpPerspective(scan, M, (out_w, out_h),
                                  flags=cv2.INTER_LINEAR)

    # post-warp trim — frame 라인까지 정밀 보정 (코너 외삽 오차 흡수)
    warped, _ = _trim_to_frame(warped)
    aspect = warped.shape[1] / warped.shape[0]
    return warped, ax_bbox, inliers, aspect, proj


def _expected_aspect(sheet_bboxes, admin, sid):
    """sheet_bboxes.json 에서 (admin, sid) 의 world bbox 종횡비 반환. 없으면 None."""
    try:
        bbox = sheet_bboxes.get(admin, {}).get(sid)
        if not bbox:
            return None
        minx, miny, maxx, maxy = bbox
        h = maxy - miny
        if h <= 0:
            return None
        return (maxx - minx) / h
    except Exception:
        return None


def _aspect_guard(scan_shape, bbox, expected_aspect, tol=0.03):
    """검출 bbox 종횡비를 expected 와 비교, 좁게 잘렸으면 마진 큰 쪽으로 확장.

    Returns: (corrected_bbox_xywh or None, deviation, applied_axis)
        applied_axis: 'w'/'h'/'' (확장한 축; '' = 보정 안함)
    """
    sh, sw = scan_shape[:2]
    x, y, w, h = bbox
    detected_aspect = w / h if h > 0 else 0
    if expected_aspect is None or detected_aspect <= 0:
        return None, 0.0, ''
    deviation = (detected_aspect - expected_aspect) / expected_aspect
    if abs(deviation) <= tol:
        return None, deviation, ''
    # 좁게 잘림 (deviation < -tol) → 폭 부족, 마진 큰 쪽 확장
    # 길게 잘림 (deviation > +tol) → 높이 부족, 마진 큰 쪽 확장
    if deviation < 0:
        # w / h 가 작음 → w 부족 (또는 h 과대) — 가용 공간 보고 w 확장
        target_w = int(round(h * expected_aspect))
        short = target_w - w
        left = x
        right = sw - (x + w)
        if right >= left and right > 0:
            new_w = w + min(short, right)
            return (x, y, new_w, h), deviation, 'w(right)'
        elif left > 0:
            new_x = max(0, x - min(short, left))
            new_w = w + (x - new_x)
            return (new_x, y, new_w, h), deviation, 'w(left)'
        return None, deviation, ''
    else:
        # w / h 가 큼 → h 부족 — h 확장
        target_h = int(round(w / expected_aspect))
        short = target_h - h
        top = y
        bot = sh - (y + h)
        if bot >= top and bot > 0:
            new_h = h + min(short, bot)
            return (x, y, w, new_h), deviation, 'h(bot)'
        elif top > 0:
            new_y = max(0, y - min(short, top))
            new_h = h + (y - new_y)
            return (x, new_y, w, new_h), deviation, 'h(top)'
        return None, deviation, ''


def _save_orb_debug(scan, proj, debug_dir, name, downscale=0.15):
    """ORB 매칭 4 코너를 scan 위에 overlay 해서 저장 (시각 검증용).

    proj: 원본 해상도 4 코너 좌표 (orb_extract_body 산출).
    """
    sh, sw = scan.shape[:2]
    vis = cv2.resize(scan, None, fx=downscale, fy=downscale,
                     interpolation=cv2.INTER_AREA)
    pts = (proj * downscale).astype(np.int32)
    cv2.polylines(vis, [pts], True, (0, 255, 0), 2)
    for p in pts:
        cv2.circle(vis, tuple(p), 6, (0, 0, 255), -1)
    out = os.path.join(debug_dir, f'{name}_proj.jpg')
    _imwrite(out, vis, [cv2.IMWRITE_JPEG_QUALITY, 85])


def main():
    ap = argparse.ArgumentParser(
        description='지도영역 추출 (ORB 매칭 기본 / HSV 폴백)')
    ap.add_argument('--identified', required=True,
                    help='Stage 2 산출 identified/ 폴더')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--sheet-cache',
                    help='Stage 2 산출 _sheet_cache/ — body 템플릿 (.body.jpg)')
    ap.add_argument('--max-saturation', type=int, default=30,
                    help='HSV 폴백: 이 값 미만 채도만 매칭')
    ap.add_argument('--v-max', type=int, default=130,
                    help='HSV 폴백: 이 값 이하 명도만 매칭')
    ap.add_argument('--inset', type=int, default=8,
                    help='HSV 폴백: 검출선 안쪽으로 자를 px')
    ap.add_argument('--sheet-bboxes',
                    help='Stage 2 산출 sheet_bboxes.json — 종횡비 가드 활성화')
    ap.add_argument('--aspect-tol', type=float, default=0.03,
                    help='종횡비 편차 허용 (기본 3%%, HSV 폴백 시 보정)')
    ap.add_argument('--no-debug', action='store_true',
                    help='ORB 매칭 4 코너 overlay 디버그 이미지 저장 비활성')
    args = ap.parse_args()

    sheet_bboxes = {}
    if args.sheet_bboxes and os.path.exists(args.sheet_bboxes):
        try:
            with open(args.sheet_bboxes) as f:
                sheet_bboxes = json.load(f)
            print(f'[종횡비 가드] sheet_bboxes 로드: '
                  f'{sum(len(v) for v in sheet_bboxes.values())}개 시트')
        except Exception as e:
            print(f'[경고] sheet_bboxes 로드 실패: {e}')

    os.makedirs(args.out_dir, exist_ok=True)
    targets = _discover_identified(args.identified)
    has_cache = args.sheet_cache and os.path.isdir(args.sheet_cache)
    debug_dir = (None if args.no_debug
                 else os.path.join(args.out_dir, '_orb_debug'))
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
    print(f'[지도영역 추출] {len(targets)}장 처리 시작 '
          f'(mode={"ORB+HSV폴백" if has_cache else "HSV 단독"})')

    csv_path = os.path.join(args.out_dir, '_status.csv')
    n_ok = n_err = n_orb = n_hsv = n_corrected = 0
    t_total = time.time()

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scan_path', 'output', 'status', 'admin_code', 'sheet_id',
                    'orig_size', 'crop_size', 'bbox_xywh', 'method',
                    'orb_inliers',
                    'expected_aspect', 'detected_aspect', 'deviation_pct',
                    'corrected_axis', 'message', 'elapsed_s'])

        for i, (scan_path, admin, sid) in enumerate(targets, 1):
            t0 = time.time()

            scan = _imread(scan_path)
            if scan is None:
                w.writerow([scan_path, '', 'ERROR', admin, sid,
                            '', '', '', '', '', '', '', '', '',
                            'imread 실패', f'{time.time()-t0:.2f}'])
                n_err += 1
                continue

            method = ''
            inliers = ''
            bbox = None
            cropped = None

            # 1차: ORB 매칭 + perspective warp (deskew + crop 동시)
            if has_cache:
                body_path = os.path.join(args.sheet_cache,
                                          f'{admin}_{sid}.body.jpg')
                if os.path.exists(body_path):
                    res = orb_extract_body(scan, body_path)
                    if res is not None:
                        cropped, bbox, n_in, _, proj = res
                        method = 'ORB'
                        inliers = str(n_in)
                        n_orb += 1
                        if debug_dir:
                            scan_name = os.path.splitext(
                                os.path.basename(scan_path))[0]
                            try:
                                _save_orb_debug(scan, proj, debug_dir,
                                                scan_name)
                            except Exception:
                                pass

            # 2차: HSV 폴백 (axis-aligned crop)
            if cropped is None:
                try:
                    _cropped, bbox = extract_map_region_scan(
                        scan,
                        max_saturation=args.max_saturation,
                        v_max=args.v_max,
                        inset=args.inset)
                    method = 'HSV'
                    n_hsv += 1
                except ValueError as e:
                    w.writerow([scan_path, '', 'FAIL', admin, sid,
                                f'{scan.shape[1]}x{scan.shape[0]}', '', '',
                                'HSV', '', '', '', '', '',
                                str(e), f'{time.time()-t0:.2f}'])
                    n_err += 1
                    continue

            # 종횡비 가드 — HSV 결과만 보정 (ORB warped 는 정확)
            exp_aspect = _expected_aspect(sheet_bboxes, admin, sid)
            axis = ''
            deviation = 0.0
            if method == 'HSV':
                corrected_bbox, deviation, axis = _aspect_guard(
                    scan.shape, bbox, exp_aspect, tol=args.aspect_tol)
                if corrected_bbox is not None:
                    bbox = corrected_bbox
                    n_corrected += 1
                x, y, bw, bh = bbox
                cropped = scan[y:y+bh, x:x+bw]
            else:
                # ORB warped 종횡비 편차 기록만
                if exp_aspect:
                    ch, cw = cropped.shape[:2]
                    det = cw / ch if ch > 0 else 0
                    deviation = (det - exp_aspect) / exp_aspect
            ch, cw = cropped.shape[:2]
            det_aspect = cw / ch if ch > 0 else 0

            rel = os.path.relpath(scan_path, args.identified)
            out_path = os.path.join(args.out_dir, rel)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            _imwrite(out_path, cropped, [cv2.IMWRITE_JPEG_QUALITY, 92])

            w.writerow([
                scan_path, out_path, 'OK', admin, sid,
                f'{scan.shape[1]}x{scan.shape[0]}',
                f'{cropped.shape[1]}x{cropped.shape[0]}',
                f'{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}',
                method, inliers,
                f'{exp_aspect:.4f}' if exp_aspect else '',
                f'{det_aspect:.4f}',
                f'{deviation*100:.2f}' if exp_aspect else '',
                axis, '', f'{time.time()-t0:.2f}'])
            n_ok += 1

            if i % 5 == 0 or i == len(targets):
                print(f'  [{i}/{len(targets)}] OK={n_ok} ERR={n_err} '
                      f'ORB={n_orb} HSV={n_hsv} corr={n_corrected} '
                      f'({(time.time()-t_total)/i:.2f}s/장)')

    print(f'\n[지도영역 추출] 완료: OK={n_ok}, ERROR={n_err} '
          f'(ORB={n_orb}, HSV={n_hsv}, 종횡비 보정={n_corrected})')
    print(f'  결과: {csv_path}')


if __name__ == '__main__':
    main()
