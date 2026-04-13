"""
분할도 처리 모듈 (N-분할 지원)
- FFT Proximity 매칭 → SHP 경계와 직접 정합
- 개별 크롭 지도영역 + JGW/TPS VRT 생성 + 병합 이미지 생성
"""

import cv2
import json
import numpy as np
import os
import re
from math import ceil, sqrt
from typing import Dict, List, Optional, Tuple

from .common import (
    JGWParams, PRJ_5179, DEFAULT_DPI, load_image, parse_jgw, write_jgw,
    extract_map_region, extract_orange_mask, build_skeleton_and_distmap,
    build_proximity_map, rasterize_boundaries, fft_match_position,
    compute_aoi, clip_shp_to_aoi, sample_points_from_boundaries,
    refine_position, detect_frame_thickness, get_image_dpi,
    build_centerline_costmap, refine_position_subpixel,
    create_gcps, write_vrt_output,
)


class SubdivisionProcessor:
    """분할도 처리기 — FFT Proximity 매칭 + TPS 워핑"""

    def __init__(self, main_image_path: str, main_jgw_path: str,
                 shp_path: str = None):
        self.main_jgw = parse_jgw(main_jgw_path)

        # 메인 이미지: 크기만 필요 (프레임 두께 감지용으로 1회 로드 후 해제)
        main_image = load_image(main_image_path)
        self.main_h, self.main_w = main_image.shape[:2]
        self._frame_thickness = detect_frame_thickness(main_image)
        del main_image

        # 메인 이미지 파일명에서 행정코드 추출
        main_base = os.path.splitext(os.path.basename(main_image_path))[0]
        m = re.match(r'^(\d{8})', main_base)
        self.main_admin_code = m.group(1) if m else None

        # 메인 이미지 DPI 저장 (스케일 계산용)
        self._main_dpi = get_image_dpi(main_image_path)

        # SHP 데이터 (FFT 매칭용)
        import geopandas as gpd
        self.gdf = gpd.read_file(shp_path, encoding='cp949')
        print(f"SHP 로드: {len(self.gdf)}개 행정구역")

        # FFT 래스터 캐시
        self._fft_ready = False

        print(f"메인 이미지: {self.main_w}x{self.main_h}")

    # ------------------------------------------------------------------
    # FFT 래스터 사전 생성
    # ------------------------------------------------------------------

    def _prepare_fft(self, admin_code: str, est_ps: float):
        """SHP 래스터 사전 생성 (전 시트 공유)"""
        if self._fft_ready:
            return

        from shapely.geometry import box, MultiPolygon

        matched = self.gdf[self.gdf['adm_cd'].astype(str) == admin_code]
        if len(matched) == 0:
            raise ValueError(f"admin_code '{admin_code}'를 SHP에서 찾을 수 없음")

        geom = matched.iloc[0].geometry
        if isinstance(geom, MultiPolygon):
            geom = max(geom.geoms, key=lambda g: g.area)

        b = geom.bounds
        img_half_w = self.main_w * self.main_jgw.pixel_size_x / 2
        img_half_h = self.main_h * abs(self.main_jgw.pixel_size_y) / 2
        self._fft_aoi = (
            b[0] - img_half_w, b[1] - img_half_h,
            b[2] + img_half_w, b[3] + img_half_h,
        )

        clipped = self.gdf[self.gdf.geometry.intersects(box(*self._fft_aoi))]
        geom_list = clipped.geometry.tolist()

        self._fft_raster_8x = rasterize_boundaries(
            geom_list, self._fft_aoi, est_ps * 8
        ).astype(np.float32)
        self._fft_raster_4x = rasterize_boundaries(
            geom_list, self._fft_aoi, est_ps * 4
        ).astype(np.float32)
        self._fft_est_ps = est_ps
        self._fft_ready = True

        print(
            f"  FFT 래스터: AOI {len(clipped)}개 구역, "
            f"8x={self._fft_raster_8x.shape}, 4x={self._fft_raster_4x.shape}"
        )

    # ------------------------------------------------------------------
    # FFT Proximity 매칭
    # ------------------------------------------------------------------

    def _fft_match(self, sub_img: np.ndarray
                   ) -> Optional[Tuple[JGWParams, float, list, np.ndarray, Tuple]]:
        """FFT proximity 매칭 + Powell 리파인먼트 + GCP 생성

        Returns:
            (jgw_params, cost, gcps, map_image, map_bbox) 또는 None
            jgw_params는 map_image(크롭 지도영역) 기준
        """
        est_ps = self._fft_est_ps

        # 지도 영역 추출
        sub_h, sub_w = sub_img.shape[:2]
        map_image, map_bbox = extract_map_region(sub_img)
        map_h, map_w = map_image.shape[:2]
        print(f"    원본: {sub_w}x{sub_h} → 지도영역: {map_w}x{map_h} "
              f"bbox={map_bbox}")

        # 주황 마스크 → 스켈레톤 → proximity
        orange_mask = extract_orange_mask(map_image)
        n_orange = np.sum(orange_mask > 0)
        print(f"    주황 픽셀: {n_orange:,}")
        if n_orange < 500:
            print("    FFT: 주황 픽셀 부족")
            return None

        skeleton, skel_points, dist_map = build_skeleton_and_distmap(orange_mask)
        proximity = build_proximity_map(dist_map, sigma=15.0)

        # --- 1단계: FFT 위치 매칭 ---
        print(f"    FFT 래스터: 8x={self._fft_raster_8x.shape}, "
              f"prox={proximity.shape}, est_ps={est_ps:.6f}")
        result = fft_match_position(
            proximity, self._fft_raster_8x, self._fft_raster_4x,
            self._fft_aoi, est_ps,
        )
        if result is None:
            print("    FFT: fft_match_position 리턴 None")
            return None

        fft_ox, fft_oy = result
        print(f"    FFT 위치: ox={fft_ox:.2f}, oy={fft_oy:.2f}")

        # SHP 경계점 샘플링 (FFT 초기 위치 기준)
        aoi2 = compute_aoi(fft_ox, fft_oy, est_ps, (map_h, map_w))
        bd2 = clip_shp_to_aoi(self.gdf, aoi2, buffer_ratio=0.05)
        shp_pts = sample_points_from_boundaries(bd2, num_points=3000)

        if len(shp_pts) < 50:
            print("    FFT: SHP 경계점 부족")
            return None

        # FFT cost 확인 (바이리니어 보간 기준)
        from scipy.ndimage import map_coordinates
        margin = 5
        fx = (shp_pts[:, 0] - fft_ox) / est_ps
        fy = (fft_oy - shp_pts[:, 1]) / est_ps
        valid = ((fx >= margin) & (fx < map_w - margin) &
                 (fy >= margin) & (fy < map_h - margin))
        fft_cost = (
            float(np.mean(map_coordinates(
                dist_map, [fy[valid], fx[valid]], order=1, mode='constant', cval=30.0
            )))
            if np.sum(valid) > 10 else 999.0
        )

        if fft_cost > 500.0:
            print(f"    FFT: cost {fft_cost:.1f}px 과다")
            return None

        # --- 2단계: Powell 리파인먼트 (축척+위치) ---
        refined_ps, refined_ox, refined_oy, refined_cost = refine_position(
            shp_pts, dist_map,
            init_pixel_size=est_ps,
            init_offset_x=fft_ox,
            init_offset_y=fft_oy,
            image_shape=(map_h, map_w),
        )

        ps_final, ox_final, oy_final = refined_ps, refined_ox, refined_oy
        cost_final = refined_cost
        print(f"    FFT→Refine: {fft_cost:.3f}→{refined_cost:.3f}px "
              f"(Δps={refined_ps - est_ps:+.2e})")

        if refined_cost > 10.0:
            print(f"    리파인 후에도 cost {refined_cost:.1f}px 과다 → 실패")
            return None

        # --- 3단계: 정밀 AOI 재클리핑 + 2차 리파인 ---
        aoi3 = compute_aoi(ox_final, oy_final, ps_final, (map_h, map_w))
        bd3 = clip_shp_to_aoi(self.gdf, aoi3, buffer_ratio=0.05)
        shp_pts3 = sample_points_from_boundaries(bd3, num_points=5000)

        if len(shp_pts3) >= 50:
            ps2, ox2, oy2, cost2 = refine_position(
                shp_pts3, dist_map,
                init_pixel_size=ps_final,
                init_offset_x=ox_final,
                init_offset_y=oy_final,
                image_shape=(map_h, map_w),
            )
            if cost2 <= cost_final:
                print(f"    2차 리파인: {cost_final:.3f}→{cost2:.3f}px")
                ps_final, ox_final, oy_final, cost_final = ps2, ox2, oy2, cost2

        # --- 4단계: 서브픽셀 리파인 (EDT centerline + cubic skeleton) ---
        cl_costmap = build_centerline_costmap(orange_mask)
        shp_pts4 = shp_pts3 if (len(shp_pts3) >= 50) else shp_pts
        ps3, ox3, oy3, cost3 = refine_position_subpixel(
            shp_pts4, dist_map, cl_costmap,
            init_pixel_size=ps_final,
            init_offset_x=ox_final,
            init_offset_y=oy_final,
            image_shape=(map_h, map_w),
        )
        if cost3 <= cost_final:
            print(f"    서브픽셀 리파인: {cost_final:.4f}→{cost3:.4f}px")
            ps_final, ox_final, oy_final, cost_final = ps3, ox3, oy3, cost3

        # --- 5단계: GCP 생성 (TPS 워핑용) ---
        gcps = create_gcps(
            boundaries=bd3,
            skel_points=skel_points,
            pixel_size=ps_final,
            offset_x=ox_final,
            offset_y=oy_final,
            map_bbox=(0, 0, map_w, map_h),
            image_shape=(map_h, map_w),
            dist_threshold_px=5.0,
            min_spacing_px=80.0,
        )

        # JGW (크롭된 지도영역 기준)
        jgw = JGWParams(
            pixel_size_x=ps_final,
            rotation_x=0.0,
            rotation_y=0.0,
            pixel_size_y=-ps_final,
            top_left_x=ox_final,
            top_left_y=oy_final,
        )

        return jgw, cost_final, gcps, map_image, map_bbox

    # ------------------------------------------------------------------
    # 폴더 스캔
    # ------------------------------------------------------------------

    def scan_folder(self, folder_path: str) -> dict:
        """분할도 폴더 스캔 — N-분할 자동 감지

        파일명 규칙: {admin_code}_{N}-{idx}.{ext}
        예: 23510310_4-1.jpg, 23510310_4-2.jpg, ...

        Returns:
            {admin_code, total_sheets, n_rows, n_cols, sheets: {idx: path}, missing: [...]}
        """
        pattern = re.compile(
            r'^(.+)_(\d+)-(\d+)\.(jpg|jpeg|tif|tiff)$', re.IGNORECASE
        )

        sheets = {}
        admin_code = None
        total_sheets = 0

        for fname in sorted(os.listdir(folder_path)):
            m = pattern.match(fname)
            if not m:
                continue

            code, n_str, idx_str, _ext = m.groups()
            n = int(n_str)
            idx = int(idx_str)

            # 메인 이미지 행정코드와 일치하는 파일만 포함
            if self.main_admin_code and code != self.main_admin_code:
                continue

            if admin_code is None:
                admin_code = code
                total_sheets = n
            elif code != admin_code:
                continue

            sheets[idx] = os.path.join(folder_path, fname)

        if not sheets:
            raise ValueError(f"분할도 파일을 찾을 수 없습니다: {folder_path}"
                             + (f" (행정코드: {self.main_admin_code})"
                                if self.main_admin_code else ""))

        missing = [i for i in range(1, total_sheets + 1) if i not in sheets]

        # grid는 _compute_expected_scale에서 자동 탐색 (여기선 임시값)
        n_cols = ceil(sqrt(total_sheets))
        n_rows = ceil(total_sheets / n_cols)

        return {
            'admin_code': admin_code,
            'total_sheets': total_sheets,
            'n_rows': n_rows,
            'n_cols': n_cols,
            'sheets': sheets,
            'missing': missing,
        }

    # ------------------------------------------------------------------
    # 스케일 계산
    # ------------------------------------------------------------------

    def _compute_expected_scale(self, sample_sub_path: str,
                               total_sheets: int,
                               n_rows: int = 1, n_cols: int = 1
                               ) -> Tuple[float, int, int]:
        """분할도 스케일 계산 (이미지 크기 + 분할 그리드 자동 탐색)

        scale_x ≈ scale_y가 되는 (n_rows, n_cols) 조합을 자동 탐색.
        비정형 배치(예: 6매가 3x3 희소 그리드)도 지원.

        Returns:
            (scale, best_n_rows, best_n_cols)
        """
        # 메인 지도 영역 크기 (프레임 제외)
        ft_h, ft_v = self._frame_thickness
        main_map_w = self.main_w - 2 * ft_v
        main_map_h = self.main_h - 2 * ft_h

        # 분할도 지도 영역 크기
        sample_img = load_image(sample_sub_path)
        sample_map, _ = extract_map_region(sample_img)
        sub_map_h, sub_map_w = sample_map.shape[:2]
        del sample_img, sample_map

        # grid 자동 탐색: scale_x ≈ scale_y (비율 차이 최소)
        best_grid = (n_rows, n_cols)
        best_diff = float('inf')
        best_scale = 0.0

        for nr in range(1, total_sheets + 1):
            for nc in range(1, total_sheets + 1):
                if nr * nc < total_sheets:
                    continue
                if nr * nc > total_sheets * 2:
                    continue
                panel_w = main_map_w / nc
                panel_h = main_map_h / nr
                sx = sub_map_w / panel_w
                sy = sub_map_h / panel_h
                diff = abs(sx - sy)
                if diff < best_diff:
                    best_diff = diff
                    best_grid = (nr, nc)
                    best_scale = (sx + sy) / 2

        n_rows, n_cols = best_grid
        scale_x = sub_map_w / (main_map_w / n_cols)
        scale_y = sub_map_h / (main_map_h / n_rows)

        print(f"  스케일 추정: main_map={main_map_w}x{main_map_h}, "
              f"sub_map={sub_map_w}x{sub_map_h}, "
              f"grid={n_rows}x{n_cols} → scale={best_scale:.3f} "
              f"(sx={scale_x:.3f}, sy={scale_y:.3f})")
        return best_scale, n_rows, n_cols

    # ------------------------------------------------------------------
    # N-분할 병합
    # ------------------------------------------------------------------

    def _merge_sheets(self, sheet_results: Dict[int, dict],
                      output_path: str,
                      crop_margin_x: int = 3,
                      crop_margin_y: int = 4) -> JGWParams:
        """메인 이미지 좌표계 기준으로 N-분할 병합

        각 타일의 개별 JGW(정밀 정합 결과)를 기준으로
        실제 int() 배치 위치에서 병합 JGW를 역산하여
        개별 JGW와의 좌표 불일치를 최소화.

        sheet_results: {num: {image, jgw, main_cx, main_cy, scale}}
        """
        scales = [r['scale'] for r in sheet_results.values()]
        avg_scale = np.mean(scales)
        merged_ps_x = self.main_jgw.pixel_size_x / avg_scale
        merged_ps_y = self.main_jgw.pixel_size_y / avg_scale
        print(f"\n평균 스케일: {avg_scale:.3f}")
        print(f"  crop_margin: x={crop_margin_x}, y={crop_margin_y}")

        tile_bounds = {}
        for num, r in sheet_results.items():
            h, w = r['image'].shape[:2]
            main_cx, main_cy = r['main_cx'], r['main_cy']

            half_w_main = (w / 2 + crop_margin_x) / avg_scale
            half_h_main = (h / 2 + crop_margin_y) / avg_scale

            tile_bounds[num] = (
                main_cx - half_w_main,
                main_cy - half_h_main,
                main_cx + half_w_main,
                main_cy + half_h_main,
            )

        all_min_x = min(b[0] for b in tile_bounds.values())
        all_min_y = min(b[1] for b in tile_bounds.values())
        all_max_x = max(b[2] for b in tile_bounds.values())
        all_max_y = max(b[3] for b in tile_bounds.values())

        canvas_w = int((all_max_x - all_min_x) * avg_scale) + 1
        canvas_h = int((all_max_y - all_min_y) * avg_scale) + 1
        print(f"캔버스 크기: {canvas_w} x {canvas_h}")

        canvas = np.ones((canvas_h, canvas_w, 3), dtype=np.uint8) * 255

        # 타일 배치 + JGW 역산용 데이터 수집
        placements = []
        for num, r in sorted(sheet_results.items()):
            tile = r['image']
            h, w = tile.shape[:2]
            main_cx, main_cy = r['main_cx'], r['main_cy']

            canvas_cx = (main_cx - all_min_x) * avg_scale
            canvas_cy = (main_cy - all_min_y) * avg_scale

            x = int(canvas_cx - w / 2)
            y = int(canvas_cy - h / 2)

            y1, y2 = max(0, y), min(canvas_h, y + h)
            x1, x2 = max(0, x), min(canvas_w, x + w)
            ty1, ty2 = max(0, -y), h - max(0, y + h - canvas_h)
            tx1, tx2 = max(0, -x), w - max(0, x + w - canvas_w)

            if y2 > y1 and x2 > x1:
                canvas[y1:y2, x1:x2] = tile[ty1:ty2, tx1:tx2]

            placements.append((num, x, y, r['jgw']))
            print(f"  시트 {num}: ({x}, {y}) {w}x{h}")

        # 병합 JGW 역산: 각 타일의 (개별JGW world좌표, 캔버스 배치좌표)에서
        # merged_top_left = tile_jgw.top_left - (canvas_x * merged_ps_x, canvas_y * merged_ps_y)
        tl_xs = []
        tl_ys = []
        for num, cx, cy, tile_jgw in placements:
            tl_xs.append(tile_jgw.top_left_x - cx * merged_ps_x)
            tl_ys.append(tile_jgw.top_left_y - cy * merged_ps_y)

        merged_top_left_x = float(np.median(tl_xs))
        merged_top_left_y = float(np.median(tl_ys))

        _, ext = os.path.splitext(output_path)
        result, encoded = cv2.imencode(ext if ext else '.jpg', canvas)
        if result:
            encoded.tofile(output_path)
        print(f"저장: {output_path}")

        jgw_path = os.path.splitext(output_path)[0] + '.jgw'
        merged_jgw = JGWParams(
            pixel_size_x=merged_ps_x,
            rotation_x=0.0,
            rotation_y=0.0,
            pixel_size_y=merged_ps_y,
            top_left_x=merged_top_left_x,
            top_left_y=merged_top_left_y,
        )
        write_jgw(jgw_path, merged_jgw)

        return merged_jgw

    # ------------------------------------------------------------------
    # 메인 엔트리포인트
    # ------------------------------------------------------------------

    def process(self, folder_path: str, output_dir: str,
                progress_callback=None,
                merged_output_path: str = None) -> dict:
        """분할도 처리

        Args:
            folder_path: 분할도 이미지 폴더
            output_dir: 결과 저장 폴더
            progress_callback: fn(current, total, filename)
            merged_output_path: 병합 출력 경로 (None이면 자동 생성)

        Returns:
            {output, admin_code, grid, scale, n_success, n_total,
             method, individual_jgws, tps_vrts, errors}
        """
        os.makedirs(output_dir, exist_ok=True)

        # 1. 폴더 스캔
        scan = self.scan_folder(folder_path)
        admin_code = scan['admin_code']
        total = scan['total_sheets']
        n_rows, n_cols = scan['n_rows'], scan['n_cols']

        print(f"\n분할도: {admin_code}, {total}매 ({n_rows}x{n_cols})")
        if scan['missing']:
            print(f"누락: {scan['missing']}")

        # 2. 스케일 계산 (grid 자동 탐색)
        sample_path = next(iter(scan['sheets'].values()))
        expected_scale, n_rows, n_cols = self._compute_expected_scale(
            sample_path, total_sheets=total, n_rows=n_rows, n_cols=n_cols)
        est_ps = self.main_jgw.pixel_size_x / expected_scale
        print(f"예상 스케일: {expected_scale:.3f}, pixel_size: {est_ps:.6f}")

        # 2b. 프레임 두께 → crop_margin
        ht, vt = self._frame_thickness
        crop_margin_y = max(1, round(ht * expected_scale))
        crop_margin_x = max(1, round(vt * expected_scale))
        print(f"  crop_margin: x={crop_margin_x}, y={crop_margin_y} "
              f"(프레임 ht={ht}, vt={vt}, scale={expected_scale:.3f})")

        # 3. FFT 래스터 사전 생성
        self._prepare_fft(admin_code, est_ps)

        # 4. 각 시트 처리
        sheet_results: Dict[int, dict] = {}
        individual_jgws: List[str] = []
        tps_vrts: List[str] = []
        errors: List[dict] = []

        failed_sheets: List[Tuple[int, str]] = []  # (num, path) 1차 실패

        for num, path in sorted(scan['sheets'].items()):
            sheet_name = os.path.basename(path)
            print(f"\n--- 시트 {num}: {sheet_name} ---")

            if progress_callback:
                progress_callback(num, total, sheet_name)

            try:
                sub_img = load_image(path)

                fft_result = self._fft_match(sub_img)
                del sub_img

                if fft_result is None:
                    failed_sheets.append((num, path))
                    continue

                jgw, cost, gcps, map_image, map_bbox = fft_result

                # 크롭 이미지 저장 (_crop 접미사로 원본 보호)
                name_base = os.path.splitext(sheet_name)[0] + '_crop'
                out_img_path = os.path.join(output_dir, f"{name_base}.jpg")
                _, encoded = cv2.imencode('.jpg', map_image,
                                          [cv2.IMWRITE_JPEG_QUALITY, 95])
                encoded.tofile(out_img_path)

                # JGW 저장
                jgw_path = os.path.join(output_dir, f"{name_base}.jgw")
                write_jgw(jgw_path, jgw)

                # meta.json
                sheet_ps = abs(jgw.pixel_size_x)
                sheet_dpi = get_image_dpi(path)
                sheet_scale = round(sheet_ps * sheet_dpi / 0.0254)

                meta_path = os.path.join(output_dir, f"{name_base}.meta.json")
                meta = {
                    'admin_code': admin_code,
                    'scale': int(sheet_scale),
                    'pixel_size': float(sheet_ps),
                    'cost': float(cost),
                    'gcp_count': len(gcps) if gcps else None,
                    'method': 'fft',
                }
                with open(meta_path, 'w', encoding='utf-8') as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)

                # PRJ
                prj_path = os.path.join(output_dir, f"{name_base}.prj")
                with open(prj_path, 'w') as f:
                    f.write(PRJ_5179)

                # TPS VRT
                if gcps:
                    try:
                        _, tps_path = write_vrt_output(
                            out_img_path, gcps, output_dir, name_base)
                        tps_vrts.append(tps_path)
                    except Exception as ve:
                        print(f"  VRT 생성 실패 (무시): {ve}")

                individual_jgws.append(jgw_path)
                print(f"  JGW 저장: {jgw_path}")

                # 병합용 결과
                map_h, map_w = map_image.shape[:2]
                world_cx = jgw.top_left_x + (map_w / 2.0) * jgw.pixel_size_x
                world_cy = jgw.top_left_y + (map_h / 2.0) * jgw.pixel_size_y

                main_cx = (world_cx - self.main_jgw.top_left_x) / self.main_jgw.pixel_size_x
                main_cy = (world_cy - self.main_jgw.top_left_y) / self.main_jgw.pixel_size_y

                actual_scale = self.main_jgw.pixel_size_x / sheet_ps

                sheet_results[num] = {
                    'image': map_image,
                    'jgw': jgw,
                    'jgw_path': jgw_path,
                    'main_cx': main_cx,
                    'main_cy': main_cy,
                    'scale': actual_scale,
                }

            except Exception as e:
                errors.append({
                    'sheet': num, 'file': sheet_name, 'error': str(e),
                })
                print(f"  오류: {e}")

        # 4b. 실패 시트 → 성공 시트 위치 패턴으로 빈 위치 추론 → refine 재시도
        if failed_sheets and len(sheet_results) >= 2:
            print(f"\n=== 실패 시트 재시도 ({len(failed_sheets)}개) ===")

            # 성공 시트들의 좌표 + median pixel_size
            all_ps = [abs(r['jgw'].pixel_size_x) for r in sheet_results.values()]
            med_ps = float(np.median(all_ps))
            sample_r = next(iter(sheet_results.values()))
            sh, sw = sample_r['image'].shape[:2]
            tile_w = sw * med_ps
            tile_h = sh * med_ps

            # 성공 시트 좌표를 열/행 클러스터링 (타일 크기 50% 임계)
            positions = [(sr['jgw'].top_left_x, sr['jgw'].top_left_y)
                         for sr in sheet_results.values()]
            ox_list = sorted(set(p[0] for p in positions))
            oy_list = sorted(set(p[1] for p in positions), reverse=True)

            # 간단한 1D 클러스터링
            def cluster(vals, thr):
                groups = []
                for v in sorted(vals):
                    if groups and abs(v - np.mean(groups[-1])) < thr:
                        groups[-1].append(v)
                    else:
                        groups.append([v])
                return [np.mean(g) for g in groups]

            cols = cluster(ox_list, tile_w * 0.5)
            rows = cluster(oy_list, tile_h * 0.5)  # 내림차순
            dx = cols[1] - cols[0] if len(cols) >= 2 else tile_w
            dy = rows[0] - rows[1] if len(rows) >= 2 else tile_h

            # 빈 위치 후보 생성 (기존 그리드 + 상하좌우 1행/열 확장)
            used = set()
            for ox, oy in positions:
                ci = min(range(len(cols)), key=lambda i: abs(cols[i] - ox))
                ri = min(range(len(rows)), key=lambda i: abs(rows[i] - oy))
                used.add((ri, ci))

            candidates = []
            for r in range(-1, len(rows) + 1):
                for c in range(-1, len(cols) + 1):
                    if (r, c) in used:
                        continue
                    gx = (cols[c] if 0 <= c < len(cols) else
                          cols[0] + c * dx if c < 0 else
                          cols[-1] + (c - len(cols) + 1) * dx)
                    gy = (rows[r] if 0 <= r < len(rows) else
                          rows[0] + r * dy if r < 0 else
                          rows[-1] - (r - len(rows) + 1) * dy)
                    candidates.append((r, c, gx, gy))

            print(f"  그리드: 열={[f'{c:.0f}' for c in cols]}, "
                  f"행={[f'{r:.0f}' for r in rows]}, "
                  f"dx={dx:.0f}m, dy={dy:.0f}m")

            for num, path in failed_sheets:
                sheet_name = os.path.basename(path)
                print(f"\n--- 시트 {num} 재시도: {sheet_name} ---")

                if progress_callback:
                    progress_callback(num, total, f"{sheet_name} (재시도)")

                # 이미지 로드 → DT 생성 (1회)
                sub_img = load_image(path)
                map_image, map_bbox = extract_map_region(sub_img)
                del sub_img
                mh, mw = map_image.shape[:2]
                orange_mask = extract_orange_mask(map_image)
                skel, skel_pts, dt = build_skeleton_and_distmap(orange_mask)

                # 각 후보 위치에서 refine → 최저 cost 선택
                best = None
                for r, c, gx, gy in candidates:
                    aoi = compute_aoi(gx, gy, med_ps, (mh, mw))
                    bd = clip_shp_to_aoi(self.gdf, aoi, buffer_ratio=0.05)
                    pts = sample_points_from_boundaries(bd, num_points=3000)
                    if len(pts) < 50:
                        continue
                    rps, rox, roy, rcost = refine_position(
                        pts, dt, init_pixel_size=med_ps,
                        init_offset_x=gx, init_offset_y=gy,
                        image_shape=(mh, mw))
                    # ps가 비정상이면 무시 (med_ps 대비 ±50% 이내만 허용)
                    if rps < med_ps * 0.5 or rps > med_ps * 1.5:
                        print(f"    ({r},{c}): ps={rps:.3f} 비정상 (skip)")
                        continue
                    print(f"    ({r},{c}): cost={rcost:.3f}px")
                    if best is None or rcost < best[0]:
                        best = (rcost, rps, rox, roy, r, c)

                if best is None or best[0] > 5.0:
                    errors.append({'sheet': num, 'file': sheet_name, 'error': '매칭 실패'})
                    print(f"    모든 후보 실패")
                    continue

                cost, ps_f, ox_f, oy_f = best[0], best[1], best[2], best[3]
                print(f"    채택: ({best[4]},{best[5]}) cost={cost:.3f}px, ps={ps_f:.6f}")

                # 2차 리파인 (정밀 AOI)
                aoi2 = compute_aoi(ox_f, oy_f, ps_f, (mh, mw))
                bd2 = clip_shp_to_aoi(self.gdf, aoi2, buffer_ratio=0.05)
                pts2 = sample_points_from_boundaries(bd2, num_points=5000)
                if len(pts2) >= 50:
                    ps2, ox2, oy2, c2 = refine_position(
                        pts2, dt, init_pixel_size=ps_f,
                        init_offset_x=ox_f, init_offset_y=oy_f,
                        image_shape=(mh, mw))
                    if c2 <= cost:
                        ps_f, ox_f, oy_f, cost = ps2, ox2, oy2, c2

                # GCP 생성
                gcps = create_gcps(
                    boundaries=bd2, skel_points=skel_pts,
                    pixel_size=ps_f, offset_x=ox_f, offset_y=oy_f,
                    map_bbox=(0, 0, mw, mh), image_shape=(mh, mw),
                    dist_threshold_px=5.0, min_spacing_px=80.0)

                # JGW
                jgw = JGWParams(ps_f, 0.0, 0.0, -ps_f, ox_f, oy_f)

                # 출력 저장 (_crop 접미사로 원본 보호)
                try:
                    name_base = os.path.splitext(sheet_name)[0] + '_crop'
                    out_img_path = os.path.join(output_dir, f"{name_base}.jpg")
                    _, encoded = cv2.imencode('.jpg', map_image,
                                              [cv2.IMWRITE_JPEG_QUALITY, 95])
                    encoded.tofile(out_img_path)

                    jgw_path = os.path.join(output_dir, f"{name_base}.jgw")
                    write_jgw(jgw_path, jgw)

                    sheet_dpi = get_image_dpi(path)
                    sheet_scale = round(ps_f * sheet_dpi / 0.0254)
                    meta = {
                        'admin_code': admin_code, 'scale': int(sheet_scale),
                        'pixel_size': float(ps_f), 'cost': float(cost),
                        'gcp_count': len(gcps) if gcps else None,
                        'method': 'fft_hint',
                    }
                    meta_path = os.path.join(output_dir, f"{name_base}.meta.json")
                    with open(meta_path, 'w', encoding='utf-8') as f:
                        json.dump(meta, f, ensure_ascii=False, indent=2)

                    prj_path = os.path.join(output_dir, f"{name_base}.prj")
                    with open(prj_path, 'w') as f:
                        f.write(PRJ_5179)

                    if gcps:
                        try:
                            _, tps_path = write_vrt_output(
                                out_img_path, gcps, output_dir, name_base)
                            tps_vrts.append(tps_path)
                        except Exception as ve:
                            print(f"  VRT 생성 실패 (무시): {ve}")

                    individual_jgws.append(jgw_path)
                    print(f"  JGW 저장: {jgw_path}")

                    world_cx = ox_f + (mw / 2.0) * ps_f
                    world_cy = oy_f + (mh / 2.0) * (-ps_f)
                    main_cx = (world_cx - self.main_jgw.top_left_x) / self.main_jgw.pixel_size_x
                    main_cy = (world_cy - self.main_jgw.top_left_y) / self.main_jgw.pixel_size_y

                    sheet_results[num] = {
                        'image': map_image, 'jgw': jgw, 'jgw_path': jgw_path,
                        'main_cx': main_cx, 'main_cy': main_cy,
                        'scale': self.main_jgw.pixel_size_x / ps_f,
                    }
                    print(f"  재시도 성공! cost={cost:.3f}px")

                except Exception as e:
                    errors.append({'sheet': num, 'file': sheet_name, 'error': str(e)})
                    print(f"  재시도 오류: {e}")
        elif failed_sheets:
            for num, path in failed_sheets:
                errors.append({'sheet': num, 'file': os.path.basename(path), 'error': '매칭 실패'})

        n_success = len(sheet_results)
        grid_str = f"{n_rows}x{n_cols}"

        # 5. pixel_size 통일 (median) → JGW 덮어쓰기
        if n_success >= 2:
            all_ps = [abs(r['jgw'].pixel_size_x) for r in sheet_results.values()]
            unified_ps = float(np.median(all_ps))
            ps_spread = max(all_ps) - min(all_ps)
            print(f"\n=== pixel_size 통일 ===")
            print(f"  median={unified_ps:.10f}, spread={ps_spread:.10f}")

            for num, r in sheet_results.items():
                old_jgw = r['jgw']
                # ox, oy 유지 → top_left_x=ox, top_left_y=oy (크롭 이미지 기준)
                new_jgw = JGWParams(
                    pixel_size_x=unified_ps,
                    rotation_x=0.0,
                    rotation_y=0.0,
                    pixel_size_y=-unified_ps,
                    top_left_x=old_jgw.top_left_x,
                    top_left_y=old_jgw.top_left_y,
                )
                r['jgw'] = new_jgw
                r['scale'] = self.main_jgw.pixel_size_x / unified_ps

                # main_cx, main_cy 재계산 (통일된 ps 기준)
                map_h, map_w = r['image'].shape[:2]
                world_cx = new_jgw.top_left_x + (map_w / 2.0) * unified_ps
                world_cy = new_jgw.top_left_y + (map_h / 2.0) * (-unified_ps)
                r['main_cx'] = (world_cx - self.main_jgw.top_left_x) / self.main_jgw.pixel_size_x
                r['main_cy'] = (world_cy - self.main_jgw.top_left_y) / self.main_jgw.pixel_size_y

            # JGW 파일 덮어쓰기
            for num, r in sheet_results.items():
                write_jgw(r['jgw_path'], r['jgw'])

        # 6. 병합
        merged_path = None
        if n_success >= 2:
            if merged_output_path:
                merged_path = merged_output_path
            else:
                merged_path = os.path.join(output_dir, f"{admin_code}_merged.jpg")

            print(f"\n=== 병합 ({n_success}/{total}) ===")
            self._merge_sheets(sheet_results, merged_path,
                               crop_margin_x=crop_margin_x,
                               crop_margin_y=crop_margin_y)
        elif n_success == 1:
            print("\n시트 1개만 성공 — 병합 생략")

        return {
            'output': merged_path,
            'admin_code': admin_code,
            'grid': grid_str,
            'scale': expected_scale,
            'n_success': n_success,
            'n_total': total,
            'method': 'fft',
            'individual_jgws': individual_jgws,
            'tps_vrts': tps_vrts,
            'errors': errors,
        }


# ------------------------------------------------------------------
# 편의 함수 (plugin.py 호환 인터페이스)
# ------------------------------------------------------------------

def process_subdivisions(input_dir: str, output_path: str,
                         main_image_path: str, main_jgw_path: str,
                         progress_callback=None,
                         shp_path: str = None) -> dict:
    """분할도 처리 — plugin.py에서 직접 호출

    Args:
        input_dir: 분할도 이미지 폴더
        output_path: 병합 이미지 출력 경로
        main_image_path: 메인 이미지 경로
        main_jgw_path: 메인 이미지 JGW 경로
        progress_callback: fn(current, total, filename)
        shp_path: SHP 경로 (필수)
    """
    processor = SubdivisionProcessor(
        main_image_path, main_jgw_path, shp_path=shp_path,
    )
    output_dir = os.path.dirname(output_path)
    return processor.process(
        input_dir, output_dir,
        progress_callback=progress_callback,
        merged_output_path=output_path,
    )
