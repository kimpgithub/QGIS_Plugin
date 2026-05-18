"""QGIS 작업 환경 구성 + 레이어 제어.

화면정의서 슬11~12: 작업 폴더의 하위 폴더 규칙(01_~13_)으로 13개 슬롯을
자동 인식해 QGIS 에 로드하고, 작업데이터 레이어만 편집 가능하게 한다.
데이터는 postDB 에 넣지 않고 로컬 파일로 작업, 결과만 HTTPS 로 제출한다.

- detect_work_folder / load_workspace : 작업 폴더 → 13레이어 구성
- start_work_mode / end_work_mode     : 작업데이터만 편집 활성, 나머지 잠금
- attach_autofill                     : split/추가 시 RI 속성 자동 부여
- boundary_to_geojson                 : 제출용 GeoJSON 추출 (속성 정규화)
- load_markup_layer                   : 발주자 마크업 readOnly 레이어
- load_warped_scans / clear_warped_scans : 워프 스캔 자동 로드

QGIS 실행 환경에서만 동작 — 각 함수가 qgis 모듈을 지연 import 한다.
"""
import glob
import os
import re


# 편집 대상 레이어 이름 키워드 — 포함하면 편집 활성, 나머지는 readOnly
EDIT_LAYER_NAMES = ('작업데이터', 'bnd_job')

# QML 스타일 폴더 (행정리작업데이터.qgz 에서 추출)
STYLE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'data', 'styles')

# 작업 폴더 슬롯 정의 — (하위폴더 번호, 키, 표시명, 종류, 기본표시, 필수)
#   종류: 'shp' | 'xlsx' | 'raster_dir'
WORK_SLOTS = [
    ('01', 'building',     '건물',            'shp',        False, False),
    ('02', 'building_grp', '건물군',          'shp',        False, False),
    ('03', 'road',         '도로구간',        'shp',        False, False),
    ('04', 'road_real',    '실폭도로',        'shp',        False, False),
    ('05', 'rail',         '철도',            'shp',        False, False),
    ('06', 'river',        '하천',            'shp',        False, False),
    ('07', 'li_bnd',       '법정리경계',      'shp',        False, False),
    ('08', 'cadastral',    '지적도',          'shp',        False, False),
    ('09', 'adm_bnd',      '행정경계',        'shp',        True,  True),
    ('10', 'work_data',    '작업데이터',      'shp',        True,  True),
    ('11', 'merged_img',   '병합이미지',      'raster_dir', True,  True),
    ('12', 'roster',       '명부',            'xlsx',       False, True),
    ('13', 'ri_2021',      '21년 행정리경계', 'shp',        False, False),
]


# ============================================================
# 작업 폴더 → 13레이어 구성
# ============================================================

def detect_work_folder(root):
    """작업 폴더의 하위 폴더 규칙(01_~13_)으로 슬롯별 파일 자동 인식.

    Returns {slot_key: path or None}. raster_dir 슬롯은 폴더 경로를 값으로.
    """
    result = {key: None for _n, key, *_ in WORK_SLOTS}
    if not root or not os.path.isdir(root):
        return result
    # 하위 폴더 → 번호 매칭 (예: "09_행정경계" → "09")
    subdirs = {}
    for name in os.listdir(root):
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue
        m = re.match(r'(\d{2})', name)
        if m:
            subdirs[m.group(1)] = full
    for num, key, _label, kind, _on, _req in WORK_SLOTS:
        folder = subdirs.get(num)
        if not folder:
            continue
        if kind == 'shp':
            hits = glob.glob(os.path.join(folder, '*.shp'))
            if hits:
                result[key] = hits[0]
        elif kind == 'xlsx':
            hits = (glob.glob(os.path.join(folder, '*.xlsx'))
                    + glob.glob(os.path.join(folder, '*.xlsm')))
            if hits:
                result[key] = hits[0]
        elif kind == 'raster_dir':
            if glob.glob(os.path.join(folder, '*.jpg')):
                result[key] = folder
    return result


def detect_merged_admin_codes(slots):
    """병합이미지 폴더의 jpg 파일명 앞 8자리 → admin code set.

    명부 38K 행을 다 보여줄 필요 없이, 실제 병합이미지가 있는 읍면동만
    명부 테이블에 노출하기 위함.
    """
    path = (slots or {}).get('merged_img')
    if not path or not os.path.isdir(path):
        return set()
    codes = set()
    for jpg in glob.glob(os.path.join(path, '*.jpg')):
        nm = os.path.splitext(os.path.basename(jpg))[0]
        m = re.match(r'(\d{8})', nm)
        if m:
            codes.add(m.group(1))
    return codes


def _remove_by_name(name):
    from qgis.core import QgsProject
    for lid, lyr in list(QgsProject.instance().mapLayers().items()):
        if lyr.name() == name:
            QgsProject.instance().removeMapLayer(lid)


def _apply_style(layer, key):
    """data/styles/{key}.qml 이 있으면 로드. (행정리작업데이터.qgz 추출본)"""
    qml = os.path.join(STYLE_DIR, f'{key}.qml')
    if not os.path.exists(qml):
        return False
    try:
        _msg, ok = layer.loadNamedStyle(qml)
        if ok:
            layer.triggerRepaint()
        return bool(ok)
    except Exception:
        return False


def _zoom_to_work_data(iface, work_layer):
    """작업데이터 레이어 범위로 캔버스 이동 (CRS 변환 포함)."""
    if iface is None or work_layer is None:
        return
    try:
        from qgis.core import (QgsProject, QgsCoordinateTransform,
                               QgsRectangle)
        extent = work_layer.extent()
        if extent is None or extent.isEmpty():
            return
        canvas = iface.mapCanvas()
        dst_crs = canvas.mapSettings().destinationCrs()
        src_crs = work_layer.crs()
        if src_crs.isValid() and dst_crs.isValid() and src_crs != dst_crs:
            tr = QgsCoordinateTransform(
                src_crs, dst_crs, QgsProject.instance())
            extent = tr.transformBoundingBox(extent)
        if isinstance(extent, QgsRectangle) and not extent.isEmpty():
            extent.scale(1.05)
            canvas.setExtent(extent)
            canvas.refresh()
    except Exception:
        pass


def load_workspace(slots, iface=None):
    """슬롯 dict({key: path})를 QGIS 에 로드. on/off 기본값 + QML 스타일 적용.

    명부(xlsx)는 레이어가 아니므로 건너뜀 — 호출자가 별도 로드.
    iface 가 주어지면 로드 후 작업데이터 범위로 캔버스 이동.
    Returns (work_data_layer or None, summary[list of (label, status)]).
    """
    from qgis.core import QgsProject, QgsVectorLayer, QgsRasterLayer
    proj = QgsProject.instance()
    root = proj.layerTreeRoot()
    work_layer = None
    summary = []
    for _num, key, label, kind, default_on, required in WORK_SLOTS:
        path = slots.get(key)
        if not path:
            if required:
                summary.append((label, '없음 (필수)'))
            continue
        if kind == 'shp':
            lyr = QgsVectorLayer(path, label, 'ogr')
            if not lyr.isValid():
                summary.append((label, '로드 실패'))
                continue
            _remove_by_name(label)
            _apply_style(lyr, key)
            proj.addMapLayer(lyr)
            node = root.findLayer(lyr.id())
            if node:
                node.setItemVisibilityChecked(default_on)
            if key == 'work_data':
                work_layer = lyr
            summary.append((label, f'OK ({lyr.featureCount()}개)'))
        elif kind == 'raster_dir':
            added = 0
            for jpg in sorted(glob.glob(os.path.join(path, '*.jpg'))):
                nm = os.path.splitext(os.path.basename(jpg))[0]
                rl = QgsRasterLayer(jpg, nm)
                if not rl.isValid():
                    continue
                _remove_by_name(nm)
                _apply_style(rl, key)
                proj.addMapLayer(rl)
                node = root.findLayer(rl.id())
                if node:
                    node.setItemVisibilityChecked(default_on)
                added += 1
            summary.append((label, f'OK ({added}장)'))
    _zoom_to_work_data(iface, work_layer)
    return work_layer, summary


# ============================================================
# 제출용 GeoJSON 추출
# ============================================================

def boundary_to_geojson(layer):
    """작업데이터 레이어 → GeoJSON FeatureCollection(dict).

    QgsJsonExporter 가 EPSG:4326 으로 변환(GeoJSON 표준). 속성은 서버
    boundary 스키마에 맞춰 {adm_cd,adm_nm,ri_cd,ri_nm,remark} 만, 소문자
    키로 정규화 (작업 SHP 는 RI_CD/RI_NM 대문자라서). geom 없는 피처 제외.
    """
    import json
    from qgis.core import QgsJsonExporter
    keep = ('adm_cd', 'adm_nm', 'ri_cd', 'ri_nm', 'remark')
    feats = [f for f in layer.getFeatures()
             if f.hasGeometry() and not f.geometry().isEmpty()]
    if not feats:
        return {'type': 'FeatureCollection', 'features': []}
    fc = json.loads(QgsJsonExporter(layer).exportFeatures(feats))
    for feat in fc.get('features', []):
        props = feat.get('properties', {}) or {}
        feat['properties'] = {k.lower(): v for k, v in props.items()
                              if k.lower() in keep}
    return fc


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
    _remove_by_name(layer_name)
    QgsProject.instance().addMapLayer(lyr)
    return lyr, len(feats)


# ============================================================
# 작업 모드 — 작업데이터만 편집 활성, 나머지 readOnly
# ============================================================

def start_work_mode(iface, edit_layer_names=EDIT_LAYER_NAMES, attach=True):
    """작업 모드 진입. snapshot dict(원복용) 반환.

    편집 대상 레이어는 편집 활성, 나머지는 readOnly. attach=True 면 기존
    autofill 콜백(get_current_ri 기반) 설치 — 새 split→팝업 흐름을 쓰는
    호출자는 attach=False 로 두고 직접 featureAdded 를 처리하면 된다.
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
            if attach:
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
    """layer.featureAdded 시 현재 선택 RI 의 adm/ri 속성 자동 기록.

    작업 SHP 는 컬럼이 대문자(RI_CD/RI_NM)일 수 있어 대소문자 무시 검색.
    """
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
        # 대소문자 무시 컬럼 인덱스
        idx_of = {fields.at(i).name().lower(): i
                  for i in range(fields.count())}
        for col, val in (('adm_cd', cur['adm_cd']),
                         ('adm_nm', cur['adm_nm']),
                         ('ri_cd', cur['ri_cd']),
                         ('ri_nm', cur['ri_nm'])):
            idx = idx_of.get(col, -1)
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
# 작업데이터 초기 시드 — 행정경계 폴리곤 복사
# ============================================================

def _field_idx_ci(layer, name):
    """대소문자 무시 필드 인덱스. 없으면 -1."""
    fields = layer.fields()
    low = name.lower()
    for i in range(fields.count()):
        if fields.at(i).name().lower() == low:
            return i
    return -1


def seed_work_layer_from_adm(work_layer, adm_layer, adm_codes):
    """작업데이터가 비어 있을 때 행정경계 폴리곤을 복사해 초기 모집단 생성.

    - adm_codes 의 adm_cd 와 매칭되는 행정경계 피처를 작업데이터로 옮긴다.
    - 공통 필드(sido_cd, sigungu_cd, adm_cd, adm_nm, …)만 복사, ri_cd/ri_nm
      은 비워 둔다 (이후 split + 팝업으로 부여).
    - 좌표계 변환은 두 레이어가 동일 CRS 라고 가정 (보통 5179 통일).
    Returns 추가된 피처 수.
    """
    from qgis.core import QgsFeature, QgsFeatureRequest
    if work_layer is None or adm_layer is None or not adm_codes:
        return 0
    src_adm_idx = _field_idx_ci(adm_layer, 'adm_cd')
    if src_adm_idx < 0:
        return 0

    # 작업데이터 컬럼 매핑 — 소스에서 같은 이름 컬럼만 옮긴다
    src_fields = adm_layer.fields()
    dst_fields = work_layer.fields()
    src_to_dst = {}
    for si in range(src_fields.count()):
        sn = src_fields.at(si).name()
        di = _field_idx_ci(work_layer, sn)
        if di >= 0:
            src_to_dst[si] = di

    if not work_layer.isEditable():
        work_layer.startEditing()

    code_set = set(str(c).strip() for c in adm_codes)
    req = QgsFeatureRequest()
    added = 0
    for src in adm_layer.getFeatures(req):
        code = src.attribute(src_adm_idx)
        if code is None or str(code).strip() not in code_set:
            continue
        dst = QgsFeature(dst_fields)
        for si, di in src_to_dst.items():
            dst.setAttribute(di, src.attribute(si))
        if src.hasGeometry():
            dst.setGeometry(src.geometry())
        if work_layer.addFeature(dst):
            added += 1
    return added


# ============================================================
# 작업데이터 분류 심볼 — ri_cd 채워진 조각만 색, 빈 조각은 회색
# ============================================================

def restrict_work_layer_to_codes(layer, adm_codes):
    """작업데이터 레이어에 adm_cd subset filter 를 적용.

    작업데이터 SHP 에 다른 읍면동 피처가 섞여 있어도 (예: 데모 데이터)
    현재 작업 대상 코드 집합만 보이도록 한다. 빈 집합이면 필터 해제.
    """
    if layer is None:
        return False
    if not adm_codes:
        layer.setSubsetString('')
        return True
    quoted = ','.join("'" + str(c).replace("'", "''") + "'"
                      for c in adm_codes)
    expr = f'"adm_cd" IN ({quoted})'
    layer.setSubsetString(expr)
    return True


def apply_work_data_categorized_renderer(layer):
    """작업데이터를 ri_cd 별 분류 심볼로 표현.

    - 카테고리 값: ri_cd
    - 라벨: 'ri_cd ri_nm' (가독성). 빈/NULL 은 '(미부여)' 단일 카테고리.
    - 빈값 = 연한 회색, 값 있음 = 팔레트 순환 색.
    QML 스타일을 덮어쓰므로 호출 시점 주의.
    """
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsRendererCategory,
                           QgsFillSymbol)
    idx_cd = _field_idx_ci(layer, 'ri_cd')
    if idx_cd < 0:
        return False
    idx_nm = _field_idx_ci(layer, 'ri_nm')
    attr_name = layer.fields().at(idx_cd).name()

    palette = ['#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00',
               '#ffff33', '#a65628', '#f781bf', '#66c2a5', '#fc8d62',
               '#8da0cb', '#e78ac3', '#a6d854', '#ffd92f']

    # 빈/NULL 단일 카테고리 (값 None — QGIS 가 NULL 매칭으로 처리)
    empty_sym = QgsFillSymbol.createSimple({
        'color': '200,200,200,80',
        'outline_color': '#666666',
        'outline_width': '0.3',
    })
    cats = [QgsRendererCategory(None, empty_sym, '(미부여)')]

    # ri_cd → ri_nm 매핑 수집 (현재 subset 범위 내 피처만 순회)
    cd_to_nm = {}
    for f in layer.getFeatures():
        cd = f.attribute(idx_cd)
        if cd is None or str(cd).strip() == '':
            continue
        cd_str = str(cd).strip()
        if idx_nm >= 0:
            nm = f.attribute(idx_nm)
            if nm is not None and str(nm).strip():
                cd_to_nm.setdefault(cd_str, str(nm).strip())
        else:
            cd_to_nm.setdefault(cd_str, '')

    for i, cd in enumerate(sorted(cd_to_nm.keys())):
        nm = cd_to_nm[cd]
        label = f'{cd} {nm}'.strip()
        sym = QgsFillSymbol.createSimple({
            'color': palette[i % len(palette)] + ',140',
            'outline_color': '#333333',
            'outline_width': '0.3',
        })
        cats.append(QgsRendererCategory(cd, sym, label))

    renderer = QgsCategorizedSymbolRenderer(attr_name, cats)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return True


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
