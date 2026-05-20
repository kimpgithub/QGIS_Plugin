"""DB 작업 플러그인 진입점 — 서울 서버 연동 (HTTPS 배치 동기화).

툴바 두 번째 아이콘에서 열리는 다이얼로그.

데이터 흐름: 대전은 로컬에서 경계를 디지타이징하고, 결과만 HTTPS로 서버에
제출한다. 발주자 마크업은 HTTPS로 회수한다. PostGIS/MinIO 직접 접속 없음.

구조:
- DBEditorDialog: 탭 컨테이너
  - [1] 서버 연결 (URL/토큰/S3 키)
  - [2] 행정리 작업 (작업 폴더 자동인식 → 13레이어 구성 → 편집 → 제출/마크업)
"""
import os

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QFormLayout,
    QTextEdit, QMessageBox, QGroupBox, QApplication, QFileDialog,
    QTableWidget, QTableWidgetItem, QDockWidget, QComboBox, QCheckBox,
)

from .db_tools import api_client, excel_loader, layer_control
from .db_tools.api_client import ServerConfig, save_config, load_config


PLUGIN_DIR = os.path.dirname(__file__)


# ============================================================
# 엑셀 Y/N 저장 워커 — UI 멈춤 방지
# ============================================================

class WorkYnSaveWorker(QThread):
    """update_work_yn 한 건을 백그라운드에서 실행.

    UI 콤보 변경은 즉시 반영하고 엑셀 저장은 비동기. 실패 시 호출부가
    UI 를 revert 한다. 매번 새 인스턴스 — 직렬 저장.
    """
    done = pyqtSignal(int, str, str, int)  # row_idx, adm_cd, ri_cd, n
    failed = pyqtSignal(int, str, str, str, str)  # row_idx, adm_cd, ri_cd, old, err

    def __init__(self, row_idx, path, adm_cd, ri_cd, new_val, old_val):
        super().__init__()
        self.row_idx = row_idx
        self.path = path
        self.adm_cd = adm_cd
        self.ri_cd = ri_cd
        self.new_val = new_val
        self.old_val = old_val

    def run(self):
        try:
            n = excel_loader.update_work_yn(
                self.path, self.adm_cd, self.ri_cd, self.new_val)
            self.done.emit(self.row_idx, self.adm_cd, self.ri_cd, n)
        except Exception as e:
            self.failed.emit(self.row_idx, self.adm_cd, self.ri_cd,
                             self.old_val, str(e))


# ============================================================
# split 후 행정리 부여 다이얼로그
# ============================================================

class RiAssignDialog(QDialog):
    """split 직후 새 피처에 부여할 행정리를 고른다.

    후보 — 현재 로드된 명부 중 작업여부 != 'Y' 행. 검색 + 더블클릭 OK.
    chosen 속성으로 선택 결과(dict) 노출. 취소면 chosen=None.
    """

    def __init__(self, candidates, parent=None):
        super().__init__(parent)
        self.setWindowTitle('행정리 부여')
        self.resize(520, 480)
        self.candidates = list(candidates)
        self._filtered_idx = list(range(len(self.candidates)))
        self.chosen = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            '이 조각에 부여할 <b>행정리</b>를 선택하세요. (미완료만 표시)'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('행정리 코드/명 검색')
        self.search.textChanged.connect(self._refilter)
        layout.addWidget(self.search)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ['읍면동', '행정리코드', '행정리명', '비고'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self._accept_selected)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton('취소(부여 안 함)')
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton('부여')
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._accept_selected)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

        self._fill_table()
        if self.candidates:
            self.table.selectRow(0)

    def _fill_table(self):
        self.table.setRowCount(len(self._filtered_idx))
        for row, src_idx in enumerate(self._filtered_idx):
            r = self.candidates[src_idx]
            adm = f"{r.get('adm_cd','')} {r.get('adm_nm','')}".strip()
            self.table.setItem(row, 0, QTableWidgetItem(adm))
            self.table.setItem(row, 1, QTableWidgetItem(r.get('ri_cd', '')))
            self.table.setItem(row, 2, QTableWidgetItem(r.get('ri_nm', '')))
            self.table.setItem(row, 3, QTableWidgetItem(r.get('remark', '')))
        self.table.resizeColumnsToContents()

    def _refilter(self, text):
        t = (text or '').strip().lower()
        if not t:
            self._filtered_idx = list(range(len(self.candidates)))
        else:
            self._filtered_idx = [
                i for i, r in enumerate(self.candidates)
                if t in (r.get('ri_cd', '') + ' '
                         + r.get('ri_nm', '')).lower()]
        self._fill_table()
        if self._filtered_idx:
            self.table.selectRow(0)

    def _accept_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        src_idx = self._filtered_idx[rows[0].row()]
        self.chosen = self.candidates[src_idx]
        self.accept()


# ============================================================
# 마크업 검토 다이얼로그 (Phase C) — 줌/속성적용/split가이드/dissolve가이드
# ============================================================

def _feature_geometry(feat):
    """GeoJSON feature → QgsGeometry. shapely 미설치 시 Point/LineString 만 지원."""
    from qgis.core import QgsGeometry
    g = (feat or {}).get('geometry') or {}
    t = g.get('type')
    c = g.get('coordinates') or []
    try:
        from shapely.geometry import shape
        return QgsGeometry.fromWkt(shape(g).wkt)
    except Exception:
        pass
    if t == 'Point' and len(c) >= 2:
        return QgsGeometry.fromWkt(f'POINT ({c[0]} {c[1]})')
    if t == 'LineString':
        pts = ', '.join(f'{x} {y}' for x, y in c)
        return QgsGeometry.fromWkt(f'LINESTRING ({pts})')
    if t == 'MultiLineString':
        parts = ['(' + ', '.join(f'{x} {y}' for x, y in line) + ')'
                 for line in c]
        return QgsGeometry.fromWkt(f'MULTILINESTRING ({", ".join(parts)})')
    return None


class MarkupReviewDialog(QDialog):
    """발주자 마크업 목록 + kind 별 액션 (modeless)."""

    def __init__(self, iface, work_layer, features, parent=None):
        super().__init__(parent)
        self.setWindowTitle('발주자 마크업 검토')
        self.resize(720, 480)
        self.setModal(False)
        self.iface = iface
        self.work_layer = work_layer
        self.features = list(features or [])

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f'마크업 <b>{len(self.features)}</b>건. '
            '행 더블클릭 = 줌. kind 에 맞는 액션 버튼 선택.'))

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ['id', 'kind', 'status', 'attrs', 'adm_cd'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_double)
        layout.addWidget(self.table, 1)

        btn = QHBoxLayout()
        self.btn_zoom = QPushButton('줌 이동')
        self.btn_zoom.clicked.connect(self._zoom_selected)
        self.btn_apply_attr = QPushButton('폴리곤 적용 (attr)')
        self.btn_apply_attr.clicked.connect(self._apply_attr)
        self.btn_split_guide = QPushButton('split 가이드 (add)')
        self.btn_split_guide.clicked.connect(self._split_guide)
        self.btn_dissolve_guide = QPushButton('병합 가이드 (delete)')
        self.btn_dissolve_guide.clicked.connect(self._dissolve_guide)
        btn.addWidget(self.btn_zoom)
        btn.addWidget(self.btn_apply_attr)
        btn.addWidget(self.btn_split_guide)
        btn.addWidget(self.btn_dissolve_guide)
        btn.addStretch()
        layout.addLayout(btn)
        self._fill_table()

    def _fill_table(self):
        self.table.setRowCount(len(self.features))
        for i, f in enumerate(self.features):
            p = f.get('properties') or {}
            attrs = p.get('attrs') or {}
            if isinstance(attrs, dict):
                attrs_str = ' '.join(f'{k}={v}' for k, v in attrs.items())
            else:
                attrs_str = str(attrs or '')
            for c, v in enumerate([p.get('id', ''), p.get('kind', ''),
                                   p.get('status', ''), attrs_str,
                                   p.get('adm_cd', '')]):
                self.table.setItem(i, c, QTableWidgetItem(str(v)))
        self.table.resizeColumnsToContents()

    def _selected_feature(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.features[rows[0].row()]

    def _on_double(self, idx):
        self.table.selectRow(idx.row())
        self._zoom_selected()

    def _zoom_selected(self):
        f = self._selected_feature()
        if not f:
            return
        self._zoom_to_feature(f)

    def _zoom_to_feature(self, feat):
        from qgis.core import (QgsCoordinateReferenceSystem,
                               QgsCoordinateTransform, QgsProject)
        geom = _feature_geometry(feat)
        if geom is None or geom.isEmpty():
            return
        bbox = geom.boundingBox()
        canvas = self.iface.mapCanvas()
        dst = canvas.mapSettings().destinationCrs()
        src = QgsCoordinateReferenceSystem('EPSG:4326')
        if src.isValid() and dst.isValid() and src != dst:
            tr = QgsCoordinateTransform(src, dst, QgsProject.instance())
            bbox = tr.transformBoundingBox(bbox)
        if bbox.isEmpty():
            return
        bbox.scale(1.5)
        canvas.setExtent(bbox)
        canvas.refresh()

    def _apply_attr(self):
        f = self._selected_feature()
        if not f:
            return
        p = f.get('properties') or {}
        if p.get('kind') != 'attr':
            QMessageBox.warning(self, '경고',
                                'attr kind 마크업에서만 사용')
            return
        attrs = p.get('attrs') or {}
        if not (attrs.get('ri_cd') or attrs.get('ri_nm')):
            QMessageBox.warning(self, '경고',
                                'attrs.ri_cd / ri_nm 비어 있음')
            return
        if self.work_layer is None or not self.work_layer.isValid():
            QMessageBox.warning(self, '경고', '작업데이터 레이어 없음')
            return
        g = f.get('geometry') or {}
        if g.get('type') != 'Point':
            QMessageBox.warning(self, '경고', 'attr geometry 가 Point 아님')
            return
        from qgis.core import (QgsPointXY, QgsGeometry, QgsFeatureRequest,
                               QgsCoordinateReferenceSystem,
                               QgsCoordinateTransform, QgsProject)
        lon, lat = g['coordinates'][:2]
        pt = QgsPointXY(lon, lat)
        src = QgsCoordinateReferenceSystem('EPSG:4326')
        dst = self.work_layer.crs()
        if src.isValid() and dst.isValid() and src != dst:
            tr = QgsCoordinateTransform(src, dst, QgsProject.instance())
            pt = tr.transform(pt)
        pt_geom = QgsGeometry.fromPointXY(pt)
        bbox = pt_geom.boundingBox()
        bbox.grow(1.0)
        match = None
        for ft in self.work_layer.getFeatures(
                QgsFeatureRequest().setFilterRect(bbox)):
            geom = ft.geometry()
            if geom and not geom.isEmpty() and geom.contains(pt_geom):
                match = ft
                break
        if match is None:
            QMessageBox.warning(
                self, '경고',
                '포인트와 intersect 되는 작업데이터 폴리곤 없음.\n'
                '캔버스 줌으로 위치 확인 후 작업데이터에 누락 폴리곤이 '
                '있는지 점검하세요.')
            return
        ri_cd = str(attrs.get('ri_cd', '')).strip()
        ri_nm = str(attrs.get('ri_nm', '')).strip()
        if QMessageBox.question(
                self, '속성 부여 확인',
                f'폴리곤 id={match.id()} 에 RI_CD="{ri_cd}" '
                f'RI_NM="{ri_nm}" 부여하시겠습니까?',
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        if not self.work_layer.isEditable():
            self.work_layer.startEditing()
        fields = self.work_layer.fields()
        idx_of = {fields.at(i).name().lower(): i
                  for i in range(fields.count())}
        if ri_cd and 'ri_cd' in idx_of:
            self.work_layer.changeAttributeValue(
                match.id(), idx_of['ri_cd'], ri_cd)
        if ri_nm and 'ri_nm' in idx_of:
            self.work_layer.changeAttributeValue(
                match.id(), idx_of['ri_nm'], ri_nm)
        self.work_layer.triggerRepaint()
        QMessageBox.information(
            self, '완료',
            '속성 부여 완료. [작업 종료] 시 저장되며, 추후 Phase D 도착 시 '
            '서버 markup 상태가 자동으로 applied 마킹됩니다.')

    def _split_guide(self):
        f = self._selected_feature()
        if not f:
            return
        p = f.get('properties') or {}
        if p.get('kind') != 'add':
            QMessageBox.warning(self, '경고',
                                'add kind 마크업에서만 사용')
            return
        self._zoom_to_feature(f)
        if self.work_layer is not None:
            try:
                self.iface.setActiveLayer(self.work_layer)
                if not self.work_layer.isEditable():
                    self.work_layer.startEditing()
                self.iface.actionSplitFeatures().trigger()
            except Exception:
                pass
        QMessageBox.information(
            self, 'split 가이드',
            '캔버스에 발주자 등록 라인(초록)이 표시됩니다. 그 라인을 따라 '
            '작업데이터 폴리곤을 분할하세요. 분할 후 행정리 부여 다이얼로그가 '
            '자동으로 뜹니다.')

    def _dissolve_guide(self):
        f = self._selected_feature()
        if not f:
            return
        p = f.get('properties') or {}
        if p.get('kind') != 'delete':
            QMessageBox.warning(self, '경고',
                                'delete kind 마크업에서만 사용')
            return
        self._zoom_to_feature(f)
        QMessageBox.information(
            self, '병합 가이드',
            '캔버스에 발주자 삭제 라인(빨강 점선)이 표시됩니다. 그 라인을 '
            '가로지르는 두 작업데이터 폴리곤을 선택 후 QGIS '
            '[Merge Selected Features] 도구로 병합하세요.')


# ============================================================
# Tab: 서버 연결
# ============================================================

class ServerConnectionTab(QWidget):
    """서울 서버 연결 설정 — API URL/토큰 + S3(MinIO) 키.

    설정은 QSettings에 저장. 연결 테스트는 API(read-only)와 S3 각각 확인.
    """

    def __init__(self, parent_dialog):
        super().__init__()
        self.parent_dialog = parent_dialog
        self._build()
        self._load_into_form()

    def _build(self):
        layout = QVBoxLayout(self)

        help_label = QLabel(
            '<i>서울 서버 접속 설정입니다. 대전은 PostGIS/MinIO에 직접 붙지 않고 '
            'HTTPS로만 통신합니다 — 경계 제출/마크업 회수는 API, 이미지 업로드는 S3.'
            '<br>설정은 QGIS 설정(QSettings)에 저장되어 다음 실행 시 복원됩니다.</i>')
        help_label.setWordWrap(True)
        help_label.setStyleSheet(
            'QLabel { padding: 6px; background: #f0f0f0; border-radius: 3px; }')
        layout.addWidget(help_label)

        # API
        api_box = QGroupBox('API (경계 제출 / 마크업 회수 / COG 등록)')
        api_form = QFormLayout(api_box)
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText('https://<funnel-host>')
        self.api_token = QLineEdit()
        self.api_token.setEchoMode(QLineEdit.Password)
        self.api_token.setPlaceholderText('서버에서 발급한 플러그인 API 토큰')
        api_form.addRow('서버 URL:', self.base_url)
        api_form.addRow('API 토큰:', self.api_token)
        layout.addWidget(api_box)

        # S3
        s3_box = QGroupBox('S3 (MinIO — COG / 원본 이미지 업로드)')
        s3_form = QFormLayout(s3_box)
        self.s3_access = QLineEdit()
        self.s3_access.setPlaceholderText('MinIO write 액세스키')
        self.s3_secret = QLineEdit()
        self.s3_secret.setEchoMode(QLineEdit.Password)
        self.bucket = QLineEdit()
        s3_form.addRow('Access Key:', self.s3_access)
        s3_form.addRow('Secret Key:', self.s3_secret)
        s3_form.addRow('Bucket:', self.bucket)
        layout.addWidget(s3_box)

        # 버튼
        btn_row = QHBoxLayout()
        self.btn_test_api = QPushButton('API 테스트')
        self.btn_test_api.clicked.connect(self._on_test_api)
        self.btn_test_s3 = QPushButton('S3 테스트')
        self.btn_test_s3.clicked.connect(self._on_test_s3)
        self.btn_save = QPushButton('설정 저장')
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_test_api)
        btn_row.addWidget(self.btn_test_s3)
        btn_row.addWidget(self.btn_save)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 로그
        self.log = QTextEdit(); self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        self.log.setStyleSheet('QTextEdit { font-family: monospace; }')
        layout.addWidget(QLabel('상태:'))
        layout.addWidget(self.log)
        layout.addStretch()

    def current_config(self):
        return ServerConfig(
            base_url=self.base_url.text().strip(),
            api_token=self.api_token.text().strip(),
            s3_access_key=self.s3_access.text().strip(),
            s3_secret_key=self.s3_secret.text().strip(),
            bucket=self.bucket.text().strip() or api_client.DEFAULT_BUCKET,
        )

    def _load_into_form(self):
        cfg = load_config()
        self.base_url.setText(cfg.base_url)
        self.api_token.setText(cfg.api_token)
        self.s3_access.setText(cfg.s3_access_key)
        self.s3_secret.setText(cfg.s3_secret_key)
        self.bucket.setText(cfg.bucket)
        self.parent_dialog.server_config = cfg

    def _on_save(self):
        cfg = self.current_config()
        save_config(cfg)
        self.parent_dialog.server_config = cfg
        self.log.append('[저장] 서버 설정 저장됨')

    def _on_test_api(self):
        cfg = self.current_config()
        self.log.append(f'[API 테스트] {cfg.api_base}')
        QApplication.processEvents()
        ok, msg = api_client.test_connection(cfg)
        self.log.append(f'  {"✅" if ok else "❌"} {msg}')
        if ok:
            self.parent_dialog.server_config = cfg

    def _on_test_s3(self):
        cfg = self.current_config()
        self.log.append(f'[S3 테스트] {cfg.s3_endpoint}')
        QApplication.processEvents()
        ok, msg = api_client.check_s3(cfg)
        self.log.append(f'  {"✅" if ok else "❌"} {msg}')


# ============================================================
# Tab: 행정리 작업
# ============================================================

class WorkListTab(QWidget):
    """행정리 작업 — 작업 폴더 자동인식 → 13레이어 구성 → 편집 → 제출/마크업.

    화면정의서 슬11~12 흐름:
    1. 작업 폴더 지정 → 하위 폴더 규칙(01_~13_)으로 슬롯 자동 인식
    2. [화면 구성] — 13레이어를 on/off 기본값대로 QGIS 로드 + 명부 로드
    3. [작업 시작] — 작업데이터 레이어만 편집 활성, 나머지 잠금
    4. 명부에서 행정리 선택 → split/추가 시 RI 속성 자동 부여
    5. [마크업 받기] — 발주자 수정요청 회수 / [제출] — 작업데이터 → 서버
    """

    def __init__(self, parent_dialog):
        super().__init__()
        self.parent_dialog = parent_dialog
        self.iface = parent_dialog.iface
        self._slots = {}            # slot_key → path
        self._merged_codes = set()  # 병합이미지 8자리 admin code 집합
        self._roster = []           # 명부 행 dict (필터 적용 후)
        self._work_layer = None     # 작업데이터 레이어
        self._work_snapshot = None
        self._current_admin = ''
        self._current_admin_nm = ''
        self._feature_added_slot = None  # split 후 팝업용 콜백 참조
        self._markup_dialog = None       # 마크업 검토 다이얼로그 ref
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        help_label = QLabel(
            '<i><b>작업 폴더</b>를 지정하면 하위 폴더(01_~13_)에서 13개 레이어를 '
            '자동 인식합니다. <b>[화면 구성]</b>으로 QGIS에 로드 → <b>[작업 시작]</b> → '
            '명부에서 행정리 선택 후 작업데이터 폴리곤을 split → '
            '<b>[제출]</b>로 서버 업로드.</i>')
        help_label.setWordWrap(True)
        help_label.setStyleSheet(
            'QLabel { padding: 6px; background: #f0f0f0; border-radius: 3px; }')
        layout.addWidget(help_label)

        # --- 데이터 선택 ---
        sel_box = QGroupBox('데이터 선택')
        sel_layout = QVBoxLayout(sel_box)
        frow = QHBoxLayout()
        frow.addWidget(QLabel('작업 폴더:'))
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText(
            '시군구 작업 폴더 — 하위 01_~13_ 폴더 자동 인식')
        btn_folder = QPushButton('찾기')
        btn_folder.clicked.connect(self._browse_folder)
        frow.addWidget(self.folder_edit, 1)
        frow.addWidget(btn_folder)
        sel_layout.addLayout(frow)

        self.slot_table = QTableWidget(0, 3)
        self.slot_table.setHorizontalHeaderLabels(['슬롯', '파일', '상태'])
        self.slot_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.slot_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.slot_table.setMinimumHeight(180)
        self.slot_table.doubleClicked.connect(self._on_slot_double_click)
        sel_layout.addWidget(self.slot_table)

        slot_hint = QLabel(
            '<i>잘못 인식된 슬롯은 행을 더블클릭해 파일을 직접 지정하세요.</i>')
        slot_hint.setStyleSheet('QLabel { color: #777; }')
        sel_layout.addWidget(slot_hint)

        self.btn_load_ws = QPushButton('화면 구성 (레이어 로드)')
        self.btn_load_ws.clicked.connect(self._on_load_workspace)
        sel_layout.addWidget(self.btn_load_ws)
        layout.addWidget(sel_box)

        # --- 작업 제어 ---
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton('작업 시작 (작업데이터 편집 활성, 기타 잠금)')
        self.btn_start.clicked.connect(self._on_start)
        self.btn_end = QPushButton('작업 종료 (저장 + 잠금 해제)')
        self.btn_end.clicked.connect(self._on_end)
        self.btn_end.setEnabled(False)
        self.markup_status_cb = QComboBox()
        self.markup_status_cb.addItems(['pending', 'applied', 'rejected', 'all'])
        self.markup_status_cb.setToolTip('회수 대상 마크업 상태')
        self.btn_markup = QPushButton('마크업 받기')
        self.btn_markup.clicked.connect(self._on_get_markup)
        self.btn_submit = QPushButton('제출')
        self.btn_submit.clicked.connect(self._on_submit)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_end)
        btn_row.addStretch()
        btn_row.addWidget(QLabel('마크업:'))
        btn_row.addWidget(self.markup_status_cb)
        btn_row.addWidget(self.btn_markup)
        btn_row.addWidget(self.btn_submit)
        layout.addLayout(btn_row)

        # --- 검색 + 명부 리스트 ---
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel('검색:'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('읍면동 코드/명칭, 행정리 코드/명칭')
        self.search.textChanged.connect(self._on_search)
        search_row.addWidget(self.search)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ['읍면동 코드', '읍면동명', '행정리 코드', '행정리명',
             '작업여부', '현재면적(㎡)', '비고'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSortingEnabled(True)
        # 헤더 클릭 정렬. work_yn 셀은 setItem + setCellWidget 양쪽 모두 세팅해
        # 정렬 키를 유지 (콤보만 두면 빈 셀로 취급됨).
        self.table.currentCellChanged.connect(
            lambda r, *_: self._on_row_selected(r))
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setMinimumHeight(220)
        layout.addWidget(self.table, 1)

        self.status = QLabel('작업 폴더 미지정')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # --- 설정 ---

    def _get_config(self):
        cfg = self.parent_dialog.server_config
        if cfg is None:
            cfg = self.parent_dialog.tabs.widget(0).current_config()
        return cfg

    # --- 데이터 선택 ---

    def _browse_folder(self):
        d = QFileDialog.getExistingDirectory(
            self, '작업 폴더 선택', self.folder_edit.text())
        if d:
            self.folder_edit.setText(d)
            self._detect()

    def _detect(self):
        root = self.folder_edit.text().strip()
        self._slots = layer_control.detect_work_folder(root)
        self._merged_codes = layer_control.detect_merged_admin_codes(
            self._slots)
        self._fill_slot_table()

    def _fill_slot_table(self):
        slots_def = layer_control.WORK_SLOTS
        self.slot_table.setRowCount(len(slots_def))
        for i, (num, key, label, kind, _on, required) in enumerate(slots_def):
            path = self._slots.get(key)
            self.slot_table.setItem(i, 0, QTableWidgetItem(f'{num} {label}'))
            disp = os.path.basename(path.rstrip('/\\')) if path else ''
            self.slot_table.setItem(i, 1, QTableWidgetItem(disp))
            if path:
                st = '✓ 필수' if required else '✓'
            else:
                st = '✗ 없음 (필수)' if required else '- 선택'
            self.slot_table.setItem(i, 2, QTableWidgetItem(st))
        self.slot_table.resizeColumnsToContents()
        missing = [label for _n, key, label, _k, _o, req
                   in slots_def if req and not self._slots.get(key)]
        if missing:
            self.status.setText(f'필수 데이터 누락: {", ".join(missing)}')
        else:
            self.status.setText('데이터 인식 완료 — [화면 구성] 진행 가능')

    def _on_slot_double_click(self, index):
        row = index.row()
        slots_def = layer_control.WORK_SLOTS
        if row < 0 or row >= len(slots_def):
            return
        _num, key, label, kind, _on, _req = slots_def[row]
        if kind == 'shp':
            p, _ = QFileDialog.getOpenFileName(
                self, f'{label} — SHP 선택', '', 'SHP (*.shp)')
        elif kind == 'xlsx':
            p, _ = QFileDialog.getOpenFileName(
                self, f'{label} — 엑셀 선택', '', '엑셀 (*.xlsx *.xlsm)')
        elif kind == 'raster_dir':
            p = QFileDialog.getExistingDirectory(
                self, f'{label} — 폴더 선택', '')
        else:
            p = ''
        if p:
            self._slots[key] = p
            if key == 'merged_img':
                self._merged_codes = (
                    layer_control.detect_merged_admin_codes(self._slots))
            self._fill_slot_table()

    def _on_load_workspace(self):
        slots_def = layer_control.WORK_SLOTS
        missing = [label for _n, key, label, _k, _o, req
                   in slots_def if req and not self._slots.get(key)]
        if missing:
            QMessageBox.warning(
                self, '경고', f'필수 데이터 누락: {", ".join(missing)}')
            return
        try:
            work_layer, summary = layer_control.load_workspace(
                self._slots, iface=self.iface)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'레이어 로드 실패: {e}')
            return
        self._work_layer = work_layer
        roster_path = self._slots.get('roster')
        if roster_path:
            self._load_roster(roster_path)
        ok = ' / '.join(f'{lbl}:{st}' for lbl, st in summary)
        self.status.setText(f'화면 구성 완료 — {ok}')

    # --- 명부 로드 ---

    def _load_roster(self, path):
        if not self._merged_codes:
            QMessageBox.warning(
                self, '경고',
                '병합이미지가 없어 작업 대상 읍면동을 특정할 수 없습니다.\n'
                '11_병합이미지 폴더의 jpg 파일을 먼저 확인하세요.')
            return
        try:
            _headers, rows, _mapping, missing = excel_loader.read_excel(
                path, adm_codes=self._merged_codes)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'명부 엑셀 읽기 실패: {e}')
            return
        if missing:
            QMessageBox.warning(
                self, '경고',
                f'명부 필수 컬럼 누락: {", ".join(missing)}')
            return
        total = len(rows)
        self._roster = rows
        # 채우는 동안 정렬 꺼두기 — row 순서 바뀌면 cellWidget 매핑 깨짐
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(r.get('adm_cd', '')))
            self.table.setItem(i, 1, QTableWidgetItem(r.get('adm_nm', '')))
            self.table.setItem(i, 2, QTableWidgetItem(r.get('ri_cd', '')))
            self.table.setItem(i, 3, QTableWidgetItem(r.get('ri_nm', '')))
            # 4: 작업여부 — sort 용 item + 표시용 콤보
            yn = (r.get('work_yn', '') or '').strip().upper()
            yn_item = QTableWidgetItem(yn)
            yn_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 4, yn_item)
            self.table.setCellWidget(i, 4, self._make_work_yn_combo(i, yn))
            # 5: 현재면적 — 작업데이터 layer 에서 계산해 채움 (초기 0)
            self.table.setItem(i, 5, self._make_area_item(0))
            self.table.setItem(i, 6, QTableWidgetItem(r.get('remark', '')))
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)
        done = sum(1 for r in rows
                   if (r.get('work_yn', '') or '').upper() == 'Y')
        tag = f' (병합이미지 {len(self._merged_codes)}개 읍면동)'
        self.status.setText(
            f'명부 로드: {len(rows)}개 행정리 (작업완료 {done}){tag}')
        self._refresh_area_column()
        self._hook_work_layer_changes()

    # --- 현재면적 컬럼 갱신 ---

    def _compute_area_sums(self):
        """work_layer 의 (adm_cd, ri_cd) 별 면적 합 dict."""
        sums = {}
        lyr = self._work_layer
        if lyr is None or not lyr.isValid():
            return sums
        fields = lyr.fields()
        idx_adm = next((i for i in range(fields.count())
                        if fields.at(i).name().lower() == 'adm_cd'), -1)
        idx_ri = next((i for i in range(fields.count())
                       if fields.at(i).name().lower() == 'ri_cd'), -1)
        if idx_adm < 0 or idx_ri < 0:
            return sums
        for f in lyr.getFeatures():
            if not f.hasGeometry():
                continue
            adm = str(f.attribute(idx_adm) or '').strip()
            ri = str(f.attribute(idx_ri) or '').strip()
            if not adm or not ri:
                continue
            sums[(adm, ri)] = sums.get((adm, ri), 0.0) + f.geometry().area()
        return sums

    def _refresh_area_column(self):
        sums = self._compute_area_sums()
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        for r in range(self.table.rowCount()):
            it_adm = self.table.item(r, 0)
            it_ri = self.table.item(r, 2)
            if not (it_adm and it_ri):
                continue
            a = sums.get((it_adm.text(), it_ri.text()), 0.0)
            cell = self.table.item(r, 5)
            if cell is None:
                self.table.setItem(r, 5, self._make_area_item(int(round(a))))
            else:
                cell.setData(Qt.DisplayRole, int(round(a)))
        self.table.setSortingEnabled(was_sorting)

    def _hook_work_layer_changes(self):
        """작업데이터 편집 저장 시 면적 컬럼 자동 갱신."""
        lyr = self._work_layer
        if lyr is None or not lyr.isValid():
            return
        if getattr(self, '_area_hook_layer_id', None) == lyr.id():
            return
        try:
            lyr.editingStopped.connect(self._refresh_area_column)
        except Exception:
            pass
        self._area_hook_layer_id = lyr.id()

    def _on_search(self, text):
        text = text.strip().lower()
        cols = self.table.columnCount()
        for r in range(self.table.rowCount()):
            if not text:
                self.table.setRowHidden(r, False)
                continue
            parts = []
            for c in range(cols):
                w = self.table.cellWidget(r, c)
                if isinstance(w, QComboBox):
                    parts.append(w.currentText().lower())
                else:
                    it = self.table.item(r, c)
                    parts.append(it.text().lower() if it else '')
            self.table.setRowHidden(r, text not in ' '.join(parts))

    # --- 작업여부 인라인 편집 ---

    def _make_work_yn_combo(self, row_idx, current):
        cb = QComboBox()
        cb.addItems(['', 'Y', 'N'])
        cur = (current or '').strip().upper()
        cb.setCurrentText(cur if cur in ('Y', 'N') else '')
        cb.currentTextChanged.connect(
            lambda v, r=row_idx: self._on_work_yn_changed(r, v))
        return cb

    def _on_work_yn_changed(self, row_idx, value):
        if row_idx < 0 or row_idx >= len(self._roster):
            return
        rec = self._roster[row_idx]
        old = (rec.get('work_yn', '') or '').strip().upper()
        new = (value or '').strip().upper()
        if old == new:
            return
        rec['work_yn'] = new   # 메모리 즉시 반영, 엑셀은 백그라운드
        # 정렬키 동기화 — 콤보가 바뀐 row 의 item 도 갱신
        cur_row = self._find_row(rec.get('adm_cd', ''), rec.get('ri_cd', ''))
        if cur_row >= 0:
            it = self.table.item(cur_row, 4)
            if it is not None:
                it.setText(new)
        path = self._slots.get('roster')
        if not path:
            self.status.setText('명부 경로 없음 — 저장 실패')
            return
        self.status.setText(
            f"저장 중 — {rec.get('ri_cd','')} 작업여부={new} (백그라운드)")
        w = WorkYnSaveWorker(
            row_idx, path, rec.get('adm_cd', ''), rec.get('ri_cd', ''),
            new, old)
        w.done.connect(self._on_work_yn_saved)
        w.failed.connect(self._on_work_yn_save_failed)
        # 인스턴스 보존 — GC 로 워커 즉시 사라지면 시그널이 끊김
        if not hasattr(self, '_work_yn_workers'):
            self._work_yn_workers = []
        self._work_yn_workers.append(w)
        w.finished.connect(lambda w=w: self._work_yn_workers.remove(w))
        w.start()

    def _on_work_yn_saved(self, row_idx, adm_cd, ri_cd, n):
        if n == 0:
            self.status.setText(
                f"저장 경고 — ri_cd={ri_cd} 매칭 행 없음")
        else:
            # 현재 rec 값을 status 에 그대로 반영
            rec = self._find_rec(adm_cd, ri_cd) or {}
            self.status.setText(
                f"저장 완료 — {ri_cd} 작업여부={rec.get('work_yn','')}")

    def _on_work_yn_save_failed(self, row_idx, adm_cd, ri_cd, old, err):
        if 'Permission' in err or 'PermissionError' in err:
            QMessageBox.critical(
                self, '저장 실패',
                '엑셀 파일이 다른 프로그램(엑셀 등)에 열려 있습니다.\n'
                '닫고 다시 시도하세요.')
        else:
            QMessageBox.critical(self, '저장 실패', err)
        rec = self._find_rec(adm_cd, ri_cd)
        if rec is not None:
            rec['work_yn'] = old
        # 정렬되어 row_idx 가 이동했을 수 있으니 (adm_cd, ri_cd) 로 찾음
        target = self._find_row(adm_cd, ri_cd)
        if target >= 0:
            self._revert_work_yn(target, old)

    def _find_rec(self, adm_cd, ri_cd):
        for r in self._roster:
            if r.get('adm_cd', '') == adm_cd and r.get('ri_cd', '') == ri_cd:
                return r
        return None

    def _find_row(self, adm_cd, ri_cd):
        """현재 정렬된 화면 row index 를 (adm_cd, ri_cd) 로 검색."""
        for r in range(self.table.rowCount()):
            it_adm = self.table.item(r, 0)
            it_ri = self.table.item(r, 2)
            if (it_adm and it_ri
                    and it_adm.text() == adm_cd
                    and it_ri.text() == ri_cd):
                return r
        return -1

    def _revert_work_yn(self, row_idx, value):
        w = self.table.cellWidget(row_idx, 4)
        if isinstance(w, QComboBox):
            w.blockSignals(True)
            w.setCurrentText(value)
            w.blockSignals(False)
        it = self.table.item(row_idx, 4)
        if it is not None:
            it.setText(value)

    def _make_area_item(self, area_m2):
        """현재면적 셀 — 정렬용 정수값 보유."""
        it = QTableWidgetItem()
        it.setData(Qt.DisplayRole, int(area_m2))
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return it

    # --- 행정리 선택 → 자동부여 준비 ---

    def _on_row_selected(self, row):
        if row < 0 or row >= len(self._roster):
            return
        r = self._roster[row]
        self._current_admin = r.get('adm_cd', '')
        self._current_admin_nm = r.get('adm_nm', '')
        layer_control.set_current_ri(
            adm_cd=r.get('adm_cd', ''), adm_nm=r.get('adm_nm', ''),
            ri_cd=r.get('ri_cd', ''), ri_nm=r.get('ri_nm', ''))
        self.status.setText(
            f"활성 행정리: {r.get('ri_cd','')} {r.get('ri_nm','')} "
            f"({r.get('adm_cd','')} {r.get('adm_nm','')}) — "
            f"split/추가 시 자동 부여")

    def _on_double_click(self, index):
        """더블클릭 — 선택 + 해당 읍면동으로 맵 줌 (행정경계 레이어 기준)."""
        row = index.row()
        if row < 0 or row >= len(self._roster):
            return
        self._on_row_selected(row)
        self._zoom_to_admin(self._current_admin)

    def _zoom_to_admin(self, adm_cd):
        from qgis.core import QgsProject, QgsVectorLayer
        if not adm_cd:
            return
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            if '행정경계' not in lyr.name():
                continue
            fields = lyr.fields()
            idx = next((i for i in range(fields.count())
                        if fields.at(i).name().lower() == 'adm_cd'), -1)
            if idx < 0:
                return
            for f in lyr.getFeatures():
                if str(f.attribute(idx)) == adm_cd and f.hasGeometry():
                    self.iface.mapCanvas().setExtent(
                        f.geometry().boundingBox())
                    self.iface.mapCanvas().refresh()
                    self.status.setText(f'맵 이동: {adm_cd}')
                    return
            return

    # --- 작업 시작/종료 ---

    def _find_adm_bnd_layer(self):
        from qgis.core import QgsProject, QgsVectorLayer
        for lyr in QgsProject.instance().mapLayers().values():
            if (isinstance(lyr, QgsVectorLayer)
                    and '행정경계' in lyr.name()):
                return lyr
        return None

    def _on_start(self):
        try:
            work = self._find_work_layer()
            # 1) 작업데이터에 adm_cd subset 적용 — 병합이미지 대상 읍면동만
            #    노출 (다른 읍면동 데모/잔여 피처 차단)
            if work is not None and self._merged_codes:
                layer_control.restrict_work_layer_to_codes(
                    work, self._merged_codes)
            # 2) subset 적용 후 0 피처면 행정경계에서 폴리곤 시드 복사
            seeded = 0
            if work is not None and work.featureCount() == 0:
                adm = self._find_adm_bnd_layer()
                if adm is None:
                    QMessageBox.warning(
                        self, '경고', '행정경계 레이어가 없어 초기 시드 불가.')
                else:
                    if not work.isEditable():
                        work.startEditing()
                    seeded = layer_control.seed_work_layer_from_adm(
                        work, adm, self._merged_codes)
                    work.commitChanges()
            # 3) attach=False — 기존 current_ri 기반 autofill 끄고
            #    아래에서 split 후 팝업 슬롯을 직접 연결
            self._work_snapshot = layer_control.start_work_mode(
                self.iface, attach=False)
            work = self._find_work_layer()
            if work is not None:
                # 4) split→팝업 콜백 연결 (qgz 스타일은 유지)
                self._connect_feature_added(work)
            self.btn_start.setEnabled(False)
            self.btn_end.setEnabled(True)
            extra = f' (시드 {seeded}개)' if seeded else ''
            self.status.setText(
                f'작업 시작 — split 시 팝업으로 행정리 부여{extra}')
        except Exception as e:
            QMessageBox.critical(self, '오류', f'작업 시작 실패: {e}')

    def _on_end(self):
        try:
            self._disconnect_feature_added()
            saved, errors = layer_control.end_work_mode(
                self.iface, self._work_snapshot)
            self._work_snapshot = None
            # subset 해제 — 작업 종료 후 전체 보기로 복귀
            work = self._find_work_layer()
            if work is not None:
                layer_control.restrict_work_layer_to_codes(work, None)
            self.btn_start.setEnabled(True)
            self.btn_end.setEnabled(False)
            msg = f'작업 종료 — {saved}개 레이어 저장'
            if errors:
                msg += f' | 오류 {len(errors)}건: {"; ".join(errors)}'
            self.status.setText(msg)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'작업 종료 실패: {e}')

    # --- split → 팝업 자동 부여 ---

    def _connect_feature_added(self, layer):
        self._disconnect_feature_added()
        slot = lambda fid, _l=layer: self._on_feature_added(_l, fid)
        layer.featureAdded.connect(slot)
        self._feature_added_slot = (layer, slot)

    def _disconnect_feature_added(self):
        if not self._feature_added_slot:
            return
        layer, slot = self._feature_added_slot
        try:
            layer.featureAdded.disconnect(slot)
        except (TypeError, RuntimeError):
            pass
        self._feature_added_slot = None

    def _on_feature_added(self, layer, fid):
        # 미완료(N/공백) 후보만 표시
        candidates = [r for r in self._roster
                      if (r.get('work_yn', '') or '').upper() != 'Y']
        if not candidates:
            self.status.setText('split 됨 — 미완료 행정리 후보 없음')
            return
        dlg = RiAssignDialog(candidates, parent=self)
        if dlg.exec_() != QDialog.Accepted or dlg.chosen is None:
            self.status.setText('split 됨 — 부여 취소')
            return
        chosen = dlg.chosen
        self._assign_attrs_to_feature(layer, fid, chosen)
        self._mark_row_done(chosen)

    def _assign_attrs_to_feature(self, layer, fid, rec):
        fields = layer.fields()
        idx_of = {fields.at(i).name().lower(): i
                  for i in range(fields.count())}
        for col, val in (('adm_cd', rec.get('adm_cd', '')),
                         ('adm_nm', rec.get('adm_nm', '')),
                         ('ri_cd', rec.get('ri_cd', '')),
                         ('ri_nm', rec.get('ri_nm', ''))):
            idx = idx_of.get(col, -1)
            if idx >= 0 and val:
                layer.changeAttributeValue(fid, idx, val)
        layer.triggerRepaint()

    def _mark_row_done(self, rec):
        # 명부 메모리 + 엑셀 + 테이블 콤보 동기 갱신
        rec['work_yn'] = 'Y'
        path = self._slots.get('roster')
        if path:
            try:
                excel_loader.update_work_yn(
                    path, rec.get('adm_cd', ''), rec.get('ri_cd', ''), 'Y')
            except Exception as e:
                self.status.setText(f'엑셀 저장 실패: {e}')
        # 테이블 콤보 갱신 — roster 내 같은 (adm_cd, ri_cd) 찾기
        for i, r in enumerate(self._roster):
            if (r.get('adm_cd', '') == rec.get('adm_cd', '')
                    and r.get('ri_cd', '') == rec.get('ri_cd', '')):
                w = self.table.cellWidget(i, 4)
                if isinstance(w, QComboBox):
                    w.blockSignals(True)
                    w.setCurrentText('Y')
                    w.blockSignals(False)
                break
        self.status.setText(
            f"부여 완료 — {rec.get('ri_cd','')} {rec.get('ri_nm','')} → Y")

    # --- 마크업 회수 ---

    def _on_get_markup(self):
        cfg = self._get_config()
        adm = self._current_admin or None
        status = self.markup_status_cb.currentText()
        self.status.setText(
            f'마크업 회수 중... ({adm or "전체"}, status={status})')
        QApplication.processEvents()
        try:
            geojson = api_client.get_markup(cfg, adm, status=status)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'마크업 회수 실패: {e}')
            self.status.setText(f'마크업 회수 실패: {e}')
            return
        last_layer, counts = layer_control.load_markup_layer(geojson)
        total = counts.get('total', 0)
        if total == 0:
            self.status.setText(
                f'마크업 0건 ({adm or "전체"}, status={status})')
        else:
            self.status.setText(
                f'마크업 {total}건 회수 — 등록 {counts.get("add",0)} / '
                f'삭제 {counts.get("delete",0)} / '
                f'속성 {counts.get("attr",0)} (status={status})')
            # 검토 다이얼로그 (modeless) — 이전 인스턴스 닫고 새로 띄움
            if self._markup_dialog is not None:
                try:
                    self._markup_dialog.close()
                except Exception:
                    pass
            self._markup_dialog = MarkupReviewDialog(
                self.iface, self._find_work_layer(),
                (geojson or {}).get('features') or [],
                parent=self)
            self._markup_dialog.show()
            self._markup_dialog.raise_()

    # --- 제출 ---

    def _find_work_layer(self):
        from qgis.core import QgsProject, QgsVectorLayer
        if self._work_layer is not None:
            try:
                if self._work_layer.isValid():
                    return self._work_layer
            except RuntimeError:
                self._work_layer = None
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and any(
                    t in lyr.name().lower()
                    for t in layer_control.EDIT_LAYER_NAMES):
                self._work_layer = lyr
                return lyr
        return None

    def _on_submit(self):
        layer = self._find_work_layer()
        if layer is None:
            QMessageBox.warning(
                self, '경고',
                '작업데이터 레이어가 없습니다. [화면 구성] 먼저 진행하세요.')
            return
        # 편집 중이면 먼저 저장 (provider 반영 후 추출)
        if layer.isEditable():
            if not layer.commitChanges():
                QMessageBox.critical(self, '오류', '편집 내역 저장 실패 — 제출 중단')
                return
            layer.startEditing()
        try:
            geojson = layer_control.boundary_to_geojson(layer)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'GeoJSON 추출 실패: {e}')
            return
        n = len(geojson.get('features', []))
        if n == 0:
            QMessageBox.warning(self, '경고', '제출할 경계(geom)가 없습니다.')
            return
        if QMessageBox.question(
                self, '제출 확인',
                f'경계 {n}건을 서버에 제출합니다. 계속할까요?',
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        cfg = self._get_config()
        updated_by = (os.environ.get('USERNAME')
                      or os.environ.get('USER') or '')
        self.status.setText(f'제출 중... ({n}건)')
        QApplication.processEvents()
        try:
            affected, msg = api_client.submit_boundary(
                cfg, geojson, updated_by=updated_by)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'제출 실패: {e}')
            self.status.setText(f'제출 실패: {e}')
            return
        self.status.setText(f'✅ 제출 완료 — {msg}')
        QMessageBox.information(self, '제출 완료', msg)


# ============================================================
# 메인 다이얼로그
# ============================================================

# ============================================================
# 완료 데이터 업로드 — 폴더 직접 물림 (경계 SHP + merge 이미지 COG)
# ============================================================

def scan_completed_folder(root):
    """01_data 구조 폴더에서 업로드 대상 수집.

    구조:
        02_행정리경계/{시도}/{시도}_bnd_job_pg.shp   ← 경계 (시도당 1 SHP)
        03_스캔이미지/{시도}/{시군구}/{adm}_scan_merged.jpg ← merge 이미지

    Returns dict:
        boundary_shps: [shp 경로, …]
        images:        [jpg 경로, …]   (jgw 동반된 것만)
        img_root:      03_스캔이미지 절대경로 (COG rel 경로 기준점)
    """
    import glob
    out = {'boundary_shps': [], 'images': [], 'img_root': None}
    bnd_dir = os.path.join(root, '02_행정리경계')
    if os.path.isdir(bnd_dir):
        out['boundary_shps'] = sorted(glob.glob(
            os.path.join(bnd_dir, '**', '*_bnd_job_pg.shp'), recursive=True))
    img_dir = os.path.join(root, '03_스캔이미지')
    if os.path.isdir(img_dir):
        out['img_root'] = img_dir
        imgs = sorted(glob.glob(
            os.path.join(img_dir, '**', '*_scan_merged.jpg'), recursive=True))
        out['images'] = [p for p in imgs
                         if os.path.exists(os.path.splitext(p)[0] + '.jgw')]
    return out


def _shp_adm_codes(shp_path):
    """SHP 의 adm_cd 값 집합 (대소문자 무시 컬럼 검색)."""
    from qgis.core import QgsVectorLayer
    lyr = QgsVectorLayer(shp_path, 'tmp', 'ogr')
    if not lyr.isValid():
        return set()
    fields = lyr.fields()
    idx = next((i for i in range(fields.count())
                if fields.at(i).name().lower() == 'adm_cd'), -1)
    if idx < 0:
        return set()
    return {str(f.attribute(idx)).strip() for f in lyr.getFeatures()
            if f.attribute(idx) is not None}


def _img_adm_code(jpg_path):
    """이미지 파일명에서 8자리 adm_cd 추출."""
    import re
    m = re.match(r'(\d{8})', os.path.basename(jpg_path))
    return m.group(1) if m else ''


class CompletedUploadWorker(QThread):
    """경계 SHP 제출 + merge 이미지 COG 업로드를 백그라운드에서 순차 실행."""
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, cfg, boundary_tasks, image_paths, img_root,
                 do_boundary, do_cog, scale):
        super().__init__()
        self.cfg = cfg
        self.boundary_tasks = boundary_tasks   # [(label, geojson, n), …]
        self.image_paths = image_paths
        self.img_root = img_root
        self.do_boundary = do_boundary
        self.do_cog = do_cog
        self.scale = scale

    def run(self):
        import csv
        import tempfile
        try:
            from .tools import stage6_publish
        except ImportError:
            from gis_scan_tools.tools import stage6_publish

        rows = []
        ok = err = 0

        if self.do_boundary:
            for label, geojson, n in self.boundary_tasks:
                self.progress.emit(f'[경계] {label} — {n}건 제출 중…')
                try:
                    affected, msg = api_client.submit_boundary(
                        self.cfg, geojson)
                    ok += 1
                    self.progress.emit(f'  ✅ {msg}')
                    rows.append(['boundary', label, 'OK', msg])
                except Exception as e:
                    err += 1
                    self.progress.emit(f'  ❌ {e}')
                    rows.append(['boundary', label, 'ERROR', str(e)])

        if self.do_cog and self.image_paths:
            tmp_out = tempfile.mkdtemp(prefix='gst_cog_')
            self.progress.emit(f'[COG] 임시 출력 폴더: {tmp_out}')
            for i, jpg in enumerate(self.image_paths, 1):
                name = os.path.basename(jpg)
                self.progress.emit(
                    f'[COG {i}/{len(self.image_paths)}] {name} 변환·업로드 중…')
                try:
                    r = stage6_publish.publish_one(
                        jpg, self.img_root, tmp_out, self.cfg,
                        scale=self.scale)
                    if r.get('status') == 'OK':
                        ok += 1
                        self.progress.emit(f'  ✅ {r.get("message", "")}')
                        rows.append(['cog', name, 'OK', r.get('message', '')])
                    else:
                        err += 1
                        self.progress.emit(f'  ❌ {r.get("message", "")}')
                        rows.append(['cog', name, 'ERROR',
                                     r.get('message', '')])
                except Exception as e:
                    err += 1
                    self.progress.emit(f'  ❌ {e}')
                    rows.append(['cog', name, 'ERROR', str(e)])

        # 상태 CSV — img_root 가 있으면 그 상위(완료 데이터 루트)에, 없으면 임시폴더
        try:
            base = (os.path.dirname(self.img_root) if self.img_root
                    else tempfile.gettempdir())
            csv_path = os.path.join(base, '_upload_status.csv')
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                wr = csv.writer(f)
                wr.writerow(['kind', 'target', 'status', 'message'])
                wr.writerows(rows)
            self.progress.emit(f'[상태] {csv_path}')
        except Exception:
            pass

        self.finished_ok.emit(f'완료 — 성공 {ok}, 실패 {err}')


class CompletedUploadTab(QWidget):
    """작업 완료 데이터 업로드 — 폴더(01_data 구조) 물림 → 경계 + COG 일괄.

    1. 폴더 선택 → [인식] 으로 경계 SHP / merge 이미지 수집 + adm_cd 매칭표
    2. [경계 제출] [COG 업로드] 체크 (둘 다 기본 ON)
    3. [업로드] — 백그라운드 순차 실행, 로그 표시
    서버 설정은 [1. 서버 연결] 탭 값을 그대로 사용.
    """

    def __init__(self, parent_dialog):
        super().__init__()
        self.parent_dialog = parent_dialog
        self._scan = {'boundary_shps': [], 'images': [], 'img_root': None}
        self._worker = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        help_label = QLabel(
            '<i>작업 완료 데이터 폴더를 물려 일괄 업로드합니다. 폴더 구조:'
            '<br><code>02_행정리경계/{시도}/{시도}_bnd_job_pg.shp</code> (경계)'
            '<br><code>03_스캔이미지/{시도}/{시군구}/{adm}_scan_merged.jpg</code> (이미지)'
            '<br>서버 설정은 [1. 서버 연결] 탭 값을 사용합니다.</i>')
        help_label.setWordWrap(True)
        help_label.setStyleSheet(
            'QLabel { padding: 6px; background: #f0f0f0; border-radius: 3px; }')
        layout.addWidget(help_label)

        # 폴더 선택
        row = QHBoxLayout()
        row.addWidget(QLabel('완료 데이터 폴더:'))
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText('01_data 루트 폴더')
        row.addWidget(self.folder_edit, 1)
        btn_browse = QPushButton('찾아보기')
        btn_browse.clicked.connect(self._browse)
        row.addWidget(btn_browse)
        btn_detect = QPushButton('인식')
        btn_detect.clicked.connect(self._detect)
        row.addWidget(btn_detect)
        layout.addLayout(row)

        # 옵션 체크박스
        opt_row = QHBoxLayout()
        self.cb_boundary = QCheckBox('경계 제출 (PUT /api/boundary)')
        self.cb_boundary.setChecked(True)
        self.cb_cog = QCheckBox('COG 업로드 (S3 + cog_catalog)')
        self.cb_cog.setChecked(True)
        opt_row.addWidget(self.cb_boundary)
        opt_row.addWidget(self.cb_cog)
        opt_row.addStretch()
        layout.addLayout(opt_row)

        # 매칭표
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['adm_cd', '경계', '이미지'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMinimumHeight(180)
        layout.addWidget(self.table, 1)

        # 업로드 버튼
        btn_row = QHBoxLayout()
        self.btn_upload = QPushButton('업로드')
        self.btn_upload.clicked.connect(self._on_upload)
        self.btn_upload.setEnabled(False)
        btn_row.addWidget(self.btn_upload)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 로그
        self.log = QTextEdit(); self.log.setReadOnly(True)
        self.log.setMinimumHeight(140)
        self.log.setStyleSheet('QTextEdit { font-family: monospace; }')
        layout.addWidget(QLabel('진행 상황:'))
        layout.addWidget(self.log)

        self.status = QLabel('폴더 미지정')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(
            self, '완료 데이터 폴더 선택', self.folder_edit.text())
        if d:
            self.folder_edit.setText(d)
            self._detect()

    def _detect(self):
        root = self.folder_edit.text().strip()
        if not root or not os.path.isdir(root):
            QMessageBox.warning(self, '경고', '유효한 폴더를 선택하세요.')
            return
        self._scan = scan_completed_folder(root)
        # 경계 adm_cd / 이미지 adm_cd 교차
        bnd_codes = set()
        for shp in self._scan['boundary_shps']:
            bnd_codes |= _shp_adm_codes(shp)
        img_codes = {_img_adm_code(p) for p in self._scan['images']}
        img_codes.discard('')
        all_codes = sorted(bnd_codes | img_codes)

        self.table.setRowCount(len(all_codes))
        for i, code in enumerate(all_codes):
            self.table.setItem(i, 0, QTableWidgetItem(code))
            self.table.setItem(
                i, 1, QTableWidgetItem('○' if code in bnd_codes else '—'))
            self.table.setItem(
                i, 2, QTableWidgetItem('○' if code in img_codes else '—'))
        self.table.resizeColumnsToContents()

        n_shp = len(self._scan['boundary_shps'])
        n_img = len(self._scan['images'])
        self.status.setText(
            f'인식: 경계 SHP {n_shp}개, 이미지 {n_img}장, '
            f'adm_cd {len(all_codes)}개 (경계 {len(bnd_codes)} / '
            f'이미지 {len(img_codes)})')
        self.btn_upload.setEnabled(n_shp > 0 or n_img > 0)

    def _on_upload(self):
        do_boundary = self.cb_boundary.isChecked()
        do_cog = self.cb_cog.isChecked()
        if not (do_boundary or do_cog):
            QMessageBox.warning(self, '경고', '업로드 대상을 하나 이상 선택하세요.')
            return
        cfg = self.parent_dialog.server_config
        if cfg is None:
            cfg = self.parent_dialog.tabs.widget(0).current_config()
        if not cfg.base_url:
            QMessageBox.warning(
                self, '경고', '[1. 서버 연결] 탭에서 서버 URL/토큰을 먼저 저장하세요.')
            return

        # 경계 geojson 은 메인스레드에서 추출 (QGIS 객체)
        boundary_tasks = []
        if do_boundary:
            from qgis.core import QgsVectorLayer
            for shp in self._scan['boundary_shps']:
                lyr = QgsVectorLayer(shp, os.path.basename(shp), 'ogr')
                if not lyr.isValid():
                    self.log.append(f'[건너뜀] SHP 무효: {shp}')
                    continue
                gj = layer_control.boundary_to_geojson(lyr)
                n = len(gj.get('features', []))
                if n:
                    boundary_tasks.append((os.path.basename(shp), gj, n))

        n_total = (len(boundary_tasks) if do_boundary else 0) + \
                  (len(self._scan['images']) if do_cog else 0)
        if n_total == 0:
            QMessageBox.warning(self, '경고', '업로드할 항목이 없습니다.')
            return
        if QMessageBox.question(
                self, '업로드 확인',
                f'경계 {len(boundary_tasks)}건 + 이미지 '
                f'{len(self._scan["images"]) if do_cog else 0}장을 '
                f'서버에 업로드합니다. 계속할까요?',
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        self.log.clear()
        self.log.append('=== 업로드 시작 ===')
        self.btn_upload.setEnabled(False)
        self._worker = CompletedUploadWorker(
            cfg, boundary_tasks,
            self._scan['images'] if do_cog else [],
            self._scan['img_root'],
            do_boundary, do_cog, scale=0.5)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, line):
        self.log.append(line)
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum())

    def _on_finished(self, summary):
        self.log.append(f'\n=== {summary} ===')
        self.status.setText(summary)
        self.btn_upload.setEnabled(True)
        QMessageBox.information(self, '업로드 완료', summary)

    def _on_failed(self, msg):
        self.log.append(f'\n[ERROR] {msg}')
        self.btn_upload.setEnabled(True)
        QMessageBox.critical(self, '업로드 실패', msg[:500])


class DBEditorDock(QDockWidget):
    """QGIS 메인 윈도우에 도킹되는 DB 작업 위젯.

    iface.addDockWidget(Qt.RightDockWidgetArea, ...) 로 설치하면 사용자가
    한 번 도킹/위치 조정한 뒤 QGIS가 layout 을 기억해 다음 실행에도 같은
    자리에 뜬다.
    """

    def __init__(self, iface, parent=None):
        super().__init__('GIS Scan Tools — DB 작업', parent)
        self.setObjectName('GISScanToolsDBDock')   # QGIS layout 저장 키
        self.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
            | Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
        self.iface = iface
        self.server_config = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        self.tabs.addTab(ServerConnectionTab(self), '1. 서버 연결')
        self.tabs.addTab(WorkListTab(self), '2. 행정리 작업')
        self.tabs.addTab(CompletedUploadTab(self), '3. 완료 데이터 업로드')
        layout.addWidget(self.tabs)
        self.setWidget(container)
        self.setMinimumWidth(540)


# 하위 호환 alias — 외부에서 import 중인 코드용
DBEditorDialog = DBEditorDock
