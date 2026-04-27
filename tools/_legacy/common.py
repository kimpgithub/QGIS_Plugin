"""
GIS Scan Tools - 공통 유틸리티
- 이미지 로드/저장, JGW 파일 관리
- 지오레퍼런싱 핵심 함수 (FFT+Powell, GCP, VRT)
"""

import cv2
import numpy as np
import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple


# ============================================================
# 상수
# ============================================================

CRS_EPSG = "EPSG:5179"
DEFAULT_DPI = 300

# EPSG:5179 (Korea 2000 / Korea Unified CS) PRJ 정의
PRJ_5179 = 'PROJCS["Korea_2000_Korea_Unified_Coordinate_System",GEOGCS["GCS_Korea_2000",DATUM["D_Korea_2000",SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",1000000.0],PARAMETER["False_Northing",2000000.0],PARAMETER["Central_Meridian",127.5],PARAMETER["Scale_Factor",0.9996],PARAMETER["Latitude_Of_Origin",38.0],UNIT["Meter",1.0]]'

# PDF 텍스트 라벨 좌상단 → 셀 좌상단 보정 (PDF pt 단위)
# Stage 1 (메인 georef) / Stage 2 (sheet bbox) 공통 상수.
# 사용자 수동 georef 3 케이스 누적 분석 (39010110_4-1, 39010130_4-4, 39010120_4-2)
# Y: -2.76 → -2.11 (stdev 0.09 pt, 평균 5m 편향 제거)
# X: 노이즈 범위 유지 (-13.82)
LABEL_OFFSET_X_PT = -13.82
LABEL_OFFSET_Y_PT = -2.11


# ============================================================
# Unicode-safe 이미지 I/O (한글 경로 OS에서 cv2 imread/imwrite 우회)
# ============================================================

def imread_unicode(path):
    """np.fromfile + cv2.imdecode — 한글 경로에서도 동작. 실패 시 None."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def imwrite_unicode(path, img, params=None):
    """cv2.imencode + tofile — 한글 경로에서도 동작. 실패 시 False."""
    ext = os.path.splitext(path)[1] or '.jpg'
    ok, buf = cv2.imencode(ext, img, params or [])
    if not ok:
        return False
    buf.tofile(path)
    return True


# ============================================================
# 데이터 클래스
# ============================================================

@dataclass
class JGWParams:
    """JGW (World File) 파라미터"""
    pixel_size_x: float      # 픽셀당 X 크기 (m)
    rotation_x: float        # X축 회전 (보통 0)
    rotation_y: float        # Y축 회전 (보통 0)
    pixel_size_y: float      # 픽셀당 Y 크기 (m, 보통 음수)
    top_left_x: float        # 좌상단 X 좌표
    top_left_y: float        # 좌상단 Y 좌표


# ============================================================
# 이미지 로드/저장
# ============================================================

def load_image(image_path: str, max_retries: int = 3, retry_delay: float = 0.5) -> np.ndarray:
    """이미지 로드 (한글 경로 지원, 파일 잠금 회피, 재시도)

    Windows에서 QGIS가 파일을 열고 있어도 읽을 수 있도록
    공유 모드로 파일을 읽습니다. 실패 시 재시도합니다.
    """
    last_error = None

    for attempt in range(max_retries):
        try:
            with open(image_path, 'rb') as f:
                img_bytes = f.read()
            img_array = np.frombuffer(img_bytes, dtype=np.uint8)
            image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if image is not None:
                return image
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(retry_delay)

    # 폴백: numpy fromfile
    try:
        img_array = np.fromfile(image_path, dtype=np.uint8)
        image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if image is not None:
            return image
    except Exception as e:
        last_error = e

    raise FileNotFoundError(f"이미지 로드 실패: {image_path} ({last_error})")


def save_image(image: np.ndarray, output_path: str, quality: int = 95) -> bool:
    """이미지 저장 (한글 경로 지원)"""
    _, ext = os.path.splitext(output_path)
    ext = ext.lower()

    if ext in ['.jpg', '.jpeg']:
        params = [cv2.IMWRITE_JPEG_QUALITY, quality]
    elif ext == '.png':
        params = [cv2.IMWRITE_PNG_COMPRESSION, 9]
    else:
        params = []

    result, encoded = cv2.imencode(ext, image, params)
    if result:
        encoded.tofile(output_path)
        return True
    return False


# ============================================================
# JGW / PRJ / aux.xml 파일 관리
# ============================================================

def parse_jgw(jgw_path: str) -> JGWParams:
    """JGW 파일 파싱"""
    with open(jgw_path, 'r') as f:
        lines = f.readlines()
    return JGWParams(
        pixel_size_x=float(lines[0].strip()),
        rotation_x=float(lines[1].strip()),
        rotation_y=float(lines[2].strip()),
        pixel_size_y=float(lines[3].strip()),
        top_left_x=float(lines[4].strip()),
        top_left_y=float(lines[5].strip()),
    )


def write_jgw(jgw_path: str, params: JGWParams, write_prj: bool = True):
    """JGW 파일 저장 (PRJ + aux.xml 파일 포함)"""
    with open(jgw_path, 'w') as f:
        f.write(f"{params.pixel_size_x:.13f}\n")
        f.write(f"{params.rotation_x:.13f}\n")
        f.write(f"{params.rotation_y:.13f}\n")
        f.write(f"{params.pixel_size_y:.13f}\n")
        f.write(f"{params.top_left_x:.13f}\n")
        f.write(f"{params.top_left_y:.13f}\n")

    base_path = os.path.splitext(jgw_path)[0]

    if write_prj:
        prj_path = base_path + '.prj'
        with open(prj_path, 'w') as f:
            f.write(PRJ_5179)

        for ext in ['.jpg', '.jpeg', '.tif', '.tiff', '.png']:
            img_path = base_path + ext
            if os.path.exists(img_path):
                aux_path = img_path + '.aux.xml'
                write_aux_xml(aux_path)


def find_main_image(dir_path: str, admin_code: str) -> Optional[str]:
    """Stage 1 출력 메인 이미지 경로. TIFF 우선, JPG 폴백.

    Stage 1 기본 출력은 .tif (GeoTIFF). SIFT/Powell 폴백 경로나 구버전
    출력은 .jpg. 둘 다 지원.
    """
    for ext in ('.tif', '.tiff', '.TIF', '.TIFF',
                '.jpg', '.jpeg', '.JPG', '.JPEG'):
        p = os.path.join(dir_path, f'{admin_code}{ext}')
        if os.path.exists(p):
            return p
    return None


def write_aux_xml(aux_path: str, epsg: int = 5179):
    """QGIS가 인식하는 래스터 좌표계 aux.xml 파일 생성"""
    aux_content = f'''<PAMDataset>
  <SRS dataAxisToSRSAxisMapping="1,2">PROJCS["Korea_2000_Korea_Unified_Coordinate_System",GEOGCS["GCS_Korea_2000",DATUM["Korea_2000",SPHEROID["GRS 1980",6378137,298.257222101]],PRIMEM["Greenwich",0],UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",38],PARAMETER["central_meridian",127.5],PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",1000000],PARAMETER["false_northing",2000000],UNIT["metre",1],AXIS["Easting",EAST],AXIS["Northing",NORTH],AUTHORITY["EPSG","{epsg}"]]</SRS>
</PAMDataset>'''
    with open(aux_path, 'w') as f:
        f.write(aux_content)


# ============================================================
# 이미지 전처리 (지도 영역 추출, 마스크)
# ============================================================

def _find_dark_clusters(profile: np.ndarray, threshold: float = 80,
                        min_gap: int = 5) -> list:
    """1D 밝기 프로파일에서 어두운 구간(dark cluster) 검출

    Args:
        profile: 행/열별 평균 밝기 (1D array)
        threshold: 이 값 미만이면 '어두운' 것으로 판정
        min_gap: 연속 간격 허용치 — 이 값보다 멀리 떨어진 어두운 행은 별도 클러스터

    Returns:
        [(start, end), ...] 클러스터 목록
    """
    dark = np.where(profile < threshold)[0]
    if len(dark) == 0:
        return []
    groups = []
    start = int(dark[0])
    prev = int(dark[0])
    for i in dark[1:]:
        if i - prev > min_gap:
            groups.append((start, int(prev)))
            start = int(i)
        prev = int(i)
    groups.append((start, int(prev)))
    return groups


def extract_map_region(image: np.ndarray, edge_ratio: float = 0.2,
                       dark_threshold: float = 80, margin: int = 3):
    """이미지에서 지도 영역만 추출 (프레임 라인 기반)

    1D 밝기 프로파일에서 검은 프레임선을 검출하고,
    각 변의 가장 안쪽 프레임선 내부를 지도 영역으로 추출.
    프레임선이 없으면 흰 여백 제거 폴백.

    Args:
        image: 입력 BGR 이미지
        edge_ratio: 프레임선 탐색 범위 (각 변으로부터 이 비율 이내)
        dark_threshold: 프레임선 판정 밝기 임계값
        margin: 프레임선 안쪽 여유 (px)

    Returns:
        (추출된 이미지, (x, y, w, h) 바운딩 박스)
    """
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    row_mean = np.mean(gray, axis=1)
    col_mean = np.mean(gray, axis=0)

    h_clusters = _find_dark_clusters(row_mean, dark_threshold)
    v_clusters = _find_dark_clusters(col_mean, dark_threshold)

    # 외곽 edge_ratio 영역 내 클러스터만 필터 (중앙 접힘선 등 제외)
    top_lines = [c for c in h_clusters if c[1] < h * edge_ratio]
    bot_lines = [c for c in h_clusters if c[0] > h * (1 - edge_ratio)]
    left_lines = [c for c in v_clusters if c[1] < w * edge_ratio]
    right_lines = [c for c in v_clusters if c[0] > w * (1 - edge_ratio)]

    # 각 변의 가장 안쪽 프레임선
    map_top = top_lines[-1][1] + margin if top_lines else None
    map_bot = bot_lines[0][0] - margin if bot_lines else None
    map_left = left_lines[-1][1] + margin if left_lines else None
    map_right = right_lines[0][0] - margin if right_lines else None

    # 폴백: 프레임선 미검출 변은 흰 여백 기반으로 추정
    if any(v is None for v in [map_top, map_bot, map_left, map_right]):
        white_thresh = 245
        if map_top is None:
            white_rows = np.mean(gray > white_thresh, axis=1)
            for i in range(h):
                if white_rows[i] < 0.8:
                    map_top = i
                    break
            if map_top is None:
                map_top = 0
        if map_bot is None:
            white_rows = np.mean(gray > white_thresh, axis=1)
            for i in range(h - 1, -1, -1):
                if white_rows[i] < 0.8:
                    map_bot = i
                    break
            if map_bot is None:
                map_bot = h - 1
        if map_left is None:
            white_cols = np.mean(gray > white_thresh, axis=0)
            for i in range(w):
                if white_cols[i] < 0.8:
                    map_left = i
                    break
            if map_left is None:
                map_left = 0
        if map_right is None:
            white_cols = np.mean(gray > white_thresh, axis=0)
            for i in range(w - 1, -1, -1):
                if white_cols[i] < 0.8:
                    map_right = i
                    break
            if map_right is None:
                map_right = w - 1

    # 범위 보정
    map_top = max(0, min(map_top, h - 1))
    map_bot = max(map_top + 1, min(map_bot, h - 1))
    map_left = max(0, min(map_left, w - 1))
    map_right = max(map_left + 1, min(map_right, w - 1))

    rx, ry = map_left, map_top
    rw, rh = map_right - map_left + 1, map_bot - map_top + 1
    return image[ry:ry + rh, rx:rx + rw], (rx, ry, rw, rh)


def extract_map_region_scan(image: np.ndarray,
                             max_saturation: int = 30,
                             v_max: int = 130,
                             row_thr: float = 0.17,
                             col_thr: float = 0.25,
                             min_gap: int = 20,
                             header_zone: float = 0.30,
                             inset: int = 8,
                             min_size_ratio: float = 0.30):
    """스캔 이미지의 지도영역 추출 (HSV "어두운 무채색" 매칭 기반).

    PDF 렌더용 ``extract_map_region()`` 은 행/열 평균 밝기로 프레임선을
    검출하지만, 스캔 이미지는 흰 배경이 압도적이라 행 평균이 항상 높아
    그 방식이 통하지 않는다.

    인쇄 프레임선의 본질은 "**저채도 (회색조) + 중간 어두움**" — 이 속성은
    스캐너 캘리브레이션, 용지 노화, JPEG 압축에 강건하다. 빨강 수기(S>100),
    주황 행정경계(S>100), 흰 배경(V>v_max), 컬러 라벨은 자동 제외되고
    프레임선·표 셀선·검은 글자만 매칭. 글자는 행 폭의 일부만 차지하므로
    행 임계(row_thr=0.20)를 못 넘기고, 프레임 라인은 한 행 전체를 가로지르므로
    매칭 비율 ≈1.0 → 안정적 검출.

    Args:
        image: BGR 입력 (스캔 이미지)
        max_saturation: 이 값 미만 채도(0~255) 픽셀만 후보. 30이면 회색조만
        v_max: 이 값 이하 명도(0~255) 픽셀만 후보. 130이면 흰/연회색 제외
        row_thr: 행이 프레임 라인으로 판정되는 매칭 픽셀 비율 임계
        col_thr: 열이 프레임 라인으로 판정되는 매칭 픽셀 비율 임계
        min_gap: 인접 라인 후보 병합 거리 (px)
        header_zone: 위쪽 이 비율 안의 가장 아래 라인을 헤더/지도 분리선으로
        inset: 검출된 프레임선보다 이 px 안쪽으로 잘라 잔여 검정 줄 회피
        min_size_ratio: 추출 영역이 (h/w) × 이 비율 미만이면 검출 실패로 보고
            하단/우측 변을 이미지 가장자리로 폴백 (스캔 잘림 / 본문 하단
            인쇄 약함 케이스 회수). silent fail (height=2 인데 OK 보고) 차단.

    Returns:
        (cropped_bgr, (x, y, w, h))  — 헤더 제거된 지도영역 + 좌상단 + 크기

    Raises:
        ValueError: 행/열 프레임 라인 미검출 또는 폴백 후에도 영역 비정상
    """
    h, w = image.shape[:2]
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError('extract_map_region_scan은 BGR 컬러 입력 필요')

    # HSV 게이팅: 저채도(무채색) + 중간 어두움 → 프레임/표선/검은 글자만 통과
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    s = hsv[..., 1]
    v = hsv[..., 2]
    mask = (s < max_saturation) & (v <= v_max)

    row_pct = mask.mean(axis=1)
    col_pct = mask.mean(axis=0)

    def _line_centers(profile, thr):
        above = profile > thr
        if not above.any():
            return []
        pad = np.concatenate([[False], above, [False]])
        edges = np.diff(pad.astype(np.int8))
        starts = np.where(edges == 1)[0]
        ends = np.where(edges == -1)[0]
        centers = [(s + e - 1) // 2 for s, e in zip(starts, ends)]
        merged = [int(centers[0])]
        for c in centers[1:]:
            if c - merged[-1] < min_gap:
                merged[-1] = (merged[-1] + int(c)) // 2
            else:
                merged.append(int(c))
        return merged

    row_lines = _line_centers(row_pct, row_thr)
    col_lines = _line_centers(col_pct, col_thr)
    if not row_lines or not col_lines:
        raise ValueError(f'프레임 라인 미검출 (rows={len(row_lines)}, '
                         f'cols={len(col_lines)})')

    # 헤더/지도 분리선 = 위쪽 header_zone 안의 가장 아래 라인
    top_zone = [r for r in row_lines if r < h * header_zone]
    map_top = max(top_zone) if top_zone else row_lines[0]
    # 하단 외곽 = 아래쪽 (1 - header_zone) 너머의 가장 위 라인
    bot_zone = [r for r in row_lines if r > h * (1 - header_zone)]
    if bot_zone:
        map_bot = min(bot_zone)
    else:
        # 본문 하단 프레임 검출 실패 — 스캔 잘림 또는 인쇄 약함.
        # 이미지 하단을 그대로 사용 (사용자가 후속 정합으로 본문만 활용)
        map_bot = h - 1
    # 좌/우 외곽 (col 검출 실패 시 이미지 가장자리 폴백)
    map_left = col_lines[0] if col_lines else 0
    map_right = col_lines[-1] if len(col_lines) >= 2 else w - 1

    # 추출 영역 사이즈가 예상보다 작으면 (예: 본문 안에 잘못된 라인 검출)
    # 하단변/우측변을 이미지 가장자리로 강제 폴백
    candidate_rh = map_bot - map_top
    candidate_rw = map_right - map_left
    if candidate_rh < h * min_size_ratio:
        map_bot = h - 1
    if candidate_rw < w * min_size_ratio:
        map_right = w - 1

    # 라인 두께 + 약간 안쪽으로
    map_top = min(h - 2, map_top + inset)
    map_bot = max(map_top + 1, map_bot - inset)
    map_left = min(w - 2, map_left + inset)
    map_right = max(map_left + 1, map_right - inset)

    rx, ry = map_left, map_top
    rw, rh = map_right - map_left + 1, map_bot - map_top + 1
    # silent fail 차단 — 폴백 후에도 영역이 비정상이면 명시적 실패
    if rh < h * min_size_ratio or rw < w * min_size_ratio:
        raise ValueError(
            f'추출 영역 비정상 ({rw}x{rh} < {int(w*min_size_ratio)}x'
            f'{int(h*min_size_ratio)}) — 헤더 분리선 또는 좌측 외곽 검출 오류')
    return image[ry:ry + rh, rx:rx + rw], (rx, ry, rw, rh)


def detect_frame_thickness(image: np.ndarray, edge_ratio: float = 0.2,
                            dark_threshold: float = 80) -> Tuple[int, int]:
    """이미지 테두리 프레임선의 가로/세로 두께 감지

    프레임선 두께를 가로(top/bottom 평균), 세로(left/right 평균) 별도로 측정.
    분할도 병합 시 crop_margin 자동 계산에 사용.

    Args:
        image: 입력 BGR 이미지
        edge_ratio: 프레임선 탐색 범위 (각 변으로부터 이 비율 이내)
        dark_threshold: 프레임선 판정 밝기 임계값

    Returns:
        (horizontal_thickness, vertical_thickness) — 가로/세로 프레임 두께 (px)
        미검출 시 기본값 (3, 3)
    """
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    row_mean = np.mean(gray, axis=1)
    col_mean = np.mean(gray, axis=0)

    h_clusters = _find_dark_clusters(row_mean, dark_threshold)
    v_clusters = _find_dark_clusters(col_mean, dark_threshold)

    top_lines = [c for c in h_clusters if c[1] < h * edge_ratio]
    bot_lines = [c for c in h_clusters if c[0] > h * (1 - edge_ratio)]
    left_lines = [c for c in v_clusters if c[1] < w * edge_ratio]
    right_lines = [c for c in v_clusters if c[0] > w * (1 - edge_ratio)]

    # 각 변의 가장 안쪽 프레임선 두께 측정
    h_thicknesses = []
    if top_lines:
        c = top_lines[-1]
        h_thicknesses.append(c[1] - c[0] + 1)
    if bot_lines:
        c = bot_lines[0]
        h_thicknesses.append(c[1] - c[0] + 1)

    v_thicknesses = []
    if left_lines:
        c = left_lines[-1]
        v_thicknesses.append(c[1] - c[0] + 1)
    if right_lines:
        c = right_lines[0]
        v_thicknesses.append(c[1] - c[0] + 1)

    ht = int(round(np.mean(h_thicknesses))) if h_thicknesses else 3
    vt = int(round(np.mean(v_thicknesses))) if v_thicknesses else 3

    print(f"  [프레임두께] 가로={ht}px, 세로={vt}px")
    return ht, vt


def extract_orange_mask(image: np.ndarray) -> np.ndarray:
    """이미지에서 주황색 경계선 마스크 추출"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    lower_orange = np.array([5, 50, 100])
    upper_orange = np.array([25, 255, 255])
    mask = cv2.inRange(hsv, lower_orange, upper_orange)

    # 노이즈 제거
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    return mask


# ============================================================
# PDF 변환
# ============================================================

def convert_pdf_to_images(pdf_path: str, dpi: int = 300, output_dir: str = None) -> dict:
    """PDF → JPG 변환

    Args:
        pdf_path: PDF 파일 경로
        dpi: 변환 해상도 (기본 300)
        output_dir: 출력 폴더 (None이면 PDF와 같은 위치)

    Returns:
        {'jpg': jpg_path}
    """
    import fitz  # PyMuPDF

    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_dir = output_dir or os.path.dirname(pdf_path)
    jpg_path = os.path.join(out_dir, base + ".jpg")

    # PyMuPDF 렌더링
    doc = fitz.open(pdf_path)
    page = doc[0]
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))

    # numpy array 변환
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:  # RGBA → RGB
        img = img[:, :, :3]
    doc.close()

    save_image(cv2.cvtColor(img, cv2.COLOR_RGB2BGR), jpg_path)
    print(f"  [PDF→JPG] {os.path.basename(pdf_path)} → {os.path.basename(jpg_path)} ({pix.width}x{pix.height}, {dpi} DPI)")

    return {'jpg': jpg_path}


def convert_pdf_to_jpg(pdf_path: str, dpi: int = 300, output_path: str = None) -> str:
    """PDF를 JPG로 변환 (호환 래퍼)

    Args:
        pdf_path: PDF 파일 경로
        dpi: 변환 해상도 (기본 300)
        output_path: 미사용 (하위호환용)

    Returns:
        생성된 JPG 파일 경로
    """
    result = convert_pdf_to_images(pdf_path, dpi=dpi)
    return result['jpg']


def get_image_dpi(image_path: str) -> int:
    """이미지 파일에서 DPI 자동 감지

    PIL로 EXIF DPI를 읽고, 신뢰할 수 있는 값(200~1200)만 사용.
    72, 96, 120 등은 뷰어/편집기 기본값이므로 무시하고 300 반환.

    Args:
        image_path: 이미지 파일 경로

    Returns:
        DPI (int)
    """
    try:
        from PIL import Image
        img = Image.open(image_path)
        dpi_info = img.info.get('dpi')
        if dpi_info:
            dpi = int(round(dpi_info[0]))
            if 200 <= dpi <= 1200:
                return dpi
    except Exception:
        pass
    return DEFAULT_DPI


# ============================================================
# 스켈레톤 + Distance Transform
# ============================================================

def build_skeleton_and_distmap(orange_mask: np.ndarray):
    """주황 마스크 → 스켈레톤, 스켈레톤 점, distance transform

    Returns:
        (skeleton, skel_points, dist_map)
    """
    from skimage.morphology import skeletonize

    skeleton = skeletonize(orange_mask > 0).astype(np.uint8) * 255
    skel_points = np.column_stack(np.where(skeleton > 0))[:, ::-1].astype(np.float64)

    dist_map = cv2.distanceTransform(
        (255 - skeleton).astype(np.uint8), cv2.DIST_L2, 5
    )

    print(f"  [스켈레톤] {len(skel_points):,}개 픽셀, dist_map range [0, {dist_map.max():.1f}]")
    return skeleton, skel_points, dist_map


def build_centerline_costmap(orange_mask: np.ndarray) -> np.ndarray:
    """주황 마스크 내부 EDT 기반 중심선 costmap (서브픽셀 정밀)

    주황선 내부에서 edge까지의 거리(EDT)를
    local max로 정규화하여 중심=0, 가장자리=local_max 인 연속적 costmap 생성.

    Args:
        orange_mask: extract_orange_mask() 결과 (uint8, 0 or 255)

    Returns:
        cost_map (float32) — 중심≈0, 외부=30.0
    """
    mask_u8 = (orange_mask > 0).astype(np.uint8) * 255
    dt_inside = cv2.distanceTransform(mask_u8, cv2.DIST_L2, 5).astype(np.float32)
    max_dt = float(dt_inside.max())

    if max_dt < 0.5:
        return np.full(orange_mask.shape, 30.0, dtype=np.float32)

    # local max: cv2.dilate (maximum_filter 대체, 훨씬 빠름)
    win = max(3, min(int(2 * max_dt) + 1, 21))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (win, win))
    local_max = cv2.dilate(dt_inside, kernel)

    binary = orange_mask > 0
    cost_map = np.where(binary, local_max - dt_inside, 30.0).astype(np.float32)

    inside_cost = cost_map[binary]
    print(f"  [중심선costmap] max_dt={max_dt:.1f}px, win={win}, "
          f"내부 cost mean={inside_cost.mean():.2f}, median={np.median(inside_cost):.2f}")
    return cost_map


def refine_position_subpixel(
    shp_points: np.ndarray,
    dist_map: np.ndarray,
    centerline_costmap: np.ndarray,
    init_pixel_size: float,
    init_offset_x: float,
    init_offset_y: float,
    image_shape: Tuple[int, int],
) -> Tuple[float, float, float, float]:
    """서브픽셀 정밀 리파인 (2-costmap 결합)

    centerline costmap(EDT 기반, 중심방향 그래디언트)과
    skeleton dist_map(bilinear, 정밀 cost)을 결합.
    이미 수렴된 해의 미세조정용 — ftol=1e-8, maxiter=50.

    Returns:
        (pixel_size, offset_x, offset_y, final_cost) — cost는 dist_map 기준
    """
    from scipy.optimize import minimize
    from scipy.ndimage import map_coordinates

    h, w = image_shape
    margin = 5

    def cost_combined(params):
        s, tx, ty = params
        if s <= 0:
            return 1e10

        fx = (shp_points[:, 0] - tx) / s
        fy = (ty - shp_points[:, 1]) / s

        valid = ((fx >= margin) & (fx < w - margin) &
                 (fy >= margin) & (fy < h - margin))
        n_valid = valid.sum()
        if n_valid < 50:
            return 1e10

        fy_v, fx_v = fy[valid], fx[valid]
        # skeleton dist_map (bilinear) — 정밀 비용
        d_skel = map_coordinates(
            dist_map, [fy_v, fx_v], order=1, mode='constant', cval=30.0
        )
        # centerline costmap (bilinear) — 중심방향 보조 그래디언트
        d_cl = map_coordinates(
            centerline_costmap, [fy_v, fx_v], order=1, mode='constant', cval=30.0
        )
        # 결합: skeleton 주도 + centerline 보조 (가중치 0.3)
        return float(np.mean(d_skel) + 0.3 * np.mean(d_cl))

    def cost_skel_only(params):
        """skeleton dist_map만으로 평가 (최종 cost 산출)"""
        s, tx, ty = params
        if s <= 0:
            return 1e10
        fx = (shp_points[:, 0] - tx) / s
        fy = (ty - shp_points[:, 1]) / s
        valid = ((fx >= margin) & (fx < w - margin) &
                 (fy >= margin) & (fy < h - margin))
        if valid.sum() < 50:
            return 1e10
        d = map_coordinates(
            dist_map, [fy[valid], fx[valid]], order=1, mode='constant', cval=30.0
        )
        return float(np.mean(d))

    init = np.array([init_pixel_size, init_offset_x, init_offset_y])
    result = minimize(cost_combined, init, method="Powell",
                      options={"maxiter": 50, "ftol": 1e-8})

    ps, ox, oy = result.x
    # 최종 cost는 skeleton dist_map 기준으로 재평가
    final_cost = cost_skel_only(result.x)
    print(f"  [SubpixelRefine] cost={final_cost:.4f}px, "
          f"ps={ps:.6f} (Δ{(ps-init_pixel_size)*1e6:.1f}μm), "
          f"offset Δ=({ox-init_offset_x:.4f}, {oy-init_offset_y:.4f})m")
    return ps, ox, oy, final_cost


# ============================================================
# 초기 파라미터 추정 (레거시, 자료/generate_intermediate_outputs.py 전용)
# ============================================================

def estimate_initial_params(
    skel_points: np.ndarray,
    main_geom,
    map_shape: Tuple[int, int],
    scale: Optional[int] = None,
    dpi: int = DEFAULT_DPI,
) -> Tuple[float, float, float]:
    """초기 (pixel_size, offset_x, offset_y) 추정

    Args:
        skel_points: 스켈레톤 점 (x, y) 배열
        main_geom: 중앙 행정구역 geometry
        map_shape: (height, width)
        scale: 축척 분모 (예: 14957)
        dpi: 이미지 DPI

    Returns:
        (pixel_size, offset_x, offset_y)
    """
    h, w = map_shape

    if scale is not None:
        pixel_size = scale * 0.0254 / dpi
    else:
        shp_bounds = main_geom.bounds
        shp_w = shp_bounds[2] - shp_bounds[0]
        shp_h = shp_bounds[3] - shp_bounds[1]
        skel_w = skel_points[:, 0].max() - skel_points[:, 0].min()
        skel_h = skel_points[:, 1].max() - skel_points[:, 1].min()
        pixel_size = max(shp_w / max(skel_w, 1), shp_h / max(skel_h, 1))

    main_centroid = main_geom.centroid
    skel_cx = skel_points[:, 0].mean()
    skel_cy = skel_points[:, 1].mean()
    offset_x = main_centroid.x - skel_cx * pixel_size
    offset_y = main_centroid.y - skel_cy * (-pixel_size)

    print(f"  [초기추정] pixel_size={pixel_size:.4f} m/px, offset=({offset_x:.1f}, {offset_y:.1f})")
    return pixel_size, offset_x, offset_y


# ============================================================
# AOI 계산 및 SHP 클리핑
# ============================================================

def compute_aoi(
    offset_x: float, offset_y: float, pixel_size: float, map_shape: Tuple[int, int]
):
    """이미지가 커버하는 지리적 범위 (AOI) → shapely box"""
    from shapely.geometry import box

    h, w = map_shape
    return box(
        offset_x,
        offset_y + h * (-pixel_size),
        offset_x + w * pixel_size,
        offset_y,
    )


def clip_shp_to_aoi(
    gdf, aoi_box, buffer_ratio: float = 0.3
) -> List:
    """AOI(+버퍼)와 교차하는 SHP 경계를 클리핑 (공간 인덱스 활용)

    Returns:
        클리핑된 경계 geometry 리스트
    """
    from shapely.geometry import MultiPolygon, box

    bx = (aoi_box.bounds[2] - aoi_box.bounds[0]) * buffer_ratio
    by = (aoi_box.bounds[3] - aoi_box.bounds[1]) * buffer_ratio
    search_box = box(
        aoi_box.bounds[0] - bx,
        aoi_box.bounds[1] - by,
        aoi_box.bounds[2] + bx,
        aoi_box.bounds[3] + by,
    )

    # 공간 인덱스로 후보 축소 후 정밀 intersects 확인
    if hasattr(gdf, 'sindex'):
        candidate_idx = list(gdf.sindex.intersection(search_box.bounds))
        candidates = gdf.iloc[candidate_idx]
        visible = candidates[candidates.geometry.intersects(search_box)]
    else:
        visible = gdf[gdf.geometry.intersects(search_box)]
    print(f"  [클리핑] AOI 교차 행정구역: {len(visible)}개")

    all_boundaries = []
    for _, row in visible.iterrows():
        geom = row.geometry
        if isinstance(geom, MultiPolygon):
            for poly in geom.geoms:
                clipped = poly.boundary.intersection(search_box)
                if not clipped.is_empty:
                    all_boundaries.append(clipped)
        else:
            clipped = geom.boundary.intersection(search_box)
            if not clipped.is_empty:
                all_boundaries.append(clipped)
    return all_boundaries


def sample_points_from_boundaries(
    boundaries: List, num_points: int = 2000
) -> np.ndarray:
    """경계 LineString들에서 균등하게 점 샘플링"""
    total_length = sum(
        b.length for b in boundaries if hasattr(b, "length") and b.length > 0
    )
    if total_length == 0:
        return np.empty((0, 2))

    all_pts = []
    for b in boundaries:
        if not hasattr(b, "length") or b.length == 0:
            continue
        n = max(5, int(num_points * b.length / total_length))
        if b.geom_type == "MultiLineString":
            for line in b.geoms:
                nl = max(3, int(n * line.length / b.length))
                dists = np.linspace(0, line.length, nl)
                all_pts.extend([[p.x, p.y] for p in (line.interpolate(d) for d in dists)])
        elif b.geom_type == "LineString":
            dists = np.linspace(0, b.length, n)
            all_pts.extend([[p.x, p.y] for p in (b.interpolate(d) for d in dists)])
        elif b.geom_type == "GeometryCollection":
            for g in b.geoms:
                if g.geom_type == "LineString" and g.length > 0:
                    nl = max(3, int(n * g.length / b.length))
                    dists = np.linspace(0, g.length, nl)
                    all_pts.extend([[p.x, p.y] for p in (g.interpolate(d) for d in dists)])

    pts = np.array(all_pts) if all_pts else np.empty((0, 2))
    print(f"  [샘플링] SHP 경계점: {len(pts):,}개")
    return pts


# ============================================================
# 최적화 (ICP 레거시 + Powell 리파인)
# ============================================================

def icp_optimize(
    shp_points: np.ndarray,
    dist_map: np.ndarray,
    init_pixel_size: float,
    init_offset_x: float,
    init_offset_y: float,
    image_shape: Tuple[int, int],
    fix_scale: bool = True,
) -> Tuple[float, float, float, float]:
    """Distance Transform 기반 ICP 최적화 (Truncated Mean)

    Args:
        shp_points: SHP 경계 점 (world 좌표)
        dist_map: 스켈레톤 distance transform
        init_pixel_size: 초기 픽셀 크기
        init_offset_x: 초기 X 오프셋
        init_offset_y: 초기 Y 오프셋
        image_shape: (height, width)
        fix_scale: True면 스케일 고정, False면 스케일도 최적화

    Returns:
        (pixel_size, offset_x, offset_y, final_cost)
    """
    from scipy.optimize import minimize

    h, w = image_shape
    shp_pts = shp_points
    dist_cap = 30.0

    def cost(params):
        if fix_scale:
            tx, ty = params
            s = init_pixel_size
        else:
            s, tx, ty = params
            if s <= 0:
                return 1e10

        ix = np.round((shp_pts[:, 0] - tx) / s).astype(np.int32)
        iy = np.round((shp_pts[:, 1] - ty) / (-s)).astype(np.int32)

        valid = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
        n_valid = valid.sum()
        if n_valid < 10:
            return 1e10

        distances = np.minimum(dist_map[iy[valid], ix[valid]], dist_cap)
        penalty = (len(shp_pts) - n_valid) * dist_cap
        return (distances.sum() + penalty) / len(shp_pts)

    if fix_scale:
        init_params = np.array([init_offset_x, init_offset_y])
    else:
        init_params = np.array([init_pixel_size, init_offset_x, init_offset_y])

    r1 = minimize(cost, init_params, method="Nelder-Mead",
                  options={"maxiter": 500, "xatol": 0.5, "fatol": 0.01, "adaptive": True})
    r2 = minimize(cost, r1.x, method="Powell",
                  options={"maxiter": 300, "ftol": 1e-8})

    best = r2 if r2.fun <= r1.fun else r1

    if fix_scale:
        ps, ox, oy = init_pixel_size, best.x[0], best.x[1]
    else:
        ps, ox, oy = best.x[0], best.x[1], best.x[2]

    print(f"  [ICP] cost={best.fun:.2f}px, pixel_size={ps:.4f}, offset=({ox:.1f}, {oy:.1f})")
    return ps, ox, oy, best.fun


def refine_position(
    shp_points: np.ndarray,
    dist_map: np.ndarray,
    init_pixel_size: float,
    init_offset_x: float,
    init_offset_y: float,
    image_shape: Tuple[int, int],
    fix_scale: bool = False,
) -> Tuple[float, float, float, float]:
    """FFT 결과를 정밀 리파인 (바이리니어 보간 + Powell)

    icp_optimize와 달리:
    - 바이리니어 보간으로 부드러운 cost 함수
    - 이미지 안쪽 점만 사용 (경계 벌점 없음)
    - Powell 1회 (이미 좋은 초기값 가정)

    Args:
        fix_scale: True면 pixel_size 고정, offset만 최적화

    Returns:
        (pixel_size, offset_x, offset_y, final_cost)
    """
    from scipy.optimize import minimize
    from scipy.ndimage import map_coordinates

    h, w = image_shape
    margin = 5  # 가장자리 5px 제외

    def cost(params):
        if fix_scale:
            s = init_pixel_size
            tx, ty = params
        else:
            s, tx, ty = params
        if s <= 0:
            return 1e10

        fx = (shp_points[:, 0] - tx) / s
        fy = (ty - shp_points[:, 1]) / s

        valid = ((fx >= margin) & (fx < w - margin) &
                 (fy >= margin) & (fy < h - margin))
        n_valid = valid.sum()
        if n_valid < 50:
            return 1e10

        # 바이리니어 보간 (부드러운 그래디언트)
        dists = map_coordinates(
            dist_map, [fy[valid], fx[valid]], order=1, mode='constant', cval=30.0
        )
        return float(np.mean(dists))

    if fix_scale:
        init = np.array([init_offset_x, init_offset_y])
    else:
        init = np.array([init_pixel_size, init_offset_x, init_offset_y])
    result = minimize(cost, init, method="Powell",
                      options={"maxiter": 300, "ftol": 1e-6})

    if fix_scale:
        ps = init_pixel_size
        ox, oy = result.x
    else:
        ps, ox, oy = result.x
    print(f"  [Refine] cost={result.fun:.3f}px, "
          f"ps={ps:.6f} (Δ{(ps-init_pixel_size)*1e6:.1f}μm), "
          f"offset Δ=({ox-init_offset_x:.3f}, {oy-init_offset_y:.3f})m")
    return ps, ox, oy, result.fun


# ============================================================
# GCP 생성
# ============================================================

def extract_all_vertices(boundaries: List, max_vertices: int = 500) -> np.ndarray:
    """클리핑된 경계에서 꼭짓점 추출"""
    verts = []
    for b in boundaries:
        if b.geom_type == "LineString":
            verts.extend(list(b.coords))
        elif b.geom_type == "MultiLineString":
            for line in b.geoms:
                verts.extend(list(line.coords))
        elif b.geom_type == "GeometryCollection":
            for g in b.geoms:
                if g.geom_type == "LineString":
                    verts.extend(list(g.coords))
    pts = np.array(verts) if verts else np.empty((0, 2))
    if len(pts) > max_vertices:
        idx = np.round(np.linspace(0, len(pts) - 1, max_vertices)).astype(int)
        pts = pts[idx]
    return pts


def create_gcps(
    boundaries: List,
    skel_points: np.ndarray,
    pixel_size: float,
    offset_x: float,
    offset_y: float,
    map_bbox: Tuple[int, int, int, int],
    image_shape: Tuple[int, int],
    dist_threshold_px: float = 8.0,
    max_gcps: int = 80,
    min_spacing_px: float = 50.0,
) -> List:
    """SHP 꼭짓점 → 스켈레톤 최근접점으로 GCP 생성 (중복 제거 + 균등 분포)

    Returns:
        List[gdal.GCP]
    """
    from osgeo import gdal
    from scipy.spatial import cKDTree

    h, w = image_shape
    map_x, map_y, _, _ = map_bbox
    skel_tree = cKDTree(skel_points)

    vertices = extract_all_vertices(boundaries)
    if len(vertices) == 0:
        print("  [GCP] 꼭짓점 없음!")
        return []

    # 후보 수집 (거리순)
    candidates = []
    for wx, wy in vertices:
        img_x = (wx - offset_x) / pixel_size
        img_y = (wy - offset_y) / (-pixel_size)
        if not (0 <= img_x < w and 0 <= img_y < h):
            continue
        dist, idx = skel_tree.query([img_x, img_y])
        if dist > dist_threshold_px:
            continue
        snap_x, snap_y = skel_points[idx]
        candidates.append((dist, wx, wy, float(map_x + snap_x), float(map_y + snap_y)))

    candidates.sort(key=lambda c: c[0])

    # 최소 간격 필터링
    gcps = []
    used = []
    dists_log = []

    for dist, wx, wy, px, py in candidates:
        if any(abs(px - ux) < min_spacing_px and abs(py - uy) < min_spacing_px for ux, uy in used):
            continue
        gcps.append(gdal.GCP(float(wx), float(wy), 0.0, px, py))
        used.append((px, py))
        dists_log.append(dist)
        if len(gcps) >= max_gcps:
            break

    if dists_log:
        d = np.array(dists_log)
        print(f"  [GCP] {len(gcps)}개 (후보 {len(candidates)}개, "
              f"거리 mean={d.mean():.1f}px, median={np.median(d):.1f}px, max={d.max():.1f}px)")
    else:
        print("  [GCP] 0개 - 임계값 초과")
    return gcps


# ============================================================
# VRT 출력
# ============================================================

def write_vrt_output(
    image_path: str, gcps: List, output_dir: str, base_name: str
) -> Tuple[str, str]:
    """GCP VRT + TPS Warp VRT 생성

    Returns:
        (gcp_vrt_path, tps_vrt_path)
    """
    from osgeo import gdal

    os.makedirs(output_dir, exist_ok=True)
    gcp_vrt = os.path.join(output_dir, f"{base_name}_gcp.vrt")
    tps_vrt = os.path.join(output_dir, f"{base_name}_tps.vrt")

    gdal.Translate(gcp_vrt, image_path,
                   options=gdal.TranslateOptions(format="VRT", GCPs=gcps, outputSRS=CRS_EPSG))
    print(f"  [VRT] GCP VRT: {gcp_vrt}")

    gdal.Warp(tps_vrt, gcp_vrt,
              options=gdal.WarpOptions(format="VRT", tps=True, dstSRS=CRS_EPSG,
                                       resampleAlg="near", dstNodata=0))
    print(f"  [VRT] TPS Warp VRT: {tps_vrt}")
    return gcp_vrt, tps_vrt


# ------------------------------------------------------------------
# FFT 템플릿 매칭 (Proximity Map 방식)
# ------------------------------------------------------------------

def build_proximity_map(dist_map: np.ndarray, sigma: float = 15.0) -> np.ndarray:
    """Distance Transform → Gaussian proximity map

    스켈레톤 근처일수록 값이 높은 부드러운 밀도맵 생성.
    FFT 교차상관의 신호를 극대화하기 위해 사용.

    Args:
        dist_map: build_skeleton_and_distmap()에서 반환된 distance transform
        sigma: Gaussian 표준편차 (px). 클수록 attraction range 넓어짐.

    Returns:
        proximity map (float32, 0~1)
    """
    return np.exp(-dist_map ** 2 / (2 * sigma ** 2)).astype(np.float32)


def rasterize_boundaries(geometries, bounds, pixel_size: float) -> np.ndarray:
    """SHP 경계선을 래스터 이미지로 변환

    Args:
        geometries: Polygon/MultiPolygon geometry 리스트
        bounds: (minx, miny, maxx, maxy) 래스터 범위 (월드좌표)
        pixel_size: 래스터 해상도 (m/px)

    Returns:
        binary uint8 image (1=경계선, 0=배경)
    """
    minx, miny, maxx, maxy = bounds
    w = int((maxx - minx) / pixel_size) + 1
    h = int((maxy - miny) / pixel_size) + 1
    canvas = np.zeros((h, w), dtype=np.uint8)

    for geom in geometries:
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type == 'Polygon':
            polys = [geom]
        elif geom.geom_type == 'MultiPolygon':
            polys = list(geom.geoms)
        else:
            continue
        for poly in polys:
            for ring in [poly.exterior] + list(poly.interiors):
                coords = np.array(ring.coords)
                px = ((coords[:, 0] - minx) / pixel_size).astype(np.int32)
                py = ((maxy - coords[:, 1]) / pixel_size).astype(np.int32)
                pts = np.column_stack([px, py]).reshape(-1, 1, 2)
                cv2.polylines(canvas, [pts], True, 1, 1)
    return canvas


def _avg_pool_2d(arr: np.ndarray, factor: int) -> np.ndarray:
    """2D 평균 풀링 다운샘플"""
    h, w = arr.shape
    nh, nw = h // factor, w // factor
    return arr[:nh * factor, :nw * factor].reshape(
        nh, factor, nw, factor
    ).mean(axis=(1, 3))


def fft_match_position(
    proximity: np.ndarray,
    raster_8x: np.ndarray,
    raster_4x: np.ndarray,
    aoi: tuple,
    pixel_size: float,
) -> tuple:
    """FFT 교차상관으로 이미지의 월드좌표 위치 탐색

    8x 축소에서 거친 탐색 → 4x 축소에서 윈도우 정밀 탐색 + 서브픽셀 보간.

    Args:
        proximity: build_proximity_map()의 출력 (원본 해상도)
        raster_8x: rasterize_boundaries()의 출력 (pixel_size*8 해상도)
        raster_4x: rasterize_boundaries()의 출력 (pixel_size*4 해상도)
        aoi: (minx, miny, maxx, maxy) 래스터 범위
        pixel_size: 이미지의 pixel size (m/px)

    Returns:
        (offset_x, offset_y) — 지도영역 (0,0)의 월드좌표
    """
    from scipy.signal import fftconvolve

    ds_c, ds_f = 8, 4

    # 8x 거친 탐색
    prox_8x = _avg_pool_2d(proximity, ds_c)
    corr_8x = fftconvolve(
        raster_8x.astype(np.float32),
        prox_8x[::-1, ::-1],
        mode='valid',
    )
    if corr_8x.size == 0:
        return None
    yi8, xi8 = np.unravel_index(np.argmax(corr_8x), corr_8x.shape)

    # 4x 윈도우 정밀 탐색
    prox_4x = _avg_pool_2d(proximity, ds_f)
    yi4c, xi4c = yi8 * 2, xi8 * 2
    ph4, pw4 = prox_4x.shape
    win = 50
    ys = max(0, yi4c - win)
    xs = max(0, xi4c - win)
    ye = min(raster_4x.shape[0], yi4c + ph4 + win)
    xe = min(raster_4x.shape[1], xi4c + pw4 + win)
    raster_win = raster_4x[ys:ye, xs:xe]

    corr_4x = fftconvolve(
        raster_win.astype(np.float32),
        prox_4x[::-1, ::-1],
        mode='valid',
    )
    if corr_4x.size == 0:
        return None
    yi4l, xi4l = np.unravel_index(np.argmax(corr_4x), corr_4x.shape)
    yi4, xi4 = ys + yi4l, xs + xi4l

    # 서브픽셀 보간
    h_c, w_c = corr_4x.shape
    dx_sub, dy_sub = 0.0, 0.0
    if 0 < xi4l < w_c - 1:
        a, bv, c = corr_4x[yi4l, xi4l - 1], corr_4x[yi4l, xi4l], corr_4x[yi4l, xi4l + 1]
        d = a - 2 * bv + c
        if abs(d) > 1e-10:
            dx_sub = 0.5 * (a - c) / d
    if 0 < yi4l < h_c - 1:
        a, bv, c = corr_4x[yi4l - 1, xi4l], corr_4x[yi4l, xi4l], corr_4x[yi4l + 1, xi4l]
        d = a - 2 * bv + c
        if abs(d) > 1e-10:
            dy_sub = 0.5 * (a - c) / d

    eff = pixel_size * ds_f
    offset_x = aoi[0] + (xi4 + dx_sub) * eff
    offset_y = aoi[3] - (yi4 + dy_sub) * eff

    return (offset_x, offset_y)


# ============================================================
# 행정리 폴리곤 → PDF 픽셀 마스크
#   Stage 3 매칭에서 행정리 영역 안 inlier 만 선별 (정합 정확도 ↑)
# ============================================================

# (shp_path, admin_cd) → list[ring(world)] 캐시 (한 시트 처리당 N admin → N 회 호출)
_ADMIN_POLYGON_CACHE: dict = {}
_DEFAULT_SHP = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'bnd_adm_pg.shp')


def load_admin_polygon_world(admin_cd: str,
                              shp_path: Optional[str] = None) -> List[np.ndarray]:
    """SHP 에서 admin_cd 폴리곤 → world 좌표 ring 리스트.

    캐시: 같은 (shp, cd) 두 번째 호출은 즉시 반환.
    Returns: [ring0(N0,2), ring1(N1,2), ...]  (외곽 + 내부 hole 포함)
    빈 리스트면 폴리곤 없음.
    """
    shp_path = shp_path or _DEFAULT_SHP
    key = (shp_path, admin_cd)
    if key in _ADMIN_POLYGON_CACHE:
        return _ADMIN_POLYGON_CACHE[key]

    from osgeo import ogr  # 지연 import: SHP 안 쓰는 경로엔 의존성 없게
    if not os.path.exists(shp_path):
        # 캐시도 빈 결과로 — 매 호출 파일 stat 안 함
        _ADMIN_POLYGON_CACHE[key] = []
        return []
    ds = ogr.Open(shp_path)
    if ds is None:
        _ADMIN_POLYGON_CACHE[key] = []
        return []
    lyr = ds.GetLayer()
    lyr.SetAttributeFilter(f"adm_cd = '{admin_cd}'")
    rings = []
    for feat in lyr:
        g = feat.GetGeometryRef()
        if g is None:
            continue
        gt = g.GetGeometryType()
        polys = ([g.GetGeometryRef(i) for i in range(g.GetGeometryCount())]
                 if gt == ogr.wkbMultiPolygon else [g])
        for poly in polys:
            for j in range(poly.GetGeometryCount()):
                r = poly.GetGeometryRef(j)
                pts = np.array(
                    [(r.GetX(k), r.GetY(k)) for k in range(r.GetPointCount())],
                    dtype=np.float64)
                rings.append(pts)
    # 빈 결과(없는 admin)도 캐시 — 같은 코드 재조회 시 SHP 재스캔 회피
    _ADMIN_POLYGON_CACHE[key] = rings
    return rings


def build_admin_polygon_mask(admin_cd: str, jgw: 'JGWParams',
                              image_shape: Tuple[int, int],
                              shp_path: Optional[str] = None) -> np.ndarray:
    """행정리 폴리곤을 PDF 이미지 좌표계로 투영해 uint8 마스크 생성.

    Args:
      jgw          참조 PDF 의 JGW (rotation 0 가정)
      image_shape  (H, W) — 마스크 크기

    Returns: (H,W) uint8, 폴리곤 내부=255, 외부=0. 폴리곤 없거나
             시트에 안 들어오면 전체 0 마스크.
    """
    H, W = image_shape[:2]
    mask = np.zeros((H, W), dtype=np.uint8)
    rings = load_admin_polygon_world(admin_cd, shp_path)
    if not rings:
        return mask
    # rotation 0 가정 — pixel = (world - origin) / px_size
    A = jgw.pixel_size_x; E = jgw.pixel_size_y
    C = jgw.top_left_x; F = jgw.top_left_y
    contours = []
    for ring in rings:
        col = (ring[:, 0] - C) / A
        row = (ring[:, 1] - F) / E
        pts = np.column_stack([col, row]).astype(np.int32)
        # 시트 밖 점은 클리핑 (cv2.fillPoly 가 알아서 잘라주지만 명시)
        pts[:, 0] = np.clip(pts[:, 0], -1, W)
        pts[:, 1] = np.clip(pts[:, 1], -1, H)
        if len(pts) < 3:
            continue
        contours.append(pts)
    if contours:
        cv2.fillPoly(mask, contours, 255)
    return mask
