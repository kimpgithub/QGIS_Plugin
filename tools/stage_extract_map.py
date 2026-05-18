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
# ECC (Enhanced Correlation Coefficient) — ORB 후 픽셀 단위 정밀화
ECC_MAX_ITER = 50
ECC_EPS = 1e-5
ECC_GAUSS_FILT = 5
# ECC 발산 가드 — ORB H 대비 4-corner 평균 편차가 이 비율(폭)을 넘으면 ORB H 사용
ECC_MAX_DRIFT_FRAC = 0.05
# 투영 quad sanity — scan 경계를 이 비율 이상 벗어나면 ORB H 자체 거부 (HSV 폴백)
PROJ_MAX_OOB_FRAC = 0.10
# 투영 quad 종횡비 — body 템플릿 종횡비 대비 이 비율 벗어나면 거부
PROJ_ASPECT_TOL = 0.20
# 라벨 anchor 위치 sanity — 검출 라벨이 좌상귀에서 너무 멀면 H 미스매핑 의심.
# src 외삽(SRC_EXPAND_*_FRAC) 위에 추가 마진. 임계 = 외삽 비율 + 이 마진.
PROJ_LABEL_OFFSET_EXTRA = 0.03
# Inlier 공간 분포 가드 — RANSAC inlier 가 body template 의 작은 영역에 몰리면
# 빈 본문(섬만 산재) 케이스. H 가 그 영역 밖으로 over-extrapolate 위험 → 거부.
# 임계: inlier bbox area / template area
INLIER_COVERAGE_MIN = 0.30
# 4-corner quadrilateral sanity — 마주보는 변 길이 비율, 인접 변 직각도.
# 정상 perspective warp 는 거의 사각형. 사다리꼴/평행사변형 변형 거부.
QUAD_OPP_SIDE_TOL = 0.10        # 마주보는 변 길이 차이 비율
QUAD_ANGLE_COS_TOL = 0.20       # 인접 변 cos(각도) — 0=직각, ±0.20 ≈ ±11.5°
# 본문 크기 sanity — 추출 본문(out_w, out_h)/scan 크기 비율.
# 정상 시트는 본문이 paper 의 ~85~88% 차지 (헤더+푸터 제외). 벗어나면 H 거부.
PROJ_BODY_RATIO_MIN = 0.78
PROJ_BODY_RATIO_MAX = 0.92
# 데이터 기반 절대 픽셀 임계 — 헐겁게 (HSV 폴백 outlier 도 통과)
# ORB median: w 9329, h 12097. 운영 데이터 누적 후 좁힐 수 있음.
EXPECTED_BODY_W = (8200, 9800)
EXPECTED_BODY_H = (11500, 12900)
# Post-warp label anchor trim (빈 본문 케이스에서 시트번호 보존)
TRIM_LABEL_QUADRANT = 0.20       # 좌상단 분석 영역 비율
TRIM_LABEL_V_MAX = 100           # 검은 잉크 임계 (밝기)
TRIM_LABEL_S_MAX = 60            # 검은 잉크 임계 (채도)
TRIM_LABEL_EROSION_FRAC = 0.001  # warped 폭 대비 erosion 커널 (라벨 두께 필터)
TRIM_LABEL_MIN_AREA_FRAC = 1e-5  # 검출 글리프 최소 면적 (warped 전체 대비)
TRIM_LABEL_MIN_H_FRAC = 0.005    # 라벨 글리프 최소 height (warped 높이 대비)
TRIM_LABEL_MARGIN_FRAC = 0.25    # 라벨 height 대비 좌·상 padding 비율
# src_quad 외삽 — body 좌상귀를 위·왼쪽으로 확장해 라벨 영역까지 sampling
# (라벨은 body template 의 (0,0) 보다 위·왼쪽에 위치하므로 H 외삽으로 흡수)
SRC_EXPAND_TOP_FRAC = 0.08
SRC_EXPAND_LEFT_FRAC = 0.08
# 라벨 미검출 시 적응 padding (src 외삽 후에도 ECC 가 더 잘라낸 코너 케이스 보강)
TRIM_LABEL_FALLBACK_STEP_FRAC = 0.01   # 시도당 padding 증가량 (warped 높이 대비)
TRIM_LABEL_FALLBACK_MAX_FRAC = 0.04    # 최대 padding cap (src 외삽으로 대부분 흡수)


def _refine_homography_ecc(body, scan_g, H_init):
    """ORB H 를 ECC (Enhanced Correlation Coefficient) 로 픽셀 단위 정밀화.

    body, scan_g 모두 grayscale. H_init: ORB 가 추정한 body→scan 호모그래피.
    ECC 가 모든 픽셀 intensity correlation 을 최대화하도록 H 를 iterative
    refine. 코너 외삽 오차를 직접 보정 (sparse feature 한계 우회).

    발산 가드: ECC 후 4-corner 가 ORB 추정에서 ECC_MAX_DRIFT_FRAC 이상 벗어나면
    빈 본문(섬만 산재) 케이스에서 발산한 것으로 보고 ORB H 사용.

    Returns:
        H_refined (np.float64) or H_init on failure/divergence.
    """
    try:
        body_f = body.astype(np.float32) / 255.0
        scan_f = scan_g.astype(np.float32) / 255.0
        warp = H_init.astype(np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                    ECC_MAX_ITER, ECC_EPS)
        cc, warp = cv2.findTransformECC(
            body_f, scan_f, warp,
            motionType=cv2.MOTION_HOMOGRAPHY,
            criteria=criteria,
            inputMask=None,
            gaussFiltSize=ECC_GAUSS_FILT)
        H_refined = warp.astype(np.float64)
        th, tw = body.shape
        corners = np.float32(
            [[0, 0], [tw, 0], [tw, th], [0, th]]).reshape(-1, 1, 2)
        proj_orb = cv2.perspectiveTransform(corners, H_init).reshape(-1, 2)
        proj_ecc = cv2.perspectiveTransform(corners, H_refined).reshape(-1, 2)
        drift = float(np.linalg.norm(proj_ecc - proj_orb, axis=1).mean())
        if drift > tw * ECC_MAX_DRIFT_FRAC:
            return H_init
        return H_refined
    except cv2.error:
        return H_init


def _trim_via_label(warped):
    """좌상단 시트라벨 ('4-1', '7-3' 등) 글리프를 anchor 로 본문 좌상단 정밀 검출.

    PDF body 템플릿은 본문 좌상단이 (0,0). 시트라벨은 코너 안쪽 ~수십 px 에
    위치하는 굵은 검정 글리프. 두께 필터 + 좌상단에 가까운 큰 CC.

    빈 본문 케이스(섬만 산재) 에서 ECC 가 좌상귀를 안쪽으로 외삽시켜 라벨이
    잘리는 회귀를 보정.

    Returns:
        (label_top, label_left, label_h, label_w) — 검출 성공
        None — 실패 (라벨 미검출)
    """
    h, w = warped.shape[:2]
    qh = int(h * TRIM_LABEL_QUADRANT)
    qw = int(w * TRIM_LABEL_QUADRANT)
    region = warped[:qh, :qw]
    if region.size == 0:
        return None
    if region.ndim == 3:
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2]
        sat = hsv[:, :, 1]
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
    min_h = int(h * TRIM_LABEL_MIN_H_FRAC)
    cands = [i for i in range(1, n)
             if stats[i, cv2.CC_STAT_AREA] >= min_area
             and stats[i, cv2.CC_STAT_HEIGHT] >= min_h]
    if not cands:
        return None
    label_idx = min(cands, key=lambda i: (stats[i, cv2.CC_STAT_LEFT] +
                                           stats[i, cv2.CC_STAT_TOP]))
    return (int(stats[label_idx, cv2.CC_STAT_TOP]),
            int(stats[label_idx, cv2.CC_STAT_LEFT]),
            int(stats[label_idx, cv2.CC_STAT_HEIGHT]),
            int(stats[label_idx, cv2.CC_STAT_WIDTH]))


def check_body_size(w, h):
    """추출 본문 크기가 데이터 기반 절대 임계 [EXPECTED_BODY_W/H] 안인지 검사.

    Returns: (ok, reason) — 실패 시 reason 에 어떤 축에서 어떻게 벗어났는지 표기.
    """
    if not (EXPECTED_BODY_W[0] <= w <= EXPECTED_BODY_W[1]):
        return False, f'width {w} ∉ {EXPECTED_BODY_W}'
    if not (EXPECTED_BODY_H[0] <= h <= EXPECTED_BODY_H[1]):
        return False, f'height {h} ∉ {EXPECTED_BODY_H}'
    return True, ''


def _pad_top_left(img, pad_top, pad_left):
    """좌·상 흰색 padding 추가 (라벨이 잘리지 않도록 캔버스 확장)."""
    if pad_top == 0 and pad_left == 0:
        return img
    h, w = img.shape[:2]
    canvas_shape = ((h + pad_top, w + pad_left, 3)
                    if img.ndim == 3 else (h + pad_top, w + pad_left))
    canvas = np.full(canvas_shape, 255, img.dtype)
    canvas[pad_top:, pad_left:] = img
    return canvas


def _normalize_to_label(img, label):
    """라벨 위치 기준 좌·상을 margin 만큼만 남기고 trim/pad — 헤더 영역 제거."""
    h, w = img.shape[:2]
    lt, ll, lh, _ = label
    margin = int(lh * TRIM_LABEL_MARGIN_FRAC)
    # 라벨이 좌상귀에서 margin 보다 멀면 잉여 영역 trim
    cut_top = max(0, lt - margin)
    cut_left = max(0, ll - margin)
    if cut_top or cut_left:
        img = img[cut_top:, cut_left:]
        lt -= cut_top
        ll -= cut_left
    # 라벨이 margin 안쪽에 너무 붙어있으면 padding 확장
    pad_top = max(0, margin - lt)
    pad_left = max(0, margin - ll)
    return _pad_top_left(img, pad_top, pad_left)


def _trim_to_label_anchor(warped):
    """라벨 anchor 정규화 — 좌상귀를 라벨 기준 일정 margin 으로 통일.

    1) 라벨 검출 → margin 안쪽이면 pad, 바깥이면 trim (헤더 잉여 제거)
    2) 라벨 미검출 → 적응 padding 으로 재검출, 검출되면 (1) 적용
    """
    label = _trim_via_label(warped)
    if label is not None:
        return _normalize_to_label(warped, label), label
    h = warped.shape[0]
    step = max(1, int(h * TRIM_LABEL_FALLBACK_STEP_FRAC))
    cap = max(step, int(h * TRIM_LABEL_FALLBACK_MAX_FRAC))
    pad = step
    while pad <= cap:
        padded = _pad_top_left(warped, pad, pad)
        lab = _trim_via_label(padded)
        if lab is not None:
            return _normalize_to_label(padded, lab), lab
        pad += step
    return _pad_top_left(warped, cap, cap), None


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

    # Inlier 공간 분포 가드 — body template 내에서 inlier 가 차지하는 bbox 비율.
    # 빈 본문(섬·작은 마을만) 케이스에서 inlier 가 한 영역에 몰려 H 가 그 영역
    # 밖으로 over-extrapolate 하는 케이스 차단. coverage 낮으면 거부.
    inlier_src = src[mask.ravel() == 1].reshape(-1, 2)
    if len(inlier_src) >= 2:
        ix_min, iy_min = inlier_src.min(axis=0)
        ix_max, iy_max = inlier_src.max(axis=0)
        coverage = ((ix_max - ix_min) * (iy_max - iy_min)) / (tw * th)
        if coverage < INLIER_COVERAGE_MIN:
            return None

    # ECC dense refinement — ORB sparse 한계 극복, 코너 외삽 오차 픽셀 단위 보정
    H = _refine_homography_ecc(body, scan_g, H)

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

    # 본문 크기 sanity — 추출 영역이 paper 의 정상 비율(~85~88%) 범위 밖이면
    # 헤더 포함(과대) 또는 ECC 과도 축소(과소) 케이스로 보고 거부
    body_ratio_w = out_w / sw
    body_ratio_h = out_h / sh
    if not (PROJ_BODY_RATIO_MIN <= body_ratio_w <= PROJ_BODY_RATIO_MAX and
            PROJ_BODY_RATIO_MIN <= body_ratio_h <= PROJ_BODY_RATIO_MAX):
        return None

    # 4-corner quadrilateral sanity — 마주보는 변 길이 비율 + 인접 변 직각도.
    # 약한 perspective skew (사다리꼴) 거부. 정상 ORB warp 는 거의 사각형.
    edges = [proj[(i + 1) % 4] - proj[i] for i in range(4)]
    lens = [float(np.linalg.norm(e)) for e in edges]
    if min(lens) <= 0:
        return None
    # 마주보는 변 (0↔2, 1↔3) 길이 차이
    opp_diff_h = abs(lens[0] - lens[2]) / max(lens[0], lens[2])
    opp_diff_v = abs(lens[1] - lens[3]) / max(lens[1], lens[3])
    if opp_diff_h > QUAD_OPP_SIDE_TOL or opp_diff_v > QUAD_OPP_SIDE_TOL:
        return None
    # 인접 변 직각도 — cos(각도) 가 0 에 가까워야 직각
    for i in range(4):
        e_in = -edges[(i - 1) % 4]
        e_out = edges[i]
        cos_a = float(np.dot(e_in, e_out) / (lens[(i - 1) % 4] * lens[i]))
        if abs(cos_a) > QUAD_ANGLE_COS_TOL:
            return None

    # quad sanity guard — inlier 가 한 영역에 몰린 케이스(빈 본문) 에서
    # H 가 사방으로 발산하여 quad 가 scan 밖으로 튀거나 종횡비가 깨지면
    # ORB 결과 거부 → HSV 폴백 위임
    oob = np.maximum.reduce([
        np.maximum(0, -proj[:, 0]).max() / sw,
        np.maximum(0, proj[:, 0] - sw).max() / sw,
        np.maximum(0, -proj[:, 1]).max() / sh,
        np.maximum(0, proj[:, 1] - sh).max() / sh,
    ])
    if oob > PROJ_MAX_OOB_FRAC:
        return None
    template_aspect = tw / th
    proj_aspect = width / height if height > 0 else 0
    if proj_aspect <= 0 or abs(proj_aspect - template_aspect) / template_aspect > PROJ_ASPECT_TOL:
        return None

    # src_quad 외삽 — body template 좌상귀 위·왼쪽 영역까지 sampling 해야
    # 시트번호 라벨이 결과에 포함됨. perspective 일관성 유지 위해 dst 코너를
    # 음수 좌표로 확장한 뒤 H_inv 로 src 외삽 위치를 역산.
    base_dst = np.float32([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]])
    src_quad = proj.astype(np.float32)
    H_dst_to_src = cv2.getPerspectiveTransform(base_dst, src_quad)
    ext_top = int(round(out_h * SRC_EXPAND_TOP_FRAC))
    ext_left = int(round(out_w * SRC_EXPAND_LEFT_FRAC))
    ext_dst = np.float32([
        [-ext_left, -ext_top], [out_w, -ext_top],
        [out_w, out_h], [-ext_left, out_h]
    ]).reshape(-1, 1, 2)
    ext_src = cv2.perspectiveTransform(ext_dst, H_dst_to_src).reshape(-1, 2)
    new_out_w = out_w + ext_left
    new_out_h = out_h + ext_top
    final_dst = np.float32([
        [0, 0], [new_out_w, 0],
        [new_out_w, new_out_h], [0, new_out_h]
    ])
    M = cv2.getPerspectiveTransform(ext_src.astype(np.float32), final_dst)
    warped = cv2.warpPerspective(scan, M, (new_out_w, new_out_h),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(255, 255, 255))
    out_w, out_h = new_out_w, new_out_h

    # 라벨 위치 기반 H sanity — 라벨이 좌상귀에서 너무 멀면 H 가 body→scan 을
    # 위·왼쪽으로 끌어올려 헤더까지 포함시킨 케이스 (빈 본문 + 한쪽 inlier 편향).
    # src 외삽으로 라벨이 ext_top 부근에 위치하는 게 정상 → 외삽 + extra 마진 허용.
    label = _trim_via_label(warped)
    if label is not None:
        lt, ll, _, _ = label
        top_thr = SRC_EXPAND_TOP_FRAC + PROJ_LABEL_OFFSET_EXTRA
        left_thr = SRC_EXPAND_LEFT_FRAC + PROJ_LABEL_OFFSET_EXTRA
        if lt / out_h > top_thr or ll / out_w > left_thr:
            return None

    # 라벨 anchor 후처리 — ECC 좌상귀 외삽으로 시트번호가 잘린 경우 padding 복원
    warped, _ = _trim_to_label_anchor(warped)
    # 절대 픽셀 사이즈 검증 — 데이터 기반 임계 (median ±3%) 벗어나면 거부
    ok, _reason = check_body_size(warped.shape[1], warped.shape[0])
    if not ok:
        return None
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


def _peer_sanity_pass(records, out_dir, tol=0.01):
    """admin 내 시트들의 body 사이즈가 같아야 한다는 물리 제약으로 outlier 보정.

    같은 admin = 같은 종이 포맷 = 같은 본문 픽셀 크기. tol 초과 outlier 는
    peer 의 좌/상/우/하 마진 중앙값으로 원본 스캔에서 재크롭 + 덮어쓰기.
    records: [{admin,sid,scan_path,out_path,orig_w,orig_h,bbox,cw,ch,method}]
    Returns: 보정 시트 수.
    """
    from statistics import median
    from collections import defaultdict
    by_admin = defaultdict(list)
    for r in records:
        by_admin[r['admin']].append(r)
    corrections = []
    for admin, recs in by_admin.items():
        if len(recs) < 3:
            continue
        ws = [r['cw'] for r in recs]
        hs = [r['ch'] for r in recs]
        med_w, med_h = median(ws), median(hs)
        peers = [r for r in recs
                 if abs(r['cw'] - med_w) / med_w <= tol
                 and abs(r['ch'] - med_h) / med_h <= tol]
        outliers = [r for r in recs if r not in peers]
        if not peers or not outliers:
            continue
        m_left = int(round(median([r['bbox'][0] for r in peers])))
        m_top = int(round(median([r['bbox'][1] for r in peers])))
        m_right = int(round(median(
            [r['orig_w'] - r['bbox'][0] - r['bbox'][2] for r in peers])))
        m_bot = int(round(median(
            [r['orig_h'] - r['bbox'][1] - r['bbox'][3] for r in peers])))
        for r in outliers:
            new_x, new_y = m_left, m_top
            new_w = r['orig_w'] - new_x - m_right
            new_h = r['orig_h'] - new_y - m_bot
            if new_w <= 0 or new_h <= 0:
                continue
            scan = _imread(r['scan_path'])
            if scan is None:
                continue
            new_crop = scan[new_y:new_y + new_h, new_x:new_x + new_w]
            _imwrite(r['out_path'], new_crop,
                     [cv2.IMWRITE_JPEG_QUALITY, 92])
            corrections.append({
                'admin': admin, 'sid': r['sid'],
                'before': f'{r["cw"]}x{r["ch"]}',
                'after': f'{new_w}x{new_h}',
                'med_peer': f'{int(med_w)}x{int(med_h)}',
                'n_peers': len(peers),
                'dw_pct': round((r['cw'] - med_w) / med_w * 100, 2),
                'dh_pct': round((r['ch'] - med_h) / med_h * 100, 2),
            })
            r['bbox'] = (new_x, new_y, new_w, new_h)
            r['cw'], r['ch'] = new_w, new_h
    if corrections:
        post_path = os.path.join(out_dir, '_status_postpass.csv')
        with open(post_path, 'w', newline='', encoding='utf-8') as f:
            wr = csv.writer(f)
            wr.writerow(['admin', 'sheet_id', 'before', 'after',
                         'peer_median', 'n_peers', 'dw_pct', 'dh_pct'])
            for c in corrections:
                wr.writerow([c['admin'], c['sid'], c['before'], c['after'],
                             c['med_peer'], c['n_peers'],
                             c['dw_pct'], c['dh_pct']])
    return len(corrections)


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
    ap.add_argument('--peer-sanity-tol', type=float, default=0.01,
                    help='admin 내 시트 사이즈 median 대비 이 비율 초과 시 '
                         'peer 마진으로 재크롭 (기본 0.01 = 1%%)')
    ap.add_argument('--no-peer-sanity', action='store_true',
                    help='peer sanity post-pass 비활성')
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
    records = []   # peer sanity post-pass 용 OK 결과 누적

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

            # 절대 픽셀 사이즈 검증 — 데이터 기반 임계 (median ±3%)
            size_ok, size_reason = check_body_size(cw, ch)
            if not size_ok:
                w.writerow([scan_path, '', 'FAIL', admin, sid,
                            f'{scan.shape[1]}x{scan.shape[0]}',
                            f'{cw}x{ch}', '', method, inliers,
                            '', f'{det_aspect:.4f}', '', '',
                            f'size 검증 실패: {size_reason}',
                            f'{time.time()-t0:.2f}'])
                n_err += 1
                continue

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
            records.append({
                'admin': admin, 'sid': sid, 'scan_path': scan_path,
                'out_path': out_path,
                'orig_w': scan.shape[1], 'orig_h': scan.shape[0],
                'bbox': tuple(bbox), 'cw': cropped.shape[1],
                'ch': cropped.shape[0], 'method': method,
            })

            if i % 5 == 0 or i == len(targets):
                print(f'  [{i}/{len(targets)}] OK={n_ok} ERR={n_err} '
                      f'ORB={n_orb} HSV={n_hsv} corr={n_corrected} '
                      f'({(time.time()-t_total)/i:.2f}s/장)')

    print(f'\n[지도영역 추출] 완료: OK={n_ok}, ERROR={n_err} '
          f'(ORB={n_orb}, HSV={n_hsv}, 종횡비 보정={n_corrected})')
    print(f'  결과: {csv_path}')

    if not args.no_peer_sanity and records:
        n_post = _peer_sanity_pass(records, args.out_dir,
                                    tol=args.peer_sanity_tol)
        if n_post:
            print(f'[peer sanity] admin 내 사이즈 outlier {n_post}장 재크롭 '
                  f'(tol={args.peer_sanity_tol*100:.1f}%) — '
                  f'{os.path.join(args.out_dir, "_status_postpass.csv")}')
        else:
            print(f'[peer sanity] outlier 없음 '
                  f'(tol={args.peer_sanity_tol*100:.1f}%)')


if __name__ == '__main__':
    main()
