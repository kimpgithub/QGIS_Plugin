"""QGIS 레이어 제어 + 로컬 GeoPackage 작업 데이터 관리.

데이터 흐름: 대전은 PostGIS에 직접 붙지 않는다. 경계는 로컬 GeoPackage 에서
디지타이징하고, 결과만 HTTPS 로 서버에 제출한다 (화면정의서 슬11:
"데이터를 postDB에 넣지 않고 QGIS 환경에서 작업").

- ensure_work_geopackage / add_geopackage_layer : 로컬 작업 데이터
- start_work_mode / end_work_mode               : 편집 대상만 활성, 나머지 잠금
- attach_autofill                               : split/추가 시 RI 속성 자동 부여
- layer_to_geojson                              : 제출용 GeoJSON 추출
- load_markup_layer                             : 발주자 마크업 readOnly 레이어
- load_warped_scans / clear_warped_scans        : 워프 스캔 자동 로드

QGIS 실행 환경에서만 동작. 테스트에서는 import 시 qgis 모듈 부재로 실패하므로
각 함수가 지연 import 한다.
"""
import os


# 편집 대상 레이어 이름 키워드 — 이 키워드를 포함하면 편집 활성, 나머지는 readOnly
EDIT_LAYER_NAMES = ('boundary', 'bnd_job')
WORK_LAYER_NAME = 'boundary'   # 작업 GeoPackage 안의 레이어 이름


# ============================================================
# 로컬 GeoPackage 작업 데이터
# ============================================================

def _boundary_fields():
    """작업 GeoPackage 의 속성 필드 — 서버 boundary 테이블과 정합."""
    from qgis.core import QgsField, QgsFields
    from qgis.PyQt.QtCore import QVariant
    fields = QgsFields()
    fields.append(QgsField('adm_cd', QVariant.String, len=8))
    fields.append(QgsField('adm_nm', QVariant.String, len=100))
    fields.append(QgsField('ri_cd', QVariant.String, len=10))
    fields.append(QgsField('ri_nm', QVariant.String, len=100))
    fields.append(QgsField('remark', QVariant.String))
    fields.append(QgsField('status', QVariant.String, len=20))
    return fields


def ensure_work_geopackage(path, layer_name=WORK_LAYER_NAME):
    """작업 GeoPackage 를 보장 — 없으면 boundary 스키마로 생성. 레이어 반환.

    Returns QgsVectorLayer (유효) or None.
    """
    from qgis.core import (QgsVectorLayer, QgsVectorFileWriter, QgsWkbTypes,
                           QgsCoordinateReferenceSystem, QgsProject)
    uri = f'{path}|layername={layer_name}'
    if os.path.exists(path):
        lyr = QgsVectorLayer(uri, layer_name, 'ogr')
        return lyr if lyr.isValid() else None

    crs = QgsCoordinateReferenceSystem('EPSG:5179')
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = 'GPKG'
    opts.layerName = layer_name
    writer = QgsVectorFileWriter.create(
        path, _boundary_fields(), QgsWkbTypes.MultiPolygon, crs,
        QgsProject.instance().transformContext(), opts)
    ok = writer is not None and writer.hasError() == QgsVectorFileWriter.NoError
    del writer   # flush
    if not ok:
        return None
    lyr = QgsVectorLayer(uri, layer_name, 'ogr')
    return lyr if lyr.isValid() else None


def add_geopackage_layer(path, layer_name=WORK_LAYER_NAME):
    """작업 GeoPackage 레이어를 QGIS 프로젝트에 추가 (중복 이름 정리). 반환 레이어."""
    from qgis.core import QgsVectorLayer, QgsProject
    lyr = QgsVectorLayer(f'{path}|layername={layer_name}', layer_name, 'ogr')
    if not lyr.isValid():
        return None
    for lid, existing in list(QgsProject.instance().mapLayers().items()):
        if existing.name() == layer_name:
            QgsProject.instance().removeMapLayer(lid)
    QgsProject.instance().addMapLayer(lyr)
    return lyr


def layer_to_geojson(layer):
    """레이어 피처를 GeoJSON FeatureCollection(dict) 으로 추출.

    QgsJsonExporter 가 기본적으로 EPSG:4326 으로 변환 — GeoJSON 표준.
    geom 이 없는 피처는 제외 (미완성 행은 제출 안 함).
    """
    import json
    from qgis.core import QgsJsonExporter
    exporter = QgsJsonExporter(layer)
    feats = [f for f in layer.getFeatures()
             if f.hasGeometry() and not f.geometry().isEmpty()]
    if not feats:
        return {'type': 'FeatureCollection', 'features': []}
    return json.loads(exporter.exportFeatures(feats))


# ============================================================
# 발주자 마크업 회수 레이어
# ============================================================

def load_markup_layer(geojson_dict, layer_name='발주자_마크업'):
    """서버에서 받은 마크업 GeoJSON 을 readOnly 레이어로 로드.

    임시 .geojson 파일로 쓴 뒤 ogr 로 로드. 같은 이름 레이어는 교체.
    Returns (layer, feature_count) or (None, 0).
    """
    import json
    import tempfile
    from qgis.core import QgsVectorLayer, QgsProject
    feats = (geojson_dict or {}).get('features', [])
    tf = tempfile.NamedTemporaryFile(
        suffix='.geojson', delete=False, mode='w', encoding='utf-8')
    json.dump(geojson_dict or {'type': 'FeatureCollection', 'features': []}, tf)
    tf.close()
    lyr = QgsVectorLayer(tf.name, layer_name, 'ogr')
    if not lyr.isValid():
        return None, 0
    lyr.setReadOnly(True)
    for lid, existing in list(QgsProject.instance().mapLayers().items()):
        if existing.name() == layer_name:
            QgsProject.instance().removeMapLayer(lid)
    QgsProject.instance().addMapLayer(lyr)
    return lyr, len(feats)


# ============================================================
# 작업 모드 — 편집 대상만 활성, 나머지 readOnly
# ============================================================

def start_work_mode(iface, edit_layer_names=EDIT_LAYER_NAMES):
    """작업 모드 진입. snapshot dict(원복용) 반환.

    편집 대상 레이어는 편집 활성 + autofill 콜백 설치, 나머지는 readOnly.
    """
    from qgis.core import QgsProject, QgsVectorLayer
    snap = {}
    for lid, layer in QgsProject.instance().mapLayers().items():
        if not isinstance(layer, QgsVectorLayer):
            continue
        snap[lid] = layer.readOnly()
        nm = layer.name().lower()
        if any(t in nm for t in edit_layer_names):
            layer.setReadOnly(False)
            if not layer.isEditable():
                layer.startEditing()
            attach_autofill(layer, edit_layer_names)
        else:
            if layer.isEditable():
                layer.commitChanges(stopEditing=True)
            layer.setReadOnly(True)
    return snap


def end_work_mode(iface, snapshot):
    """작업 모드 해제 + 편집 내역 저장. (saved_count, errors[]) 반환."""
    from qgis.core import QgsProject, QgsVectorLayer
    detach_autofill()
    saved = 0
    errors = []
    for lid, layer in QgsProject.instance().mapLayers().items():
        if not isinstance(layer, QgsVectorLayer):
            continue
        if layer.isEditable():
            if layer.commitChanges(stopEditing=True):
                saved += 1
            else:
                errors.append(f'{layer.name()}: commit 실패')
        if snapshot and lid in snapshot:
            layer.setReadOnly(bool(snapshot[lid]))
        else:
            layer.setReadOnly(False)
    return saved, errors


# ============================================================
# 현재 선택 RI — split/추가 시 자동 부여될 속성
# ============================================================

PROJ_VAR_RI_CD = 'gst_ri_cd_current'
PROJ_VAR_RI_NM = 'gst_ri_nm_current'
PROJ_VAR_ADM_CD = 'gst_adm_cd_current'
PROJ_VAR_ADM_NM = 'gst_adm_nm_current'


def set_current_ri(adm_cd='', adm_nm='', ri_cd='', ri_nm=''):
    from qgis.core import QgsProject, QgsExpressionContextUtils
    p = QgsProject.instance()
    QgsExpressionContextUtils.setProjectVariable(p, PROJ_VAR_ADM_CD, adm_cd)
    QgsExpressionContextUtils.setProjectVariable(p, PROJ_VAR_ADM_NM, adm_nm)
    QgsExpressionContextUtils.setProjectVariable(p, PROJ_VAR_RI_CD, ri_cd)
    QgsExpressionContextUtils.setProjectVariable(p, PROJ_VAR_RI_NM, ri_nm)


def get_current_ri():
    from qgis.core import QgsProject, QgsExpressionContextUtils
    ctx = QgsExpressionContextUtils.projectScope(QgsProject.instance())
    return dict(
        adm_cd=ctx.variable(PROJ_VAR_ADM_CD) or '',
        adm_nm=ctx.variable(PROJ_VAR_ADM_NM) or '',
        ri_cd=ctx.variable(PROJ_VAR_RI_CD) or '',
        ri_nm=ctx.variable(PROJ_VAR_RI_NM) or '',
    )


# ============================================================
# 자동 속성 부여 (featureAdded 훅)
# ============================================================

_active_callbacks = {}   # layer_id → slot


def attach_autofill(layer, target_layer_names=EDIT_LAYER_NAMES):
    """layer.featureAdded 시 현재 선택 RI 의 adm_cd/adm_nm/ri_cd/ri_nm 자동 기록."""
    nm = layer.name().lower()
    if not any(t in nm for t in target_layer_names):
        return False
    lid = layer.id()
    if lid in _active_callbacks:
        return False

    def _on_added(fid, _layer=layer):
        cur = get_current_ri()
        if not cur['ri_cd']:
            return
        fields = _layer.fields()
        for col, val in (('adm_cd', cur['adm_cd']),
                         ('adm_nm', cur['adm_nm']),
                         ('ri_cd', cur['ri_cd']),
                         ('ri_nm', cur['ri_nm'])):
            idx = fields.indexOf(col)
            if idx >= 0 and val:
                _layer.changeAttributeValue(fid, idx, val)

    layer.featureAdded.connect(_on_added)
    _active_callbacks[lid] = _on_added
    return True


def detach_autofill(layer=None):
    """featureAdded 콜백 해제. layer=None 이면 전체 해제."""
    from qgis.core import QgsProject
    targets = [layer.id()] if layer else list(_active_callbacks.keys())
    for lid in targets:
        if lid not in _active_callbacks:
            continue
        lyr = QgsProject.instance().mapLayer(lid)
        slot = _active_callbacks.pop(lid)
        if lyr is not None:
            try:
                lyr.featureAdded.disconnect(slot)
            except (TypeError, RuntimeError):
                pass


# ============================================================
# 워프 스캔 자동 로드
# ============================================================

def load_warped_scans(iface, admin_code, warped_root):
    """특정 admin 의 워프 스캔(시트별 jpg)을 QGIS 레이어로 로드.

    규칙: {warped_root}/{시도2}/{시군구5}/{admin}_{sheet}/{admin}_{sheet}.jpg
    이미 있으면 스킵. 추가된 레이어 이름 list 반환.
    """
    from qgis.core import QgsProject, QgsRasterLayer
    if not warped_root or not os.path.isdir(warped_root):
        return []
    sub = os.path.join(warped_root, admin_code[:2], admin_code[:5])
    if not os.path.isdir(sub):
        return []
    added = []
    existing_names = {lyr.name() for lyr in
                      QgsProject.instance().mapLayers().values()}
    for folder in sorted(os.listdir(sub)):
        if not folder.startswith(f'{admin_code}_'):
            continue
        fpath = os.path.join(sub, folder, f'{folder}.jpg')
        if not os.path.exists(fpath):
            continue
        if folder in existing_names:
            continue
        rlayer = QgsRasterLayer(fpath, folder)
        if rlayer.isValid():
            QgsProject.instance().addMapLayer(rlayer)
            added.append(folder)
    return added


def clear_warped_scans(iface, exclude_admin=None):
    """기존 워프 스캔 레이어 제거 — {8자리}_{N-i} 패턴 이름.

    exclude_admin 지정 시 그 admin 은 유지. 제거 개수 반환.
    """
    import re
    from qgis.core import QgsProject
    pat = re.compile(r'^(\d{8})_(\d+-\d+)$')
    to_remove = []
    for lid, layer in QgsProject.instance().mapLayers().items():
        m = pat.match(layer.name())
        if not m:
            continue
        if exclude_admin and m.group(1) == exclude_admin:
            continue
        to_remove.append(lid)
    for lid in to_remove:
        QgsProject.instance().removeMapLayer(lid)
    return len(to_remove)
