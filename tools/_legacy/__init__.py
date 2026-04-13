"""
GIS Scan Tools - 도구 모듈
"""

# 공통 유틸리티
from .common import (
    CRS_EPSG,
    DEFAULT_DPI,
    PRJ_5179,
    JGWParams,
    load_image,
    save_image,
    parse_jgw,
    write_jgw,
    write_aux_xml,
    extract_map_region,
    detect_frame_thickness,
    extract_orange_mask,
    convert_pdf_to_images,
    convert_pdf_to_jpg,
    get_image_dpi,
    build_skeleton_and_distmap,
    estimate_initial_params,
    compute_aoi,
    clip_shp_to_aoi,
    sample_points_from_boundaries,
    icp_optimize,
    extract_all_vertices,
    create_gcps,
    write_vrt_output,
    refine_position,
    build_proximity_map,
    rasterize_boundaries,
    fft_match_position,
)

# 도구 클래스/함수
from .boundary_validator import BoundaryValidator
from .image_validator import validate_images
try:
    from .subdivision_processor import SubdivisionProcessor, process_subdivisions
except ImportError:
    SubdivisionProcessor = None
    process_subdivisions = None
from .shp_georeferencer import SHPGeoreferencer

__all__ = [
    # 상수
    'CRS_EPSG',
    'DEFAULT_DPI',
    'PRJ_5179',
    # 데이터 클래스
    'JGWParams',
    # 이미지 유틸리티
    'load_image',
    'save_image',
    'parse_jgw',
    'write_jgw',
    'write_aux_xml',
    'extract_map_region',
    'detect_frame_thickness',
    'extract_orange_mask',
    'convert_pdf_to_images',
    'convert_pdf_to_jpg',
    'get_image_dpi',
    # DT ICP 함수
    'build_skeleton_and_distmap',
    'estimate_initial_params',
    'compute_aoi',
    'clip_shp_to_aoi',
    'sample_points_from_boundaries',
    'icp_optimize',
    'extract_all_vertices',
    'create_gcps',
    'write_vrt_output',
    # FFT + Refine 함수
    'refine_position',
    'build_proximity_map',
    'rasterize_boundaries',
    'fft_match_position',
    # 도구 클래스
    'BoundaryValidator',
    'validate_images',
    'SubdivisionProcessor',
    'process_subdivisions',
    'SHPGeoreferencer',
]
