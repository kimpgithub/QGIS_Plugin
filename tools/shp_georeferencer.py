"""
SHP 기반 이미지 좌표 부여 모듈 (FFT + Powell 방식)
- FFT 전역 탐색 + Powell 서브픽셀 리파인으로 SHP 경계와 이미지 주황선 정합
- GCP 생성 → JGW + VRT 출력
- 단일 이미지 또는 폴더 일괄 처리
"""

import os
import re
import glob
import time
import numpy as np
import geopandas as gpd
from shapely.geometry import MultiPolygon
from typing import Optional

from .common import (
    JGWParams,
    load_image,
    extract_map_region,
    extract_orange_mask,
    write_jgw,
    convert_pdf_to_images,
    get_image_dpi,
    build_skeleton_and_distmap,
    compute_aoi,
    clip_shp_to_aoi,
    sample_points_from_boundaries,
    refine_position,
    create_gcps,
    write_vrt_output,
    build_proximity_map,
    rasterize_boundaries,
    fft_match_position,
)

IMAGE_EXTENSIONS = ('*.jpg', '*.jpeg', '*.tif', '*.tiff', '*.pdf')


class SHPGeoreferencer:
    """SHP 기반 이미지 좌표 부여기 (FFT + Powell)

    FFT 전역 탐색 → Powell 서브픽셀 리파인.
    단일 이미지 또는 폴더 일괄 처리 지원.
    """

    def __init__(self, national_shp_path: str):
        self.national_shp_path = national_shp_path
        self.gdf = gpd.read_file(national_shp_path, encoding='cp949')
        self._valid_codes = set(self.gdf['adm_cd'].astype(str).tolist())
        self._admin_code_candidates = None
        print(f"SHP 로드: {len(self.gdf)}개 행정구역")

    # ------------------------------------------------------------------
    # PDF 텍스트 직접 추출
    # ------------------------------------------------------------------

    def _extract_info_from_pdf(self, pdf_path: str) -> dict:
        """PDF에서 텍스트를 직접 추출하여 행정코드/축척 파싱

        PDF는 벡터 텍스트가 내장되어 있으므로 OCR 불필요.

        Returns:
            {'admin_code': str or None, 'scale': int or None, 'text': str}
        """
        import fitz

        doc = fitz.open(pdf_path)
        text = doc[0].get_text()
        doc.close()

        print(f"  [PDF 텍스트] {text[:200].replace(chr(10), ' ')}...")

        result = {'admin_code': None, 'scale': None, 'text': text}

        # 행정코드 (8자리 숫자) — 괄호 안 우선, 없으면 일반 매칭
        code_match = re.search(r'[\(\[\{]\s*(\d[\d\s]{6,9})\s*[\)\]\}]', text)
        if code_match:
            code = re.sub(r'\s+', '', code_match.group(1))
            if len(code) == 8:
                result['admin_code'] = code
        if not result['admin_code']:
            matches = re.findall(r'\d{8}', text)
            if matches:
                result['admin_code'] = matches[0]

        if result['admin_code']:
            print(f"  [PDF 텍스트] 행정코드: {result['admin_code']}")

        # 축척 (1:XXXXX)
        scale = self._find_scale_in_text(text)
        if scale:
            result['scale'] = scale
            print(f"  [PDF 텍스트] 축척: 1:{scale:,}")

        return result

    # ------------------------------------------------------------------
    # OCR (Tesseract digits → kor → 퍼지매칭)
    # ------------------------------------------------------------------

    def _extract_admin_code(self, image: np.ndarray) -> str:
        """헤더에서 행정리 코드(8자리) 추출

        1차: Tesseract 숫자 whitelist (2x+Otsu, ~1.7초) → 정확 일치
        2차: Tesseract kor 원본 (~0.8초) → 정확 일치 or 퍼지매칭
        """
        import pytesseract
        import cv2

        # Windows Tesseract 경로 자동 탐색
        import shutil
        if shutil.which('tesseract') is None:
            import platform
            if platform.system() == 'Windows':
                for p in [
                    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
                    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
                    r'C:\OSGeo4W\bin\tesseract.exe',
                    r'C:\OSGeo4W64\bin\tesseract.exe',
                ]:
                    if os.path.isfile(p):
                        pytesseract.pytesseract.tesseract_cmd = p
                        break

        h, w = image.shape[:2]
        header = image[:int(h * 0.08), :]
        gray = cv2.cvtColor(header, cv2.COLOR_BGR2GRAY)
        valid_codes = self._valid_codes

        # 1차: 숫자 whitelist + 2x 업스케일 + Otsu 이진화
        up = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        blur = cv2.GaussianBlur(up, (3, 3), 0)
        _, binarized = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        text_digits = pytesseract.image_to_string(
            binarized, lang='eng',
            config='--psm 6 -c tessedit_char_whitelist=0123456789() ')
        candidates = re.findall(r'\d{8}', text_digits)
        print(f"  [Tesseract-digits] 후보: {candidates}")

        for c in candidates:
            if c in valid_codes:
                print(f"  행정코드 확정 [digits]: {c}")
                return c

        # 2차: Tesseract kor 원본 해상도
        text_kor = pytesseract.image_to_string(gray, lang='kor', config='--psm 6')
        candidates_kor = re.findall(r'\d{8}', text_kor)
        print(f"  [Tesseract-kor] 후보: {candidates_kor}")

        for c in candidates_kor:
            if c in valid_codes:
                print(f"  행정코드 확정 [kor]: {c}")
                return c

        # 퍼지매칭 (edit distance ≤ 2) — 두 단계 후보 합산
        all_candidates = list(dict.fromkeys(candidates + candidates_kor))
        fuzzy = []
        for c in all_candidates:
            for vc in valid_codes:
                d = sum(a != b for a, b in zip(c, vc))
                if d <= 2:
                    fuzzy.append(vc)
        if fuzzy:
            self._admin_code_candidates = list(set(fuzzy))
            print(f"  [퍼지] 후보 {len(self._admin_code_candidates)}개: {self._admin_code_candidates[:5]}")
            return self._admin_code_candidates[0]

        raise ValueError("행정리 코드를 찾을 수 없습니다 (Tesseract digits/kor 모두 실패)")

    @staticmethod
    def _find_scale_in_text(text: str) -> Optional[int]:
        """텍스트에서 '1:XXXX' 축척 패턴 검색"""
        # 공백 없이 연속된 숫자+콤마만 캡처 (뒤 숫자 오염 방지)
        for pattern in [r'1\s*:\s*([\d,\.]+)', r'1\s*/\s*([\d,\.]+)']:
            for m in re.finditer(pattern, text):
                scale_str = re.sub(r'[,\.]', '', m.group(1))
                try:
                    scale = int(scale_str)
                    if 1000 <= scale <= 100000:
                        return scale
                except ValueError:
                    continue
        return None

    # ------------------------------------------------------------------
    # SHP 경계 조회
    # ------------------------------------------------------------------

    def _get_boundary_from_shp(self, admin_code: str):
        """SHP에서 해당 코드의 경계 추출

        Returns:
            (main_geom, info_dict)
        """
        matched = self.gdf[self.gdf['adm_cd'].astype(str) == admin_code]
        if len(matched) == 0:
            raise ValueError(f"코드 '{admin_code}'에 해당하는 경계를 찾을 수 없습니다")

        row = matched.iloc[0]
        geom = row.geometry

        if isinstance(geom, MultiPolygon):
            geom = max(geom.geoms, key=lambda g: g.area)

        info = {
            'adm_nm': row.get('adm_nm', ''),
            'sigungu_nm': row.get('sigungu_nm', ''),
            'sido_nm': row.get('sido_nm', ''),
        }
        print(f"행정구역: {info['sido_nm']} {info['sigungu_nm']} {info['adm_nm']}")
        return geom, info

    # ------------------------------------------------------------------
    # 출력 파일명 결정
    # ------------------------------------------------------------------

    @staticmethod
    def _make_base_name(image_path: str, admin_code: str) -> str:
        """출력 파일명 결정 (행정코드 + 분할도 시트번호)

        Examples:
            33590140.jpg         → '33590140'
            23510310_4-1.jpg     → '23510310_4-1'
            23510310_georef.jpg  → '23510310'
        """
        img_base = os.path.splitext(os.path.basename(image_path))[0]
        sheet_match = re.search(r'_(\d+-\d+)$', img_base)
        if sheet_match:
            return f"{admin_code}_{sheet_match.group(1)}"
        return admin_code

    # ------------------------------------------------------------------
    # FFT 전역 탐색
    # ------------------------------------------------------------------

    def _fft_match_position(self, dist_map, main_geom, pixel_size, map_shape):
        """FFT 전역 탐색으로 초기 위치 추정

        Args:
            dist_map: build_skeleton_and_distmap()에서 반환된 distance transform
            main_geom: 대상 행정구역 geometry
            pixel_size: 픽셀 크기 (m/px)
            map_shape: (height, width) 지도 영역 크기

        Returns:
            (offset_x, offset_y) or None
        """
        from shapely.geometry import box

        map_h, map_w = map_shape

        # 1) proximity map 생성 (skeleton 근처일수록 값 높음)
        proximity = build_proximity_map(dist_map, sigma=15.0)

        # 2) FFT 탐색 AOI: 행정구역 bounds ± 이미지 절반 크기
        b = main_geom.bounds
        half_w = map_w * pixel_size / 2
        half_h = map_h * pixel_size / 2
        fft_aoi = (b[0] - half_w, b[1] - half_h,
                   b[2] + half_w, b[3] + half_h)

        # 3) AOI 내 SHP 경계 래스터화 (8x, 4x)
        clipped = self.gdf[self.gdf.geometry.intersects(box(*fft_aoi))]
        geom_list = clipped.geometry.tolist()

        raster_8x = rasterize_boundaries(
            geom_list, fft_aoi, pixel_size * 8).astype(np.float32)
        raster_4x = rasterize_boundaries(
            geom_list, fft_aoi, pixel_size * 4).astype(np.float32)

        print(f"  [FFT] AOI {len(clipped)}개 구역, "
              f"8x={raster_8x.shape}, 4x={raster_4x.shape}")

        # 4) FFT 교차상관
        result = fft_match_position(
            proximity, raster_8x, raster_4x, fft_aoi, pixel_size)

        if result:
            print(f"  [FFT] 위치: offset=({result[0]:.1f}, {result[1]:.1f})")
        else:
            print("  [FFT] 매칭 실패")

        return result

    # ------------------------------------------------------------------
    # 단일 이미지 좌표 부여
    # ------------------------------------------------------------------

    def georeference_image(self, image_path: str, output_dir: str = None) -> dict:
        """이미지에 좌표 부여 (FFT + Powell 방식)

        FFT 전역 탐색 → Powell 서브픽셀 리파인.
        DPI는 EXIF에서 자동 감지 (없으면 300 기본값).

        Args:
            image_path: 이미지 또는 PDF 경로
            output_dir: 출력 폴더 (None이면 원본 위치)

        Returns:
            결과 정보 dict
        """
        t0 = time.time()
        print(f"\n{'='*50}")
        print(f"처리: {os.path.basename(image_path)}")
        print(f"{'='*50}")

        # --- Step 0: PDF → JPG + TIFF ---
        tif_path = None
        pdf_info = None
        if image_path.lower().endswith('.pdf'):
            print("\n[Step 0] PDF → JPG + TIFF 변환 + 텍스트 추출")
            pdf_info = self._extract_info_from_pdf(image_path)
            pdf_result = convert_pdf_to_images(image_path, dpi=300, output_dir=output_dir)
            image_path = pdf_result['jpg']    # 이미지 처리는 JPG로
            tif_path = pdf_result['tif']      # QGIS용 TIFF 경로 보관

        # --- DPI 자동 감지 ---
        dpi = get_image_dpi(image_path)
        print(f"  DPI: {dpi} (자동감지)")

        # --- Step 1: 이미지 로드 + 지도 영역 추출 ---
        print("\n[Step 1] 이미지 로드")
        image = load_image(image_path)
        orig_h, orig_w = image.shape[:2]
        print(f"  원본: {orig_w} x {orig_h}")

        map_image, map_bbox = extract_map_region(image)
        map_h, map_w = map_image.shape[:2]
        print(f"  지도 영역: {map_w} x {map_h}, bbox={map_bbox}")

        # --- Step 2: 행정코드 추출 ---
        if pdf_info and pdf_info.get('admin_code'):
            print("\n[Step 2] PDF 텍스트에서 추출 (OCR 생략)")
            admin_code = pdf_info['admin_code']
            scale = pdf_info.get('scale')
        else:
            print("\n[Step 2] OCR (행정코드)")
            admin_code = self._extract_admin_code(image)
            scale = None
        base_name = self._make_base_name(image_path, admin_code)
        print(f"  admin_code={admin_code}, base_name={base_name}")

        # --- Step 3: 주황 마스크 → 스켈레톤 + DT ---
        print("\n[Step 3] 주황 마스크 → 스켈레톤 + Distance Transform")
        orange_mask = extract_orange_mask(map_image)
        orange_count = np.sum(orange_mask > 0)
        print(f"  주황 픽셀: {orange_count:,}개")
        if orange_count < 500:
            raise ValueError("주황색 경계선이 부족합니다.")
        skeleton, skel_points, dist_map = build_skeleton_and_distmap(orange_mask)

        # --- Step 4: SHP 경계 + 기하 기반 초기 추정 ---
        print("\n[Step 4] SHP 경계 + 기하 기반 초기 추정")

        # 퍼지 후보가 있으면 각 후보에 대해 기하 매칭 → cost 최소 선택
        code_candidates = self._admin_code_candidates
        if code_candidates and len(code_candidates) > 1:
            print(f"  퍼지 후보 {len(code_candidates)}개 기하 검증 중...")
            best_code, best_cost = admin_code, 999.0
            for cand_code in code_candidates:
                try:
                    cand_geom, _ = self._get_boundary_from_shp(cand_code)
                    cb = cand_geom.bounds
                    cw, ch = cb[2] - cb[0], cb[3] - cb[1]
                    sw = skel_points[:, 0].max() - skel_points[:, 0].min()
                    sh = skel_points[:, 1].max() - skel_points[:, 1].min()
                    cps = max(cw / max(sw, 1), ch / max(sh, 1))
                    # pixel_size 범위 체크 (0.1~20 m/px)
                    if not (0.1 <= cps <= 20.0):
                        print(f"    {cand_code}: skip (ps={cps:.2f} 범위 초과)")
                        continue
                    cox = cand_geom.centroid.x - skel_points[:, 0].mean() * cps
                    coy = cand_geom.centroid.y - skel_points[:, 1].mean() * (-cps)
                    caoi = compute_aoi(cox, coy, cps, (map_h, map_w))
                    cbnd = clip_shp_to_aoi(self.gdf, caoi, buffer_ratio=0.5)
                    cpts = sample_points_from_boundaries(cbnd, num_points=2000)
                    if len(cpts) < 50:
                        continue
                    rps, _, _, ccost = refine_position(
                        cpts, dist_map, cps, cox, coy, (map_h, map_w), fix_scale=False)
                    # 최적화 후 pixel_size 범위 재검증
                    if not (0.1 <= rps <= 20.0):
                        print(f"    {cand_code}: skip (최적화 ps={rps:.2f} 범위 초과)")
                        continue
                    print(f"    {cand_code}: cost={ccost:.2f}px, ps={rps:.4f}")
                    if ccost < best_cost:
                        best_cost, best_code = ccost, cand_code
                except Exception:
                    continue
            if best_code != admin_code:
                admin_code = best_code
                base_name = self._make_base_name(image_path, admin_code)
                print(f"  → 행정코드 확정: {admin_code} (cost={best_cost:.2f}px)")
            self._admin_code_candidates = None

        main_geom, info = self._get_boundary_from_shp(admin_code)

        # 기하 기반 pixel_size (항상 사용, DPI 오류에 강건)
        geo_bounds = main_geom.bounds
        shp_w = geo_bounds[2] - geo_bounds[0]
        shp_h = geo_bounds[3] - geo_bounds[1]
        sk_w = skel_points[:, 0].max() - skel_points[:, 0].min()
        sk_h = skel_points[:, 1].max() - skel_points[:, 1].min()
        init_ps = max(shp_w / max(sk_w, 1), shp_h / max(sk_h, 1))

        # centroid 기반 offset
        skel_cx = skel_points[:, 0].mean()
        skel_cy = skel_points[:, 1].mean()
        init_ox = main_geom.centroid.x - skel_cx * init_ps
        init_oy = main_geom.centroid.y - skel_cy * (-init_ps)
        print(f"  pixel_size={init_ps:.4f} m/px (기하 기반)")

        # --- Step 5: 1차 Powell 리파인 (기하 기반) ---
        print("\n[Step 5] 1차 Powell 리파인 (기하 기반)")
        aoi1 = compute_aoi(init_ox, init_oy, init_ps, (map_h, map_w))
        boundaries = clip_shp_to_aoi(self.gdf, aoi1, buffer_ratio=0.5)
        shp_pts1 = sample_points_from_boundaries(boundaries, num_points=3000)

        if len(shp_pts1) < 50:
            raise ValueError(f"경계점 부족 ({len(shp_pts1)}개)")

        opt_ps, opt_ox, opt_oy, opt_cost = refine_position(
            shp_pts1, dist_map, init_ps, init_ox, init_oy, (map_h, map_w),
            fix_scale=False,
        )

        # 기하 기반 실패 시 FFT 폴백
        if opt_cost > 10:
            print(f"\n[Step 5-FFT] 기하 기반 cost={opt_cost:.1f}px > 10, FFT 폴백")
            fft_result = self._fft_match_position(
                dist_map, main_geom, init_ps, (map_h, map_w))
            if fft_result:
                fft_ox, fft_oy = fft_result
                aoi_fft = compute_aoi(fft_ox, fft_oy, init_ps, (map_h, map_w))
                bnd_fft = clip_shp_to_aoi(self.gdf, aoi_fft, buffer_ratio=0.5)
                pts_fft = sample_points_from_boundaries(bnd_fft, num_points=3000)
                if len(pts_fft) >= 50:
                    ps_f, ox_f, oy_f, cost_f = refine_position(
                        pts_fft, dist_map, init_ps, fft_ox, fft_oy, (map_h, map_w),
                        fix_scale=False,
                    )
                    if cost_f < opt_cost:
                        opt_ps, opt_ox, opt_oy, opt_cost = ps_f, ox_f, oy_f, cost_f
                        print(f"  [FFT] 채택: cost={cost_f:.2f}px")

        if opt_cost > 10:
            raise ValueError(f"정합 실패: cost={opt_cost:.1f}px > 10px")

        # --- Step 5b: 재클리핑 + 2차 리파인 (정밀 AOI) ---
        print("\n[Step 5b] 재클리핑 + 2차 Powell 리파인")
        aoi2 = compute_aoi(opt_ox, opt_oy, opt_ps, (map_h, map_w))
        boundaries = clip_shp_to_aoi(self.gdf, aoi2, buffer_ratio=0.05)
        shp_pts2 = sample_points_from_boundaries(boundaries, num_points=5000)

        if len(shp_pts2) >= 50:
            ps2, ox2, oy2, cost2 = refine_position(
                shp_pts2, dist_map, opt_ps, opt_ox, opt_oy, (map_h, map_w),
            )
            if cost2 <= opt_cost:
                print(f"  -> 2차 리파인 채택: cost {opt_cost:.3f} → {cost2:.3f}px")
                opt_ps, opt_ox, opt_oy, opt_cost = ps2, ox2, oy2, cost2
            else:
                print(f"  -> 2차 리파인 미채택: {cost2:.3f}px > {opt_cost:.3f}px")

        # --- Step 7: GCP 생성 ---
        print("\n[Step 7] GCP 생성")
        gcps = create_gcps(
            boundaries, skel_points, opt_ps, opt_ox, opt_oy, map_bbox, (map_h, map_w)
        )
        if len(gcps) < 4:
            print("  GCP 부족, 임계값 완화...")
            gcps = create_gcps(
                boundaries, skel_points, opt_ps, opt_ox, opt_oy, map_bbox, (map_h, map_w),
                dist_threshold_px=15.0,
            )
        if len(gcps) < 4:
            raise ValueError(f"GCP가 {len(gcps)}개로 부족합니다 (최소 4개 필요).")

        # --- Step 8: JGW 출력 ---
        print("\n[Step 8] JGW 출력")
        map_x, map_y, _, _ = map_bbox
        jgw_params = JGWParams(
            pixel_size_x=opt_ps,
            rotation_x=0.0,
            rotation_y=0.0,
            pixel_size_y=-opt_ps,
            top_left_x=opt_ox - map_x * opt_ps,
            top_left_y=opt_oy - map_y * (-opt_ps),
        )

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            jgw_path = os.path.join(output_dir, f"{base_name}.jgw")
        else:
            jgw_path = os.path.join(os.path.dirname(image_path), f"{base_name}.jgw")

        write_jgw(jgw_path, jgw_params)
        print(f"  JGW 저장: {jgw_path}")

        # TIFF용 TFW 생성 (JGW와 동일 포맷)
        if tif_path:
            tfw_path = os.path.splitext(tif_path)[0] + '.tfw'
            write_jgw(tfw_path, jgw_params)
            print(f"  TFW 저장: {tfw_path}")

        # 이미지명과 출력명이 다르면 QGIS 자동매칭용 JGW 추가 생성
        img_base = os.path.splitext(os.path.basename(image_path))[0]
        if img_base != base_name:
            img_jgw_dir = output_dir or os.path.dirname(image_path)
            img_jgw_path = os.path.join(img_jgw_dir, f"{img_base}.jgw")
            write_jgw(img_jgw_path, jgw_params)
            print(f"  JGW 저장 (QGIS용): {img_jgw_path}")

        # --- Step 9: VRT 출력 ---
        gcp_vrt = None
        tps_vrt = None
        if output_dir:
            print("\n[Step 9] VRT 출력")
            gcp_vrt, tps_vrt = write_vrt_output(image_path, gcps, output_dir, base_name)

        elapsed = time.time() - t0
        admin_name = f"{info['sigungu_nm']} {info['adm_nm']}"
        print(f"\n{'='*50}")
        print(f"[완료] {elapsed:.1f}초, GCP {len(gcps)}개, cost={opt_cost:.2f}px")
        print(f"  {admin_name} ({admin_code}) → {base_name}")
        print(f"{'='*50}\n")

        return {
            'image_path': image_path,
            'tif_path': tif_path,
            'admin_code': admin_code,
            'admin_name': admin_name,
            'base_name': base_name,
            'jgw_path': jgw_path,
            'pixel_size': opt_ps,
            'top_left': (jgw_params.top_left_x, jgw_params.top_left_y),
            'scale': scale,
            'dpi': dpi,
            'cost': opt_cost,
            'gcp_count': len(gcps),
            'elapsed': elapsed,
            'gcp_vrt': gcp_vrt,
            'tps_vrt': tps_vrt,
        }

    # ------------------------------------------------------------------
    # 폴더 일괄 처리
    # ------------------------------------------------------------------

    def georeference_folder(self, folder_path: str, output_dir: str = None,
                            progress_callback=None) -> dict:
        """폴더 내 모든 이미지에 좌표 부여 (일괄 처리)

        Args:
            folder_path: 이미지 폴더 경로
            output_dir: 출력 폴더 (None이면 원본 위치)
            progress_callback: (현재번호, 전체수, 파일명) 콜백

        Returns:
            {'results': [...], 'errors': [...], 'total': N}
        """
        files = []
        for ext in IMAGE_EXTENSIONS:
            files.extend(glob.glob(os.path.join(folder_path, ext)))
            files.extend(glob.glob(os.path.join(folder_path, ext.upper())))
        files = sorted(set(files))

        # PDF에서 변환된 JPG/TIF와 원본 PDF 중복 제거: PDF 우선
        pdf_bases = set()
        for f in files:
            if f.lower().endswith('.pdf'):
                pdf_bases.add(os.path.splitext(f)[0].lower())
        files = [f for f in files
                 if f.lower().endswith('.pdf')
                 or os.path.splitext(f)[0].lower() not in pdf_bases]

        if not files:
            raise ValueError(f"이미지 파일이 없습니다: {folder_path}")

        total = len(files)
        print(f"\n{'#'*50}")
        print(f"# 일괄 처리: {total}개 파일")
        print(f"# 폴더: {folder_path}")
        print(f"{'#'*50}")

        results = []
        errors = []

        for i, f in enumerate(files, 1):
            filename = os.path.basename(f)
            print(f"\n[{i}/{total}] {filename}")

            if progress_callback:
                progress_callback(i, total, filename)

            try:
                result = self.georeference_image(f, output_dir)
                results.append(result)
            except Exception as e:
                print(f"  오류: {e}")
                errors.append({'file': f, 'filename': filename, 'error': str(e)})

        # 요약
        print(f"\n{'#'*50}")
        print(f"# 일괄 처리 완료: {len(results)}/{total} 성공")
        if errors:
            print(f"# 실패 {len(errors)}개:")
            for err in errors:
                print(f"#   {err['filename']}: {err['error']}")
        print(f"{'#'*50}\n")

        return {'results': results, 'errors': errors, 'total': total}
