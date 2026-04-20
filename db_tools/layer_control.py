"""QGIS 레이어 편집/가시성 제어 유틸.

화면정의서 요구:
- 작업 시작 시 bnd_job_pg만 편집 가능, 나머지 레이어는 잠금(readOnly)
- 작업 종료 시 원상 복구 + 편집 내역 저장
- 워프 스캔 레이어 자동 로드 ({admin}_{sheet} 이름 규칙)

이 모듈은 QGIS 실행 환경에서만 동작. iface가 None인 테스트에서는 no-op.
"""
import os


EDIT_LAYER_NAMES = ('bnd_job_pg',)  # 편집 대상 — 나머지는 전부 readOnly


def start_work_mode(iface, edit_layer_names=EDIT_LAYER_NAMES):
    """작업 모드 진입. Returns snapshot dict (원복용).

    snapshot = {layer_id: readOnly_prev_state}
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
        else:
            if layer.isEditable():
                layer.commitChanges(stopEditing=True)
            layer.setReadOnly(True)
    return snap


def end_work_mode(iface, snapshot):
    """작업 모드 해제 + 편집 내역 저장.

    Returns (saved_count, errors[]).
    """
    from qgis.core import QgsProject, QgsVectorLayer
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
        # readOnly 복원
        if snapshot and lid in snapshot:
            layer.setReadOnly(bool(snapshot[lid]))
        else:
            layer.setReadOnly(False)
    return saved, errors


def load_warped_scans(iface, admin_code, warped_root):
    """특정 admin의 워프 스캔(시트별 jpg)을 QGIS 레이어로 로드.

    규칙:
      {warped_root}/{시도2}/{시군구5}/{admin}_{sheet}/{admin}_{sheet}.jpg
    이미 같은 이름의 레이어가 있으면 스킵. 성공한 레이어 이름 list 반환.

    이전 admin의 워프 레이어는 제거되지 않음 (호출자가 clear_warped_scans
    필요 시 호출).
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
        layer_name = folder
        if layer_name in existing_names:
            continue
        rlayer = QgsRasterLayer(fpath, layer_name)
        if rlayer.isValid():
            QgsProject.instance().addMapLayer(rlayer)
            added.append(layer_name)
    return added


def add_postgis_layer(profile, schema, table, layer_name=None,
                      geom_column='geom', primary_key='gid'):
    """PostGIS 테이블을 QGIS 레이어로 추가.

    Returns QgsVectorLayer or None (로드 실패).
    """
    from qgis.core import QgsVectorLayer, QgsDataSourceUri, QgsProject
    uri = QgsDataSourceUri()
    uri.setConnection(profile.host, str(profile.port), profile.database,
                      profile.username, profile.password or '')
    uri.setDataSource(schema, table, geom_column, '', primary_key)
    name = layer_name or f'{schema}.{table}'
    layer = QgsVectorLayer(uri.uri(False), name, 'postgres')
    if not layer.isValid():
        return None
    # 중복 이름 제거 (새로 추가하면서 기존 것 정리)
    for lid, lyr in list(QgsProject.instance().mapLayers().items()):
        if lyr.name() == name:
            QgsProject.instance().removeMapLayer(lid)
    QgsProject.instance().addMapLayer(layer)
    return layer


def clear_warped_scans(iface, exclude_admin=None):
    """기존 워프 스캔 레이어 제거 — {8자리}_{N-i} 패턴 이름.

    exclude_admin 지정 시 그 admin은 유지.
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
