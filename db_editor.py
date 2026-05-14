"""DB 작업 플러그인 진입점 — 서울 서버 연동 (HTTPS 배치 동기화).

툴바 두 번째 아이콘에서 열리는 다이얼로그.

데이터 흐름: 대전은 로컬에서 경계를 디지타이징하고, 결과만 HTTPS로 서버에
제출한다. 발주자 마크업은 HTTPS로 회수한다. PostGIS/MinIO 직접 접속 없음.

구조:
- DBEditorDialog: 탭 컨테이너
  - [1] 서버 연결 (URL/토큰/S3 키)
  - [2] 행정리 작업 (명부 + 로컬 GeoPackage 편집 + 제출 + 마크업 회수)
"""
import os

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QFormLayout,
    QTextEdit, QMessageBox, QGroupBox, QApplication, QFileDialog,
    QTableWidget, QTableWidgetItem,
)

from .db_tools import api_client, excel_loader, layer_control
from .db_tools.api_client import ServerConfig, save_config, load_config


PLUGIN_DIR = os.path.dirname(__file__)


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
    """행정리 작업 — 명부(엑셀) 체크리스트 + 로컬 GeoPackage 편집 + 제출/마크업.

    데이터 흐름:
    1. 명부 엑셀 로드 → 행정리 리스트 표시 (검색·선택)
    2. 행정리 선택 → split/추가 시 자동 부여될 RI 속성 준비
    3. 작업 GeoPackage 를 편집 레이어로 추가 → [작업 시작] 으로 편집 활성
    4. QGIS 편집 툴바로 경계 디지타이징
    5. [마크업 받기] — 발주자 수정요청을 readOnly 레이어로 회수
    6. [제출] — GeoPackage → 서버 boundary 테이블 (PUT /api/boundary)
    """

    def __init__(self, parent_dialog):
        super().__init__()
        self.parent_dialog = parent_dialog
        self.iface = parent_dialog.iface
        self._roster = []           # 명부 행 dict 리스트
        self._work_layer = None     # 로컬 GeoPackage 레이어
        self._work_snapshot = None  # 작업 시작 시 저장, 종료 시 복원
        self._current_admin = ''
        self._current_admin_nm = ''
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        help_label = QLabel(
            '<i>경계는 <b>로컬 GeoPackage</b>에서 디지타이징하고 <b>[제출]</b>로 '
            '서버에 업로드합니다. 명부(행정리현황 엑셀)에서 행정리를 선택하면 '
            'split/추가 시 RI 속성이 자동 부여됩니다. '
            '<b>[마크업 받기]</b>로 발주자 수정요청을 회수합니다.</i>')
        help_label.setWordWrap(True)
        help_label.setStyleSheet(
            'QLabel { padding: 6px; background: #f0f0f0; border-radius: 3px; }')
        layout.addWidget(help_label)

        # 작업 데이터
        src_box = QGroupBox('작업 데이터')
        form = QFormLayout(src_box)

        self.gpkg_edit = QLineEdit()
        self.gpkg_edit.setPlaceholderText('작업 GeoPackage (.gpkg) — 경계 디지타이징 대상')
        btn_gpkg = QPushButton('찾기')
        btn_gpkg.clicked.connect(self._browse_gpkg)
        btn_gpkg_new = QPushButton('새로 만들기')
        btn_gpkg_new.clicked.connect(self._create_gpkg)
        grow = QHBoxLayout()
        grow.addWidget(self.gpkg_edit, 1)
        grow.addWidget(btn_gpkg)
        grow.addWidget(btn_gpkg_new)
        gw = QWidget(); gw.setLayout(grow)
        form.addRow('작업 GeoPackage:', gw)

        self.roster_edit = QLineEdit()
        self.roster_edit.setPlaceholderText('명부(행정리현황) 엑셀 (.xlsx)')
        btn_roster = QPushButton('찾기')
        btn_roster.clicked.connect(self._browse_roster)
        rrow = QHBoxLayout()
        rrow.addWidget(self.roster_edit, 1)
        rrow.addWidget(btn_roster)
        rw = QWidget(); rw.setLayout(rrow)
        form.addRow('명부 엑셀:', rw)

        self.warped_edit = QLineEdit()
        self.warped_edit.setPlaceholderText(
            '(선택) 워프 스캔 루트 폴더 — 행정리 더블클릭 시 시트 자동 로드')
        btn_warped = QPushButton('찾기')
        btn_warped.clicked.connect(self._browse_warped)
        wrow = QHBoxLayout()
        wrow.addWidget(self.warped_edit, 1)
        wrow.addWidget(btn_warped)
        ww = QWidget(); ww.setLayout(wrow)
        form.addRow('워프 폴더:', ww)

        btn_add = QPushButton('작업 GeoPackage를 QGIS 레이어로 추가')
        btn_add.clicked.connect(self._on_add_layer)
        form.addRow(btn_add)
        layout.addWidget(src_box)

        # 작업 제어
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton('작업 시작 (편집 활성, 기타 잠금)')
        self.btn_start.clicked.connect(self._on_start)
        self.btn_end = QPushButton('작업 종료 (저장 + 잠금 해제)')
        self.btn_end.clicked.connect(self._on_end)
        self.btn_end.setEnabled(False)
        self.btn_markup = QPushButton('마크업 받기')
        self.btn_markup.clicked.connect(self._on_get_markup)
        self.btn_submit = QPushButton('제출')
        self.btn_submit.clicked.connect(self._on_submit)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_end)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_markup)
        btn_row.addWidget(self.btn_submit)
        layout.addLayout(btn_row)

        # 검색 + 명부 리스트
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel('검색:'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('읍면동 코드/명칭, 행정리 코드/명칭')
        self.search.textChanged.connect(self._on_search)
        search_row.addWidget(self.search)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ['읍면동 코드', '읍면동명', '행정리 코드', '행정리명', '비고'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.currentCellChanged.connect(
            lambda r, *_: self._on_row_selected(r))
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setMinimumHeight(260)
        layout.addWidget(self.table, 1)

        self.status = QLabel('명부 미로드')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # --- 설정/소스 ---

    def _get_config(self):
        cfg = self.parent_dialog.server_config
        if cfg is None:
            cfg = self.parent_dialog.tabs.widget(0).current_config()
        return cfg

    def _browse_gpkg(self):
        p, _ = QFileDialog.getOpenFileName(
            self, '작업 GeoPackage 선택', self.gpkg_edit.text(),
            'GeoPackage (*.gpkg)')
        if p:
            self.gpkg_edit.setText(p)

    def _create_gpkg(self):
        p, _ = QFileDialog.getSaveFileName(
            self, '작업 GeoPackage 생성', self.gpkg_edit.text(),
            'GeoPackage (*.gpkg)')
        if not p:
            return
        if not p.lower().endswith('.gpkg'):
            p += '.gpkg'
        try:
            lyr = layer_control.ensure_work_geopackage(p)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'GeoPackage 생성 실패: {e}')
            return
        if lyr is None:
            QMessageBox.critical(self, '오류', 'GeoPackage 생성 실패')
            return
        self.gpkg_edit.setText(p)
        self.status.setText(f'GeoPackage 생성: {os.path.basename(p)}')

    def _browse_roster(self):
        p, _ = QFileDialog.getOpenFileName(
            self, '명부(행정리현황) 엑셀 선택', self.roster_edit.text(),
            '엑셀 (*.xlsx *.xlsm)')
        if p:
            self.roster_edit.setText(p)
            self._load_roster(p)

    def _browse_warped(self):
        d = QFileDialog.getExistingDirectory(
            self, '워프 스캔 루트 폴더 선택', self.warped_edit.text())
        if d:
            self.warped_edit.setText(d)

    # --- 명부 로드 ---

    def _load_roster(self, path):
        try:
            _headers, rows, _mapping, missing = excel_loader.read_excel(path)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'명부 엑셀 읽기 실패: {e}')
            return
        if missing:
            QMessageBox.warning(
                self, '경고',
                f'필수 컬럼 누락: {", ".join(missing)}\n'
                f'(ADM_CD, ADM_NM, RI_CD, RI_NM 필요 — 한글명 관용)')
            return
        self._roster = rows
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(r.get('adm_cd', '')))
            self.table.setItem(i, 1, QTableWidgetItem(r.get('adm_nm', '')))
            self.table.setItem(i, 2, QTableWidgetItem(r.get('ri_cd', '')))
            self.table.setItem(i, 3, QTableWidgetItem(r.get('ri_nm', '')))
            self.table.setItem(i, 4, QTableWidgetItem(r.get('remark', '')))
        self.table.resizeColumnsToContents()
        self.status.setText(f'명부 로드: {len(rows)}개 행정리')

    def _on_search(self, text):
        text = text.strip().lower()
        for r in range(self.table.rowCount()):
            if not text:
                self.table.setRowHidden(r, False)
                continue
            vals = ' '.join(
                (self.table.item(r, c).text().lower()
                 if self.table.item(r, c) else '')
                for c in range(5))
            self.table.setRowHidden(r, text not in vals)

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
        """더블클릭 — 워프 스캔 로드 + 해당 영역으로 줌."""
        row = index.row()
        if row < 0 or row >= len(self._roster):
            return
        self._on_row_selected(row)
        cd = self._current_admin
        warp_root = self.warped_edit.text().strip()
        if not warp_root or not cd:
            return
        try:
            from qgis.core import QgsProject, QgsRectangle
            layer_control.clear_warped_scans(self.iface, exclude_admin=cd)
            added = layer_control.load_warped_scans(self.iface, cd, warp_root)
            if added:
                rect = QgsRectangle()
                rect.setMinimal()
                for lyr in QgsProject.instance().mapLayers().values():
                    if lyr.name() in added:
                        rect.combineExtentWith(lyr.extent())
                if not rect.isEmpty():
                    self.iface.mapCanvas().setExtent(rect)
                    self.iface.mapCanvas().refresh()
                self.status.setText(
                    f'{cd} — 워프 스캔 {len(added)}개 로드 + 줌')
            else:
                self.status.setText(f'{cd} — 워프 스캔 없음')
        except Exception as e:
            self.status.setText(f'워프 스캔 로드 오류: {e}')

    # --- 레이어 추가 / 작업 시작·종료 ---

    def _on_add_layer(self):
        path = self.gpkg_edit.text().strip()
        if not path:
            QMessageBox.warning(self, '경고', '작업 GeoPackage를 먼저 지정하세요')
            return
        if not os.path.exists(path):
            QMessageBox.warning(
                self, '경고',
                'GeoPackage 파일이 없습니다. [새로 만들기]로 생성하세요.')
            return
        try:
            lyr = layer_control.add_geopackage_layer(path)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'레이어 추가 실패: {e}')
            return
        if lyr is None:
            QMessageBox.critical(self, '오류', 'GeoPackage 레이어 로드 실패')
            return
        self._work_layer = lyr
        self.status.setText(
            f'작업 레이어 추가: {lyr.name()} ({lyr.featureCount()}개 피처)')

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

    def _on_start(self):
        try:
            self._work_snapshot = layer_control.start_work_mode(self.iface)
            self.btn_start.setEnabled(False)
            self.btn_end.setEnabled(True)
            self.status.setText(
                '작업 시작 — 작업 레이어 편집 가능, 나머지 readOnly')
        except Exception as e:
            QMessageBox.critical(self, '오류', f'작업 시작 실패: {e}')

    def _on_end(self):
        try:
            saved, errors = layer_control.end_work_mode(
                self.iface, self._work_snapshot)
            self._work_snapshot = None
            self.btn_start.setEnabled(True)
            self.btn_end.setEnabled(False)
            msg = f'작업 종료 — {saved}개 레이어 저장'
            if errors:
                msg += f' | 오류 {len(errors)}건: {"; ".join(errors)}'
            self.status.setText(msg)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'작업 종료 실패: {e}')

    # --- 마크업 회수 ---

    def _on_get_markup(self):
        cfg = self._get_config()
        adm = self._current_admin or None
        self.status.setText(
            f'마크업 회수 중... ({adm or "전체"})')
        QApplication.processEvents()
        try:
            geojson = api_client.get_markup(cfg, adm)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'마크업 회수 실패: {e}')
            self.status.setText(f'마크업 회수 실패: {e}')
            return
        lyr, n = layer_control.load_markup_layer(geojson)
        if lyr is None:
            self.status.setText('마크업 0건 (또는 로드 실패)')
        else:
            self.status.setText(
                f'마크업 {n}건 회수 — "{lyr.name()}" 레이어로 표시')

    # --- 제출 ---

    def _on_submit(self):
        layer = self._find_work_layer()
        if layer is None:
            QMessageBox.warning(
                self, '경고',
                '작업 레이어가 없습니다. [작업 GeoPackage를 QGIS 레이어로 추가] 먼저.')
            return
        # 편집 중이면 먼저 저장 (provider 에 반영돼야 추출됨)
        if layer.isEditable():
            if not layer.commitChanges():
                QMessageBox.critical(self, '오류', '편집 내역 저장 실패 — 제출 중단')
                return
            layer.startEditing()
        try:
            geojson = layer_control.layer_to_geojson(layer)
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

class DBEditorDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.server_config = None
        self.setWindowTitle('GIS Scan Tools — DB 작업')
        self.resize(820, 680)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(ServerConnectionTab(self), '1. 서버 연결')
        self.tabs.addTab(WorkListTab(self), '2. 행정리 작업')
        layout.addWidget(self.tabs)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton('닫기')
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)
