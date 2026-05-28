"""수동 정합 — 사람이 찍은 스캔 4점으로 지도영역 정류 크롭.

헤더가 절단된 스캔은 Stage 2(헤더 OCR 식별)·extract_map(헤더 zone 가정)이 깨진다.
이때 사람이 (admin_code, sheet_id) 와 스캔 위 지도 4꼭지점을 지정해 복구한다.

스캔 4점 → 퍼스펙티브 정류 크롭. world bbox 는 PDF 메타에서 계산
(SheetCache.compute_sheet_world_bbox). 결과 크롭을 3_map_extracted/ 에 떨궈
Stage 3(SIFT)+4 가 그대로 잇는다 — 정합 정밀도는 SIFT 가 책임.

좌표 규약: 4점은 항상 TL, TR, BR, BL (좌상→우상→우하→좌하) 순서.
"""
import os

import cv2
import numpy as np

from .common import load_image, save_image


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
