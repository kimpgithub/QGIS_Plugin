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

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QFormLayout,
    QTextEdit, QMessageBox, QGroupBox, QApplication, QFileDialog,
    QTableWidget, QTableWidgetItem, QDockWidget, QComboBox,
)

from .db_tools import api_client, excel_loader, layer_control
from .db_tools.api_client import ServerConfig, save_config, load_config


PLUGIN_DIR = os.path.dirname(__file__)


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

        # --- 검색 + 명부 리스트 ---
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel('검색:'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('읍면동 코드/명칭, 행정리 코드/명칭')
        self.search.textChanged.connect(self._on_search)
        search_row.addWidget(self.search)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ['읍면동 코드', '읍면동명', '행정리 코드', '행정리명',
             '작업여부', '비고'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
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
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(r.get('adm_cd', '')))
            self.table.setItem(i, 1, QTableWidgetItem(r.get('adm_nm', '')))
            self.table.setItem(i, 2, QTableWidgetItem(r.get('ri_cd', '')))
            self.table.setItem(i, 3, QTableWidgetItem(r.get('ri_nm', '')))
            # 4: 작업여부 — Y/N 콤보로 즉시 편집 + 엑셀 저장
            self.table.setCellWidget(i, 4, self._make_work_yn_combo(
                i, r.get('work_yn', '')))
            self.table.setItem(i, 5, QTableWidgetItem(r.get('remark', '')))
        self.table.resizeColumnsToContents()
        done = sum(1 for r in rows
                   if (r.get('work_yn', '') or '').upper() == 'Y')
        tag = f' (병합이미지 {len(self._merged_codes)}개 읍면동)'
        self.status.setText(
            f'명부 로드: {len(rows)}개 행정리 (작업완료 {done}){tag}')

    def _on_search(self, text):
        text = text.strip().lower()
        for r in range(self.table.rowCount()):
            if not text:
                self.table.setRowHidden(r, False)
                continue
            parts = []
            for c in range(6):
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
        rec['work_yn'] = new
        path = self._slots.get('roster')
        if not path:
            self.status.setText('명부 경로 없음 — 저장 실패')
            return
        try:
            n = excel_loader.update_work_yn(
                path, rec.get('adm_cd', ''), rec.get('ri_cd', ''), new)
        except PermissionError:
            QMessageBox.critical(
                self, '저장 실패',
                '엑셀 파일이 다른 프로그램(엑셀 등)에 열려 있습니다.\n'
                '닫고 다시 시도하세요.')
            # UI 되돌림
            self._revert_work_yn(row_idx, old)
            rec['work_yn'] = old
            return
        except Exception as e:
            QMessageBox.critical(self, '저장 실패', str(e))
            self._revert_work_yn(row_idx, old)
            rec['work_yn'] = old
            return
        if n == 0:
            self.status.setText(
                f"저장 경고 — ri_cd={rec.get('ri_cd','')} 매칭 행 없음")
        else:
            self.status.setText(
                f"저장 완료 — {rec.get('ri_cd','')} 작업여부={new}")

    def _revert_work_yn(self, row_idx, value):
        w = self.table.cellWidget(row_idx, 4)
        if isinstance(w, QComboBox):
            w.blockSignals(True)
            w.setCurrentText(value)
            w.blockSignals(False)

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
                # 4) 분류 심볼 적용 (ri_cd 빈/채워짐 시각화)
                layer_control.apply_work_data_categorized_renderer(work)
                # 5) split→팝업 콜백 연결
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
        self.status.setText(f'마크업 회수 중... ({adm or "전체"})')
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
        layout.addWidget(self.tabs)
        self.setWidget(container)
        self.setMinimumWidth(540)


# 하위 호환 alias — 외부에서 import 중인 코드용
DBEditorDialog = DBEditorDock
