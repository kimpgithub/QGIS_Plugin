"""수동 정합 — 사람이 찍은 4점으로 지도영역 크롭/지오레퍼런싱.

헤더가 절단된 스캔은 Stage 2(헤더 OCR 식별)·extract_map(헤더 zone 가정)이 깨진다.
이때 사람이 (admin_code, sheet_id) 와 스캔 위 지도 4꼭지점을 지정해 복구한다.

두 경로:
- PDF 모드: 스캔 4점 → 퍼스펙티브 정류 크롭만 한다. world bbox 는 PDF 메타에서
  계산(SheetCache.compute_sheet_world_bbox). 결과 크롭을 3_map_extracted/ 에
  떨궈 Stage 3(SIFT)+4 가 그대로 잇는다 — 정합 정밀도는 SIFT 가 책임.
- PDF-less 모드: 스캔 4점 + 지도(월드) 4점 → 4-GCP 직접 지오레퍼런싱.
  결과 jpg+jgw 를 4_warped/ 에 떨궈 Stage 3 를 건너뛰고 Stage 4 가 잇는다.

좌표 규약: 4점은 항상 TL, TR, BR, BL (좌상→우상→우하→좌하) 순서.
"""
import os

import cv2
import numpy as np

from .common import JGWParams, PRJ_5179, load_image, save_image, write_jgw


def _quad_output_size(quad):
    """4점(TL,TR,BR,BL) 의 변 길이로 출력 (w, h) 픽셀 산출."""
    tl, tr, br, bl = (np.asarray(p, np.float32) for p in quad)
    w = max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))
    h = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    return max(int(round(w)), 1), max(int(round(h)), 1)


def rectify_quad(img, quad, out_size=None):
    """스캔 quad(TL,TR,BR,BL, 원본 픽셀) → axis-aligned 직사각 이미지."""
    w, h = out_size or _quad_output_size(quad)
    M = cv2.getPerspectiveTransform(
        np.asarray(quad, np.float32),
        np.float32([[0, 0], [w, 0], [w, h], [0, h]]))
    return cv2.warpPerspective(img, M, (w, h), borderValue=(255, 255, 255))


def crop_map_region(scan_path, quad, out_jpg, quality=92):
    """PDF 모드 — 스캔 quad 를 정류 크롭해 저장 (Stage 3 입력용).

    Returns: (width, height) 픽셀.
    """
    rect = rectify_quad(load_image(scan_path), quad)
    os.makedirs(os.path.dirname(out_jpg), exist_ok=True)
    save_image(rect, out_jpg, quality)
    return rect.shape[1], rect.shape[0]


def georef_from_gcps(scan_path, scan_quad, world_pts, out_jpg,
                     target_long_px=4000, quality=92):
    """PDF-less 모드 — 스캔 4점 ↔ 월드 4점(EPSG:5179) 직접 지오레퍼런싱.

    출력: north-up axis-aligned JPG + JGW(+PRJ+aux) — Stage 3 산출과 동일 포맷.

    Args:
        scan_quad: 스캔 원본 픽셀 4점 (TL,TR,BR,BL).
        world_pts: 대응 월드 좌표 4점 (TL,TR,BR,BL), EPSG:5179.
        target_long_px: 출력 긴 변 픽셀 수 (해상도 결정).
    Returns: world bbox (minx, miny, maxx, maxy).
    """
    world = np.asarray(world_pts, np.float64)
    minx, maxx = world[:, 0].min(), world[:, 0].max()
    miny, maxy = world[:, 1].min(), world[:, 1].max()
    ps = max(maxx - minx, maxy - miny) / float(target_long_px)  # m/px
    ow = max(int(round((maxx - minx) / ps)), 1)
    oh = max(int(round((maxy - miny) / ps)), 1)
    # 월드점 → 출력 픽셀 좌표 (north-up: y 반전)
    dst = np.float32([[(x - minx) / ps, (maxy - y) / ps] for x, y in world])
    M = cv2.getPerspectiveTransform(np.asarray(scan_quad, np.float32), dst)
    warped = cv2.warpPerspective(load_image(scan_path), M, (ow, oh),
                                 borderValue=(255, 255, 255))
    os.makedirs(os.path.dirname(out_jpg), exist_ok=True)
    save_image(warped, out_jpg, quality)
    # write_jgw 가 동일 base 의 .prj + .aux.xml 까지 생성 (jpg 가 이미 있어야 함)
    write_jgw(os.path.splitext(out_jpg)[0] + '.jgw',
              JGWParams(ps, 0.0, 0.0, -ps, minx, maxy))
    return (float(minx), float(miny), float(maxx), float(maxy))
