"""
스캔이미지-경계 검수 스크립트

목적: 주황색 폴리곤(이미지) 밖에 SHP 경계가 있는 지역을 시각화
→ 수동 수정이 필요한 영역 표시

1. 이미지에서 주황색 폴리곤 영역 추출
2. SHP 경계선 로드
3. 주황색 폴리곤 밖에 있는 SHP 경계 부분 검출
4. 문제 영역 시각화 (빨간색 하이라이트)
"""

import cv2
import numpy as np
import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, Polygon, MultiPolygon
from shapely.ops import unary_union
from rasterio.transform import from_bounds
from rasterio.features import shapes
from scipy import ndimage
import matplotlib.pyplot as plt
import koreanize_matplotlib
import os
import re

import pytesseract

plt.rcParams['axes.unicode_minus'] = False


def order_points(pts):
    """4개의 좌표를 [top-left, top-right, bottom-right, bottom-left] 순서로 정렬"""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def extract_map_region(image):
    """
    문서 이미지에서 지도 영역만 추출
    Returns: (추출된 이미지, 원본에서의 바운딩박스 [x, y, w, h])
    """
    h, w = image.shape[:2]

    # 전처리
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 21, 10)
    kernel = np.ones((5, 5), np.uint8)
    morph = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)

    # 컨투어 검출
    contours, _ = cv2.findContours(morph, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [c for c in contours if cv2.contourArea(c) > (h * w * 0.2)]
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for cnt in contours[:3]:
        area_ratio = cv2.contourArea(cnt) / (h * w)
        if area_ratio > 0.95:
            continue

        hull = cv2.convexHull(cnt)
        peri = cv2.arcLength(hull, True)
        approx = cv2.approxPolyDP(hull, 0.02 * peri, True)

        if len(approx) == 4:
            # 투영 변환
            rect = order_points(approx.reshape(4, 2))
            (tl, tr, br, bl) = rect

            widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
            widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
            maxWidth = max(int(widthA), int(widthB))

            heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
            heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
            maxHeight = max(int(heightA), int(heightB))

            dst = np.array([[0, 0], [maxWidth - 1, 0],
                           [maxWidth - 1, maxHeight - 1], [0, maxHeight - 1]], dtype="float32")

            M = cv2.getPerspectiveTransform(rect, dst)
            warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))

            # 바운딩 박스 (원본 좌표 기준)
            x, y, rw, rh = cv2.boundingRect(approx)

            return warped, rect, (x, y, rw, rh)

    # 실패 시 원본 반환
    return image, None, (0, 0, w, h)


class BoundaryValidator:
    """스캔이미지와 Shapefile 경계 비교 검수"""

    def __init__(self, image_path: str, jgw_path: str, national_shp_path: str):
        """
        Args:
            image_path: 스캔 이미지 경로
            jgw_path: World 파일 경로 (.jgw)
            national_shp_path: 전국 행정읍면동경계 Shapefile 경로
        """
        self.image_path = image_path
        self.jgw_path = jgw_path
        self.national_shp_path = national_shp_path

        self.original_image = None
        self.image = None  # 지도 이미지
        self.admin_code = None
        self.transform_params = None
        self.map_transform_params = None
        self.orange_polygon = None  # 주황색 폴리곤 (Shapely geometry)
        self.shp_boundary_gdf = None
        self.problem_gdf = None  # 문제 영역 (수동 수정 필요)

    def load_image(self):
        """이미지 로드 (한글 경로 지원, 파일 잠금 회피)"""
        try:
            # 공유 모드로 읽기 (Windows 파일 잠금 회피)
            with open(self.image_path, 'rb') as f:
                img_bytes = f.read()
            img_array = np.frombuffer(img_bytes, dtype=np.uint8)
            self.original_image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        except Exception:
            # 폴백: 기존 방식
            img_array = np.fromfile(self.image_path, dtype=np.uint8)
            self.original_image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

        if self.original_image is None:
            raise FileNotFoundError(f"이미지 로드 실패: {self.image_path}")
        print(f"원본 이미지 로드: {self.original_image.shape[1]} x {self.original_image.shape[0]}")
        return self

    def extract_map_region(self):
        """문서에서 지도 영역만 추출"""
        self.image, corners, bbox = extract_map_region(self.original_image)
        print(f"지도 영역 추출: {self.image.shape[1]} x {self.image.shape[0]}")

        # 좌표 재계산
        if corners is not None and self.transform_params:
            params = self.transform_params
            orig_h, orig_w = self.original_image.shape[:2]

            # 4개 코너의 지리 좌표 계산
            geo_corners = []
            for px, py in corners:
                geo_x = params['top_left_x'] + px * params['pixel_size_x']
                geo_y = params['top_left_y'] + py * params['pixel_size_y']
                geo_corners.append((geo_x, geo_y))

            # 추출된 이미지의 새 좌표 파라미터
            tl, tr, br, bl = geo_corners
            new_w, new_h = self.image.shape[1], self.image.shape[0]

            self.map_transform_params = {
                'pixel_size_x': (tr[0] - tl[0]) / new_w,
                'pixel_size_y': (bl[1] - tl[1]) / new_h,
                'top_left_x': tl[0],
                'top_left_y': tl[1],
            }
            print(f"지도 영역 좌표 재계산 완료")
        else:
            # 원본 좌표 그대로 사용
            self.map_transform_params = self.transform_params

        return self

    def load_jgw(self):
        """JGW 파일에서 좌표 정보 로드"""
        with open(self.jgw_path, 'r') as f:
            lines = f.readlines()

        self.transform_params = {
            'pixel_size_x': float(lines[0].strip()),
            'rotation_x': float(lines[1].strip()),
            'rotation_y': float(lines[2].strip()),
            'pixel_size_y': float(lines[3].strip()),
            'top_left_x': float(lines[4].strip()),
            'top_left_y': float(lines[5].strip()),
        }
        print(f"좌표 정보 로드: 픽셀 크기 {self.transform_params['pixel_size_x']:.2f}m")
        return self

    def extract_admin_code_ocr(self) -> str:
        """이미지 헤더에서 행정리경계코드 OCR 추출 (pytesseract 사용)"""
        # 원본 이미지에서 헤더 추출 (지도 추출 전 원본 사용)
        h, w = self.original_image.shape[:2]

        # 헤더 영역 추출 (상단 8%)
        header = self.original_image[0:int(h*0.08), :]

        # 그레이스케일 변환
        if len(header.shape) == 3:
            gray = cv2.cvtColor(header, cv2.COLOR_BGR2GRAY)
        else:
            gray = header

        # pytesseract OCR 수행 (숫자 우선)
        all_text = pytesseract.image_to_string(gray, config='--psm 6 -c tessedit_char_whitelist=0123456789')
        print(f"OCR 인식 텍스트: {all_text.strip()[:100]}...")

        # 8자리 숫자 패턴 찾기
        matches = re.findall(r'\d{8}', all_text)

        if matches:
            self.admin_code = matches[0]
            print(f"OCR 추출 행정리경계코드: {self.admin_code}")
        else:
            print("OCR에서 코드 추출 실패. 파일명에서 추출 시도...")
            self._extract_code_from_filename()

        return self.admin_code

    def _extract_code_from_filename(self) -> str:
        """파일명에서 행정리경계코드 추출 (fallback)"""
        filename = os.path.basename(self.image_path)
        match = re.search(r'(\d{8})', filename)
        if match:
            self.admin_code = match.group(1)
            print(f"파일명에서 추출한 코드: {self.admin_code}")
        else:
            raise ValueError("행정리경계코드를 찾을 수 없습니다")
        return self.admin_code

    def extract_orange_polygon(self, threshold_px: float = 8.0):
        """
        주황색 마스크 + 거리맵 생성 (픽셀 기반 비교)

        거리맵 방식: 각 픽셀에서 가장 가까운 주황 픽셀까지의 거리를 계산.
        SHP 경계점이 주황 픽셀로부터 threshold_px 이내면 정상 판정.

        기존 Polygon/LineString 방식은 컨투어 단순화·곡선부에서 오검출이 심했음.

        Args:
            threshold_px: 허용 거리 (픽셀) — SHP 점이 이 거리 안에 주황 픽셀이 있으면 OK
        """
        h, w = self.image.shape[:2]

        # HSV 변환 및 주황색 마스크
        hsv = cv2.cvtColor(self.image, cv2.COLOR_BGR2HSV)
        lower_orange = np.array([5, 50, 100])
        upper_orange = np.array([25, 255, 255])
        mask = cv2.inRange(hsv, lower_orange, upper_orange)

        # 노이즈 제거
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

        self.orange_mask = mask
        self.orange_threshold_px = threshold_px

        # 거리맵: 비주황 픽셀 → 최근접 주황 픽셀까지의 유클리드 거리
        inv_mask = cv2.bitwise_not(mask)
        self.orange_dist = cv2.distanceTransform(inv_mask, cv2.DIST_L2, 5)

        n_orange = np.sum(mask > 0)
        print(f"주황색 픽셀: {n_orange:,}, 거리맵 생성 (임계값: {threshold_px}px)")

        # 시각화용 주황선 LineString (큰 컨투어만)
        params = self.map_transform_params
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        min_perim = min(h, w) * 0.05

        lines = []
        for cnt in contours:
            if cv2.arcLength(cnt, True) < min_perim:
                continue
            coords = [(params['top_left_x'] + pt[0] * params['pixel_size_x'],
                        params['top_left_y'] + pt[1] * params['pixel_size_y'])
                       for pt in cnt.reshape(-1, 2)]
            if len(coords) >= 3:
                coords.append(coords[0])
                try:
                    line = LineString(coords)
                    if line.is_valid:
                        lines.append(line)
                except:
                    pass

        self.orange_polygon = unary_union(lines) if lines else None
        print(f"시각화용 주황선: {len(lines)}개")

        return self

    def extract_shp_boundary(self, output_shp_path: str = None):
        """이미지 범위(AOI) 내 Shapefile 경계 추출 및 클립"""
        from shapely.geometry import box

        gdf = gpd.read_file(self.national_shp_path)
        print(f"전국 Shapefile: {len(gdf)}개 읍면동")

        # 이미지 범위 계산 (AOI)
        h, w = self.image.shape[:2]
        params = self.map_transform_params

        min_x = params['top_left_x']
        max_y = params['top_left_y']
        max_x = min_x + (w * params['pixel_size_x'])
        min_y = max_y + (h * params['pixel_size_y'])

        # bbox 생성
        image_bbox = box(min_x, min_y, max_x, max_y)
        print(f"이미지 범위: ({min_x:.1f}, {min_y:.1f}) ~ ({max_x:.1f}, {max_y:.1f})")

        # 이미지 범위와 교차하는 SHP 추출
        matched = gdf[gdf.intersects(image_bbox)]
        print(f"이미지 범위 내 읍면동: {len(matched)}개")

        if len(matched) == 0:
            print("Warning: 이미지 범위 내 경계를 찾지 못함")
            self.shp_boundary_gdf = gpd.GeoDataFrame(columns=['geometry'], crs="EPSG:5179")
            return self

        if 'adm_nm' in matched.columns:
            print(f"읍면동: {', '.join(matched['adm_nm'].tolist())}")

        # 경계선 추출 후 이미지 범위로 클립
        boundaries = []
        for idx, row in matched.iterrows():
            boundary = row.geometry.boundary
            # 이미지 범위로 클립
            clipped = boundary.intersection(image_bbox)
            if not clipped.is_empty:
                boundaries.append({'geometry': clipped, 'source_idx': idx})

        self.shp_boundary_gdf = gpd.GeoDataFrame(boundaries, crs="EPSG:5179")
        print(f"클립된 경계선: {len(self.shp_boundary_gdf)}개")

        # Shapefile 저장
        if output_shp_path:
            os.makedirs(os.path.dirname(output_shp_path), exist_ok=True)
            self.shp_boundary_gdf.to_file(output_shp_path, encoding='utf-8')
            print(f"Shapefile 경계선 저장: {output_shp_path}")

        return self

    def _get_boundaries_in_image_extent(self, gdf):
        """이미지 범위 내 경계 추출 (fallback)"""
        from shapely.geometry import box

        h, w = self.image.shape[:2]
        params = self.transform_params

        min_x = params['top_left_x']
        max_y = params['top_left_y']
        max_x = min_x + (w * params['pixel_size_x'])
        min_y = max_y + (h * params['pixel_size_y'])

        image_bbox = box(min_x, min_y, max_x, max_y)
        return gdf[gdf.intersects(image_bbox)]

    def find_problem_areas(self, min_length: float = 10.0):
        """
        거리맵 기반 문제 영역 검출

        SHP 경계점을 픽셀 좌표로 변환 → 거리맵에서 최근접 주황 픽셀 거리 조회.
        임계값(threshold_px) 초과 구간을 문제 영역으로 판정.

        Args:
            min_length: 최소 길이 (미터) - 이보다 짧은 문제는 무시
        """
        if self.orange_dist is None:
            raise ValueError("먼저 주황선 추출을 실행하세요")
        if self.shp_boundary_gdf is None:
            raise ValueError("먼저 SHP 경계 추출을 실행하세요")

        params = self.map_transform_params
        ps_x = params['pixel_size_x']
        ps_y = params['pixel_size_y']
        ox = params['top_left_x']
        oy = params['top_left_y']
        h, w = self.image.shape[:2]
        threshold = self.orange_threshold_px

        def _extract_coords(geom):
            """geometry에서 좌표 리스트 추출"""
            if hasattr(geom, 'geoms'):
                result = []
                for g in geom.geoms:
                    result.extend(_extract_coords(g))
                return result
            return [list(geom.coords)]

        problem_geometries = []
        for idx, row in self.shp_boundary_gdf.iterrows():
            geom = row.geometry
            if geom is None:
                continue

            for coords in _extract_coords(geom):
                problem_segment = []

                for geo_x, geo_y in coords:
                    # 지리좌표 → 픽셀좌표
                    px = int(round((geo_x - ox) / ps_x))
                    py = int(round((geo_y - oy) / ps_y))

                    # 이미지 범위 밖 → 무시
                    if not (0 <= px < w and 0 <= py < h):
                        if len(problem_segment) >= 2:
                            self._flush_segment(problem_segment, idx,
                                                min_length, problem_geometries)
                        problem_segment = []
                        continue

                    dist = self.orange_dist[py, px]

                    if dist > threshold:
                        # 주황선에서 먼 점 → 문제
                        problem_segment.append((geo_x, geo_y))
                    else:
                        # 주황선 근처 → 정상, 이전 문제 구간 저장
                        if len(problem_segment) >= 2:
                            self._flush_segment(problem_segment, idx,
                                                min_length, problem_geometries)
                        problem_segment = []

                # 마지막 구간
                if len(problem_segment) >= 2:
                    self._flush_segment(problem_segment, idx,
                                        min_length, problem_geometries)

        if problem_geometries:
            self.problem_gdf = gpd.GeoDataFrame(
                problem_geometries, crs="EPSG:5179")
            total_length = self.problem_gdf['length'].sum()
            print(f"문제 영역: {len(self.problem_gdf)}개, "
                  f"총 {total_length:.1f}m (임계값 {threshold}px, 최소 {min_length}m)")
        else:
            self.problem_gdf = gpd.GeoDataFrame(
                columns=['geometry', 'source_idx', 'length'], crs="EPSG:5179")
            print(f"문제 영역 없음 (임계값 {threshold}px, 최소 {min_length}m)")

        return self

    @staticmethod
    def _flush_segment(points, source_idx, min_length, out_list):
        """연속 문제점 → LineString 변환, 최소 길이 이상이면 저장"""
        try:
            line = LineString(points)
            if line.is_valid and line.length >= min_length:
                out_list.append({
                    'geometry': line,
                    'source_idx': source_idx,
                    'length': line.length,
                })
        except:
            pass

    def visualize_problem_areas(self, output_path: str = "boundary_check.png"):
        """
        문제 영역 시각화

        - 주황색 선: 이미지에서 추출한 주황색 경계 (선으로만 표시)
        - 파란색: SHP 경계
        - 빨간색 굵은 선: 주황색 폴리곤 밖의 SHP 경계 (수동 수정 필요)
        """
        fig, ax = plt.subplots(figsize=(16, 20))

        # 주황선 표시 (LineString 직접 표시)
        if self.orange_polygon is not None:
            gpd.GeoSeries([self.orange_polygon], crs="EPSG:5179").plot(
                ax=ax, color='orange', linewidth=1.5, alpha=0.8
            )

        # SHP 경계 (파란색)
        if self.shp_boundary_gdf is not None and len(self.shp_boundary_gdf) > 0:
            self.shp_boundary_gdf.plot(
                ax=ax, color='#4169E1', linewidth=1.0,
                alpha=0.6
            )

        # 문제 영역 (빨간색 굵은 선 - 수동 수정 필요)
        if hasattr(self, 'problem_gdf') and len(self.problem_gdf) > 0:
            # 굵은 빨간선으로 강조
            self.problem_gdf.plot(
                ax=ax, color='red', linewidth=6, alpha=0.9
            )
            # 노란 테두리로 더 눈에 띄게
            self.problem_gdf.plot(
                ax=ax, color='yellow', linewidth=2, alpha=1.0
            )
            problem_info = f"수정 필요: {len(self.problem_gdf)}개소, 총 {self.problem_gdf['length'].sum():.1f}m"
        else:
            problem_info = "수정 필요 영역 없음"

        ax.set_title(f'경계 검수 결과: {self.admin_code}\n{problem_info}', fontsize=14, fontweight='bold')
        ax.set_aspect('equal')

        # 범례 수동 생성
        from matplotlib.lines import Line2D
        legend_elements = [
            Line2D([0], [0], color='orange', linewidth=2, label='주황선 (이미지)'),
            Line2D([0], [0], color='#4169E1', linewidth=2, label='SHP 경계'),
            Line2D([0], [0], color='red', linewidth=6, label='수정 필요 (주황선 밖)'),
        ]
        ax.legend(handles=legend_elements, loc='upper right', fontsize=10)

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        print(f"검수 결과 시각화 저장: {output_path}")
        return output_path

    def run(self, output_dir: str = "output", threshold_px: float = 8.0, min_problem_length: float = 10.0):
        """
        전체 프로세스 실행

        Args:
            output_dir: 출력 폴더
            threshold_px: 주황선 허용 거리 (픽셀) - 기본 8px
            min_problem_length: 최소 문제 길이 (미터) - 이보다 짧으면 무시

        Returns:
            dict: 검수 결과 (문제 영역 수, 총 길이 등)
        """
        os.makedirs(output_dir, exist_ok=True)

        print("=" * 50)
        print("경계 검수 시작")
        print("목적: 주황선 밖 SHP 경계 검출 → 수동 수정 필요 영역")
        print(f"설정: 임계값 {threshold_px}px, 최소 문제 길이 {min_problem_length}m")
        print("=" * 50)

        # 1. 데이터 로드
        self.load_image()
        self.load_jgw()

        # 2. 행정리경계코드 추출
        self.extract_admin_code_ocr()

        # 3. 지도 영역 추출 (헤더/범례 영역 제외)
        self.extract_map_region()

        # 4. 주황색 마스크 + 거리맵 생성
        self.extract_orange_polygon(threshold_px=threshold_px)

        # 5. SHP 경계 추출 (SHP 저장 안 함, PNG만 생성)
        self.extract_shp_boundary()

        # 6. 문제 영역 검출 (최소 길이 이상만)
        self.find_problem_areas(min_length=min_problem_length)

        # 7. 결과 시각화 (PNG만 생성) — 원본 파일명 기반
        base_name = os.path.splitext(os.path.basename(self.image_path))[0]
        output_image = os.path.join(output_dir, f"{base_name}_check_result.png")
        self.visualize_problem_areas(output_path=output_image)

        print("=" * 50)
        print("검수 완료")
        print("=" * 50)

        # 결과 반환
        result = {
            'admin_code': self.admin_code,
            'problem_count': len(self.problem_gdf) if hasattr(self, 'problem_gdf') else 0,
            'problem_length': self.problem_gdf['length'].sum() if hasattr(self, 'problem_gdf') and len(self.problem_gdf) > 0 else 0,
            'output_dir': output_dir,
            'output_image': output_image,
        }
        return result


if __name__ == "__main__":
    # 샘플 실행 - 병합 지도 사용
    validator = BoundaryValidator(
        image_path="01_스캔이미지와 경계검수/merged_map_v5.jpg",
        jgw_path="01_스캔이미지와 경계검수/merged_map_v5.jgw",
        national_shp_path="01_스캔이미지와 경계검수/3. 행정읍면동경계/1. 전국/bnd_adm_pg.shp"
    )

    result = validator.run(output_dir="boundary_check_output", buffer_distance=2.0)
    print(f"\n결과: {result}")
