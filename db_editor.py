"""DB 작업 플러그인 진입점 — 서울 서버 연동 (HTTPS 배치 동기화).

툴바 두 번째 아이콘에서 열리는 다이얼로그.

데이터 흐름: 대전은 로컬에서 경계를 디지타이징하고, 결과만 HTTPS로 서버에
제출한다. PostGIS/MinIO 직접 접속 없음.

수정요청(마크업)은 플러그인이 다루지 않는다 — 작업자가 검수 웹 화면에서 요청을
보고 QGIS 로 경계를 수정·제출하면, 요청의 반영/반려 처리는 웹에서 한다.

구조:
- DBEditorDialog: 탭 컨테이너
  - [1] 서버 연결 (URL/토큰/S3 키)
  - [2] 행정리 작업 (작업 폴더 자동인식 → 13레이어 구성 → 편집 → 제출)
"""
import os

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QFormLayout,
    QTextEdit, QMessageBox, QGroupBox, QApplication, QFileDialog,
    QTableWidget, QTableWidgetItem, QDockWidget, QComboBox, QCheckBox,
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
            'HTTPS로만 통신합니다 — 경계 제출/COG 등록은 API, 이미지 업로드는 S3.'
            '<br>설정은 QGIS 설정(QSettings)에 저장되어 다음 실행 시 복원됩니다.</i>')
        help_label.setWordWrap(True)
        help_label.setStyleSheet(
            'QLabel { padding: 6px; background: #f0f0f0; border-radius: 3px; }')
        layout.addWidget(help_label)

        # API
        api_box = QGroupBox('API (경계 제출 / COG 등록)')
        api_form = QFormLayout(api_box)
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText('https://www.kosisgis.kr')
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
    """행정리 작업 — 작업 폴더 자동인식 → 13레이어 구성 → 편집 → 제출.

    화면정의서 슬11~12 흐름:
    1. 작업 폴더 지정 → 하위 폴더 규칙(01_~13_)으로 슬롯 자동 인식
    2. [화면 구성] — 13레이어를 on/off 기본값대로 QGIS 로드 + 명부 로드
    3. [작업 시작] — 작업데이터 레이어만 편집 활성, 나머지 잠금
    4. 명부에서 행정리 선택 → split/추가 시 RI 속성 자동 부여
    5. [제출] — 작업데이터 → 서버 (수정요청 확인/처리는 검수 웹에서)
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
        self._editor = None              # 명부 WorkbookEditor (메인스레드 전용)
        self._editor_path = None
        self._suppress_item_changed = False  # 프로그램적 셀 갱신 중 itemChanged 무시
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
        self.btn_submit = QPushButton('제출')
        self.btn_submit.clicked.connect(self._on_submit)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_end)
        btn_row.addStretch()
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
        # 비고(6열) 인라인 편집 — 편집 종료 시 엑셀 REMARK 자동 저장
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setMinimumHeight(220)
        layout.addWidget(self.table, 1)

        edit_hint = QLabel(
            '<i>비고 칸을 더블클릭하면 바로 입력할 수 있습니다 — 입력 후 '
            'Enter/다른 칸 클릭 시 명부에 자동 저장됩니다.</i>')
        edit_hint.setStyleSheet('QLabel { color: #777; }')
        layout.addWidget(edit_hint)

        # 선택 폴리곤 ← 명부 행 RI 부여
        assign_row = QHBoxLayout()
        self.btn_assign = QPushButton('선택 폴리곤에 행정리 부여')
        self.btn_assign.setToolTip(
            '지도에서 폴리곤(들)을 선택하고, 위 명부에서 행정리 행을 고른 뒤 누르세요. '
            '선택된 폴리곤 모두에 같은 행정리가 부여됩니다.')
        self.btn_assign.clicked.connect(self._on_assign_selected)
        assign_row.addWidget(self.btn_assign)
        assign_row.addStretch()
        layout.addLayout(assign_row)

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
        self._suppress_item_changed = True   # 프로그램적 setItem → 저장 트리거 방지
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
        self._suppress_item_changed = False
        self.table.setSortingEnabled(True)
        done = sum(1 for r in rows
                   if (r.get('work_yn', '') or '').upper() == 'Y')
        tag = f' (병합이미지 {len(self._merged_codes)}개 읍면동)'
        self.status.setText(
            f'명부 로드: {len(rows)}개 행정리 (작업완료 {done}){tag}')
        self._ensure_editor(path)
        self._refresh_area_column()
        self._hook_work_layer_changes()

    # --- 명부 WORK_YN/REMARK 저장 (메인스레드 인메모리 + 경계 시점 저장) ---
    #
    # openpyxl 은 내부적으로 lxml(libxml2)을 쓴다. 이를 별도 QThread 에서 돌리면
    # QGIS 메인스레드의 네이티브 코드(GDAL/GEOS 등)와 libxml2 전역상태가 동시
    # 접근돼 Windows access violation 으로 플러그인이 죽는다. 그래서 워크북
    # 작업은 전적으로 메인스레드에서만 한다 — 변경은 인메모리(즉시 반영),
    # 디스크 저장은 [작업 종료]/닫기 같은 경계 시점에 1회.

    def _ensure_editor(self, path):
        """명부 경로별 WorkbookEditor 1개 유지. 경로 바뀌면 이전 것 저장 후 교체."""
        if self._editor is not None and self._editor_path == path:
            return
        self.shutdown_saver()
        self._editor = excel_loader.WorkbookEditor(path)
        self._editor_path = path

    def _set_cell(self, adm_cd, ri_cd, field, value):
        """명부 셀 인메모리 갱신. 첫 호출은 워크북 로딩(수 초)이라 대기 커서."""
        path = self._slots.get('roster')
        if not path:
            self.status.setText('명부 경로 없음 — 저장 불가')
            return
        self._ensure_editor(path)
        first_load = not self._editor.loaded
        if first_load:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.status.setText('명부 준비 중...')
            QApplication.processEvents()
        try:
            self._editor.set_cell(adm_cd, ri_cd, field, value)
        except Exception as e:
            QMessageBox.critical(self, '저장 실패', str(e))
        finally:
            if first_load:
                QApplication.restoreOverrideCursor()

    def _flush_editor(self):
        """미저장 변경을 디스크에 1회 저장 (메인스레드, 대기 커서)."""
        ed = self._editor
        if ed is None or not ed.dirty:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if ed.flush():
                self.status.setText('명부 저장 완료')
        except Exception as e:
            msg = str(e)
            if 'Permission' in msg:
                QMessageBox.critical(
                    self, '명부 저장 실패',
                    '명부 엑셀이 다른 프로그램(엑셀 등)에 열려 있습니다.\n'
                    '닫고 [작업 종료]를 다시 누르세요. (변경분은 메모리에 보존)')
            else:
                QMessageBox.critical(self, '명부 저장 실패', msg)
        finally:
            QApplication.restoreOverrideCursor()

    def shutdown_saver(self):
        """종료 전 미저장분 저장 + 에디터 정리."""
        if self._editor is None:
            return
        self._flush_editor()
        try:
            self._editor.close()
        except Exception:
            pass
        self._editor = None
        self._editor_path = None

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
        self._suppress_item_changed = True
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
        self._suppress_item_changed = False
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
        rec['work_yn'] = new   # 메모리 즉시 반영, 엑셀 저장은 [작업 종료] 시
        # 정렬키 동기화 — 콤보가 바뀐 row 의 item 도 갱신
        cur_row = self._find_row(rec.get('adm_cd', ''), rec.get('ri_cd', ''))
        if cur_row >= 0:
            it = self.table.item(cur_row, 4)
            if it is not None:
                it.setText(new)
        self._set_cell(rec.get('adm_cd', ''), rec.get('ri_cd', ''),
                       'work_yn', new)
        self.status.setText(
            f"작업여부={new} — {rec.get('ri_cd','')} ([작업 종료] 시 저장)")

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

    def _make_area_item(self, area_m2):
        """현재면적 셀 — 정렬용 정수값 보유."""
        it = QTableWidgetItem()
        it.setData(Qt.DisplayRole, int(area_m2))
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return it

    # --- 행정리 선택 → 자동부여 준비 ---

    def _on_row_selected(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        # 정렬 시 화면 행번호 ≠ _roster 인덱스 → 셀의 코드로 레코드를 조회한다.
        it_adm = self.table.item(row, 0)
        it_ri = self.table.item(row, 2)
        adm_cd = it_adm.text().strip() if it_adm else ''
        ri_cd = it_ri.text().strip() if it_ri else ''
        r = next((x for x in self._roster
                  if (x.get('adm_cd', '') or '').strip() == adm_cd
                  and (x.get('ri_cd', '') or '').strip() == ri_cd), None)
        if r is None:
            return
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
        """더블클릭 — 비고열은 인라인 편집, 그 외는 해당 행정리(없으면 읍면동)로 맵 줌."""
        if index.column() == 6:          # 비고 → 인라인 편집 시작
            it = self.table.item(index.row(), 6)
            if it is not None:
                self.table.editItem(it)
            return
        row = index.row()
        if row < 0 or row >= len(self._roster):
            return
        self._on_row_selected(row)
        # 정렬돼도 안전하게 셀에서 코드 읽기
        it_adm = self.table.item(row, 0)
        it_ri = self.table.item(row, 2)
        adm = it_adm.text() if it_adm else self._current_admin
        ri = it_ri.text() if it_ri else ''
        # 부여된 행정리 폴리곤이 있으면 그 영역으로, 없으면 읍면동으로
        if not self._zoom_to_ri(adm, ri):
            self._zoom_to_admin(adm)

    def _zoom_to_ri(self, adm_cd, ri_cd):
        """작업데이터에서 (adm_cd, ri_cd) 폴리곤들의 합 bbox 로 줌. 있으면 True."""
        if not (adm_cd and ri_cd):
            return False
        lyr = self._find_work_layer()
        if lyr is None or not lyr.isValid():
            return False
        fields = lyr.fields()
        idx_adm = next((i for i in range(fields.count())
                        if fields.at(i).name().lower() == 'adm_cd'), -1)
        idx_ri = next((i for i in range(fields.count())
                       if fields.at(i).name().lower() == 'ri_cd'), -1)
        if idx_adm < 0 or idx_ri < 0:
            return False
        from qgis.core import QgsRectangle
        bbox = None
        for f in lyr.getFeatures():
            if not f.hasGeometry():
                continue
            if (str(f.attribute(idx_adm) or '').strip() == adm_cd
                    and str(f.attribute(idx_ri) or '').strip() == ri_cd):
                gb = f.geometry().boundingBox()
                bbox = QgsRectangle(gb) if bbox is None \
                    else (bbox.combineExtentWith(gb) or bbox)
        if bbox is None or bbox.isEmpty():
            return False
        canvas = self.iface.mapCanvas()
        dst = canvas.mapSettings().destinationCrs()
        src = lyr.crs()
        if src.isValid() and dst.isValid() and src != dst:
            from qgis.core import QgsCoordinateTransform, QgsProject
            tr = QgsCoordinateTransform(src, dst, QgsProject.instance())
            bbox = tr.transformBoundingBox(bbox)
        bbox.scale(1.3)
        canvas.setExtent(bbox)
        canvas.refresh()
        self.status.setText(f'맵 이동: {ri_cd}')
        return True

    def _on_item_changed(self, item):
        """비고(6열) 인라인 편집 종료 → 명부 메모리 + 엑셀 REMARK 자동 저장."""
        if self._suppress_item_changed or item.column() != 6:
            return
        row = item.row()
        it_adm = self.table.item(row, 0)
        it_ri = self.table.item(row, 2)
        if not (it_adm and it_ri):
            return
        adm, ri = it_adm.text(), it_ri.text()
        new_remark = item.text()
        rec = next((r for r in self._roster
                    if r.get('adm_cd', '') == adm
                    and r.get('ri_cd', '') == ri), None)
        if rec is None:
            return
        if (rec.get('remark', '') or '') == new_remark:
            return
        rec['remark'] = new_remark
        self._set_cell(adm, ri, 'remark', new_remark)
        self.status.setText(
            f"비고 입력됨 — {ri} {rec.get('ri_nm','')} ([작업 종료] 시 저장)")

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
            # 3) attach=False — current_ri 기반 자동부여 끄고, split 시엔
            #    area 재계산만 (리 부여는 [선택 폴리곤에 부여] 버튼으로)
            self._work_snapshot = layer_control.start_work_mode(
                self.iface, attach=False)
            work = self._find_work_layer()
            if work is not None:
                self._connect_feature_added(work)
            self.btn_start.setEnabled(False)
            self.btn_end.setEnabled(True)
            extra = f' (시드 {seeded}개)' if seeded else ''
            self.status.setText(
                '작업 시작 — 분할을 자유롭게 한 뒤, 폴리곤 선택 + 명부 행 선택 → '
                f'[선택 폴리곤에 행정리 부여]{extra}')
        except Exception as e:
            QMessageBox.critical(self, '오류', f'작업 시작 실패: {e}')

    def _on_end(self):
        try:
            self._flush_editor()   # 미저장 WORK_YN/REMARK 디스크 저장
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

    # --- split 시 area 자동 재계산 (부여는 [선택 폴리곤에 부여] 버튼으로) ---

    def _connect_feature_added(self, layer):
        """split/추가·도형변경 시 area 필드만 자동 재계산.

        리 속성 부여는 더 이상 팝업으로 하지 않고, 분할을 자유롭게 한 뒤
        [선택 폴리곤에 행정리 부여] 버튼으로 명부 행과 매칭한다.
        """
        self._disconnect_feature_added()
        added = lambda fid, _l=layer: layer_control.recalc_area(_l, fid)
        changed = lambda fid, _g=None, _l=layer: layer_control.recalc_area(_l, fid)
        layer.featureAdded.connect(added)
        layer.geometryChanged.connect(changed)
        self._feature_added_slot = (layer, added, changed)

    def _disconnect_feature_added(self):
        if not self._feature_added_slot:
            return
        layer, added, changed = self._feature_added_slot
        # 신호 속성 접근 자체가 스테일 레이어면 RuntimeError(C++ deleted) →
        # try 안에서 평가해야 한다.
        try:
            layer.featureAdded.disconnect(added)
        except (TypeError, RuntimeError):
            pass
        try:
            layer.geometryChanged.disconnect(changed)
        except (TypeError, RuntimeError):
            pass
        self._feature_added_slot = None

    # --- 선택 폴리곤 ← 명부 행 RI 부여 ---

    def _on_assign_selected(self):
        work = self._find_work_layer()
        if work is None:
            QMessageBox.warning(self, '경고', '작업데이터 레이어가 없습니다.')
            return
        row = self.table.currentRow()
        if row < 0 or row >= self.table.rowCount():
            QMessageBox.warning(self, '경고', '명부에서 부여할 행정리 행을 먼저 선택하세요.')
            return
        # 정렬 시 화면 행번호 ≠ _roster 인덱스 → 셀의 코드로 레코드를 조회한다.
        it_adm = self.table.item(row, 0)
        it_ri = self.table.item(row, 2)
        adm_cd = it_adm.text().strip() if it_adm else ''
        ri_cd = it_ri.text().strip() if it_ri else ''
        rec = next((x for x in self._roster
                    if (x.get('adm_cd', '') or '').strip() == adm_cd
                    and (x.get('ri_cd', '') or '').strip() == ri_cd), None)
        if rec is None:
            QMessageBox.warning(self, '경고', '명부에서 부여할 행정리 행을 먼저 선택하세요.')
            return
        fids = list(work.selectedFeatureIds())
        if not fids:
            QMessageBox.warning(
                self, '경고',
                '지도에서 부여할 폴리곤을 먼저 선택하세요. '
                '(QGIS "객체 선택" 툴로 클릭, 여러 개 선택 가능)')
            return
        if not work.isEditable():
            work.startEditing()
        for fid in fids:
            self._assign_attrs_to_feature(work, fid, rec)
        self._mark_row_done(rec)
        # 부여 즉시 면적 반영 — 편집 중 getFeatures 가 미커밋 변경도 보므로
        # editingStopped 를 기다리지 않고 바로 (adm_cd, ri_cd) 면적 합 갱신
        self._refresh_area_column()
        self.status.setText(
            f"부여 완료 — {rec.get('ri_cd','')} {rec.get('ri_nm','')} "
            f"→ 폴리곤 {len(fids)}개")

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
        self._set_cell(rec.get('adm_cd', ''), rec.get('ri_cd', ''),
                       'work_yn', 'Y')
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
        feats = geojson.setdefault('features', [])
        # 지도 문제로 폴리곤을 그리지 못한 행정리도 명부 정보(geom=null)로 함께
        # 제출 — 웹에서 "경계 없음" 으로 표시되도록.
        unmapped = self._unmapped_roster_features(feats)
        feats.extend(unmapped)
        n_geom = len(feats) - len(unmapped)
        n = len(feats)
        if n == 0:
            QMessageBox.warning(self, '경고', '제출할 내용이 없습니다.')
            return
        # 부호(ri_cd)가 빈 폴리곤은 빈 채로 제출한다 — 서버가 읍면 단위 전체
        # 교체로 저장하므로 키 충돌이 없고, 빈 부호는 웹에서 발주자가
        # 속성등록 요청으로 채운다. (가짜 일련번호 자동부여 제거)
        # 단, *실제 부호*가 같은 읍면 안에서 중복이면 서버가 400 으로 거부한다.
        # 수정요청(마크업) 처리는 웹에서 — 여기서는 경계 데이터만 제출한다.
        extra = (f' + 미매핑 행정리 {len(unmapped)}건(경계 없음)'
                 if unmapped else '')
        if QMessageBox.question(
                self, '제출 확인',
                f'경계 {n_geom}건{extra}을 서버에 제출합니다.\n계속할까요?',
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

    def _unmapped_roster_features(self, mapped_feats):
        """작업 세션 명부(self._roster) 기준 미매핑 행정리 → geom=null Feature."""
        return unmapped_roster_features(
            self._roster, mapped_feats, self._merged_codes)


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


def unmapped_roster_features(roster, mapped_feats, scope_admins=None):
    """폴리곤이 부여되지 않은 명부 행정리 → geom=null Feature 리스트.

    명부(행정리현황 엑셀) 기준 — 폴리곤이 부여된 행정리는 부여대로(geom 경로),
    부여되지 않은 행정리는 이름만(geom=null) 제출해 웹에 "경계 없음"으로 노출.
    명부에 있는데 폴리곤이 없으면 곧 미매핑(작업완료/비고 플래그 무관).

    scope_admins: 미매핑을 채울 읍면(adm_cd) 범위. None 이면 mapped_feats 의
    adm_cd 집합을 사용 — 완료데이터 업로드처럼 SHP 단위로 호출하면 그 SHP 의
    읍면만 대상이 된다. 명부는 adm_cd/ri_cd/ri_nm 필수라 행마다 보장됨.
    """
    mapped = set()           # 이미 폴리곤이 부여된 (adm_cd, ri_cd)
    admins_in_geo = set()
    for f in mapped_feats:
        p = f.get('properties', {}) or {}
        a = (p.get('adm_cd') or '').strip()
        r = (p.get('ri_cd') or '').strip()
        if a:
            admins_in_geo.add(a)
        if a and r:
            mapped.add((a, r))
    scope = ({str(c).strip() for c in scope_admins} if scope_admins
             else admins_in_geo)
    out = []
    for rec in (roster or []):
        a = (rec.get('adm_cd', '') or '').strip()
        r = (rec.get('ri_cd', '') or '').strip()
        if not a or not r or a not in scope or (a, r) in mapped:
            continue
        out.append({
            'type': 'Feature',
            'geometry': None,
            'properties': {
                'adm_cd': a, 'adm_nm': rec.get('adm_nm', '') or '',
                'ri_cd': r, 'ri_nm': rec.get('ri_nm', '') or '',
                'remark': (rec.get('remark', '') or '').strip(),
            },
        })
    return out


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
            '<br>명부(행정리현황.xlsx)를 주면 폴리곤 없는 행정리도 "경계 없음"'
            '으로 함께 제출됩니다.'
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

        # 명부(행정리현황.xlsx) — 선택. 주면 폴리곤 없는 행정리도 이름만 제출.
        rrow = QHBoxLayout()
        rrow.addWidget(QLabel('명부 (선택):'))
        self.roster_edit = QLineEdit()
        self.roster_edit.setPlaceholderText(
            '행정리현황.xlsx — 폴리곤 없는 행정리를 "경계 없음"으로 함께 제출')
        rrow.addWidget(self.roster_edit, 1)
        btn_roster = QPushButton('찾아보기')
        btn_roster.clicked.connect(self._browse_roster)
        rrow.addWidget(btn_roster)
        layout.addLayout(rrow)

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

    def _browse_roster(self):
        f, _ = QFileDialog.getOpenFileName(
            self, '명부(행정리현황) 엑셀 선택', self.roster_edit.text(),
            'Excel (*.xlsx *.xls)')
        if f:
            self.roster_edit.setText(f)

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

        # 명부(선택) — 주면 폴리곤 없는 행정리를 geom=null 로 함께 제출.
        roster_path = self.roster_edit.text().strip()
        if roster_path and not os.path.isfile(roster_path):
            QMessageBox.warning(self, '경고', '명부 엑셀 경로가 유효하지 않습니다.')
            return
        self.log.clear()   # 빌드 중 로그(명부 로드 등)가 보존되도록 여기서 비움

        # 경계 geojson 은 메인스레드에서 추출 (QGIS 객체)
        boundary_tasks = []
        n_unmapped = 0
        if do_boundary:
            from qgis.core import QgsVectorLayer
            geojsons = []
            bnd_codes = set()
            for shp in self._scan['boundary_shps']:
                lyr = QgsVectorLayer(shp, os.path.basename(shp), 'ogr')
                if not lyr.isValid():
                    self.log.append(f'[건너뜀] SHP 무효: {shp}')
                    continue
                gj = layer_control.boundary_to_geojson(lyr)
                # 빈 부호(ri_cd)는 빈 채로 업로드 — 서버가 읍면 단위 전체
                # 교체로 저장하므로 자동부여 불필요. 실제 부호 중복만 서버가 거부.
                for f in gj.get('features', []):
                    a = ((f.get('properties') or {}).get('adm_cd') or '').strip()
                    if a:
                        bnd_codes.add(a)
                geojsons.append((os.path.basename(shp), gj))

            # 명부 로드 — SHP 의 읍면 범위만 (38K 행 중 대상만 조기 컷)
            roster_rows = []
            if roster_path and bnd_codes:
                try:
                    _h, roster_rows, _m, _miss = excel_loader.read_excel(
                        roster_path, adm_codes=bnd_codes)
                    self.log.append(
                        f'명부 로드: {len(roster_rows)}개 행정리 (대상 읍면 '
                        f'{len(bnd_codes)}개)')
                except Exception as e:
                    QMessageBox.critical(self, '오류', f'명부 엑셀 읽기 실패: {e}')
                    return

            for label, gj in geojsons:
                feats = gj.setdefault('features', [])
                # SHP 단위로 호출 → 그 SHP 의 읍면만 미매핑 대상
                extra = unmapped_roster_features(roster_rows, feats)
                feats.extend(extra)
                n_unmapped += len(extra)
                if feats:
                    boundary_tasks.append((label, gj, len(feats)))

        n_total = (len(boundary_tasks) if do_boundary else 0) + \
                  (len(self._scan['images']) if do_cog else 0)
        if n_total == 0:
            QMessageBox.warning(self, '경고', '업로드할 항목이 없습니다.')
            return
        extra_msg = f' (미매핑 행정리 {n_unmapped}건 포함)' if n_unmapped else ''
        if QMessageBox.question(
                self, '업로드 확인',
                f'경계 {len(boundary_tasks)}건{extra_msg} + 이미지 '
                f'{len(self._scan["images"]) if do_cog else 0}장을 '
                f'서버에 업로드합니다. 계속할까요?',
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

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

    def closeEvent(self, event):
        # 닫기 전 WORK_YN 미저장분 flush + 저장 스레드 정지
        try:
            tab = self.tabs.widget(1)
            if hasattr(tab, 'shutdown_saver'):
                tab.shutdown_saver()
        except Exception:
            pass
        super().closeEvent(event)


# 하위 호환 alias — 외부에서 import 중인 코드용
DBEditorDialog = DBEditorDock
