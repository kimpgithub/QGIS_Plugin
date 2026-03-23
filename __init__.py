"""
GIS Scan Tools - QGIS Plugin
스캔 이미지 처리 및 검수 도구 모음
"""


def classFactory(iface):
    """QGIS 플러그인 로드 시 호출되는 함수"""
    from .plugin import GISScanToolsPlugin
    return GISScanToolsPlugin(iface)
