"""GIS Scan Tools — 5-단계 파이프라인 도구 모듈.

각 stage는 독립 CLI 진입점:
  python -m gis_scan_tools.tools.stage1_pdf_georef
  python -m gis_scan_tools.tools.stage2_scan_identify
  python -m gis_scan_tools.tools.stage3_scan_warp
  python -m gis_scan_tools.tools.stage4_merge
  python -m gis_scan_tools.tools.stage5_validate

코어 모듈: common (이미지 I/O · JGW · ICP/FFT · GCP 유틸),
          shp_georeferencer (SIFT 폴백), boundary_validator (Stage 5).
"""
