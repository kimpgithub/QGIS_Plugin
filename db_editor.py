"""DB 작업 플러그인 진입점 — 화면정의서 Part 3 (S10-S14).

툴바 두 번째 아이콘에서 열리는 다이얼로그. 당장은 PG 연결 UI만 있고,
이후 Phase 3~6에서 엑셀 탑재 / 작업리스트 / 편집 툴바 / Simplify를 추가.

구조:
- DBEditorDialog: 탭 컨테이너
  - [1] PG 연결 (이번 Phase 2)
  - [2] 엑셀 탑재 (Phase 3)
  - [3] 행정리 작업 (Phase 4~6)
  - [4] 경량화 (Phase 6)
"""
import os

from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QFormLayout, QComboBox, QSpinBox,
    QTextEdit, QMessageBox, QGroupBox, QApplication, QFileDialog,
    QTableWidget, QTableWidgetItem,
)
from qgis.PyQt.QtCore import Qt

from .db_tools.pg_connection import (
    PGProfile, save_profile, load_profile, list_profiles, delete_profile,
    test_connection, SETTINGS_PREFIX,
)
from .db_tools import excel_loader, admin_list, layer_control


PLUGIN_DIR = os.path.dirname(__file__)


# ============================================================
# Tab: PG 연결
# ============================================================

class PGConnectionTab(QWidget):
    def __init__(self, parent_dialog):
        super().__init__()
        self.parent_dialog = parent_dialog
        self._build()
        self._refresh_profile_list()

    def _build(self):
        layout = QVBoxLayout(self)

        # 프로파일 선택
        prof_box = QGroupBox('연결 프로파일')
        prof_layout = QHBoxLayout(prof_box)
        self.profile_cb = QComboBox()
        self.profile_cb.setEditable(True)
        self.profile_cb.currentTextChanged.connect(self._on_profile_changed)
        prof_layout.addWidget(QLabel('프로파일:'))
        prof_layout.addWidget(self.profile_cb, 1)
        self.btn_load = QPushButton('로드')
        self.btn_load.clicked.connect(self._on_load)
        self.btn_delete = QPushButton('삭제')
        self.btn_delete.clicked.connect(self._on_delete)
        prof_layout.addWidget(self.btn_load)
        prof_layout.addWidget(self.btn_delete)
        layout.addWidget(prof_box)

        # 접속 정보
        form_box = QGroupBox('접속 정보 (PostgreSQL + PostGIS)')
        form = QFormLayout(form_box)
        self.host = QLineEdit('localhost')
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(5432)
        self.database = QLineEdit('')
        self.schema = QLineEdit('public')
        self.username = QLineEdit('postgres')
        self.password = QLineEdit(''); self.password.setEchoMode(QLineEdit.Password)
        form.addRow('Host:', self.host)
        form.addRow('Port:', self.port)
        form.addRow('Database:', self.database)
        form.addRow('Schema (기본):', self.schema)
        form.addRow('User:', self.username)
        form.addRow('Password:', self.password)
        layout.addWidget(form_box)

        # 버튼
        btn_row = QHBoxLayout()
        self.btn_test = QPushButton('연결 테스트')
        self.btn_test.clicked.connect(self._on_test)
        self.btn_save = QPushButton('프로파일 저장')
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_test)
        btn_row.addWidget(self.btn_save)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 로그
        self.log = QTextEdit(); self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        self.log.setStyleSheet('QTextEdit { font-family: monospace; }')
        layout.addWidget(QLabel('상태:'))
        layout.addWidget(self.log)

        # 도움말
        help_label = QLabel(
            '<i>ℹ 프로파일은 QGIS 설정(QSettings)에 저장됩니다. '
            '연결 테스트 후 저장하면 다음 실행 시 자동 복원. '
            '비밀번호는 평문 저장되니 개인 PC에서만 사용 권장.</i>')
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        layout.addStretch()

    def _refresh_profile_list(self):
        current = self.profile_cb.currentText()
        self.profile_cb.blockSignals(True)
        self.profile_cb.clear()
        for name in list_profiles():
            self.profile_cb.addItem(name)
        if current:
            idx = self.profile_cb.findText(current)
            if idx >= 0:
                self.profile_cb.setCurrentIndex(idx)
        self.profile_cb.blockSignals(False)
        # 초기 로드
        self._on_load()

    def _on_profile_changed(self, _name):
        pass  # 수동 로드 버튼 사용 (실수 방지)

    def _current_profile(self):
        return PGProfile(
            host=self.host.text().strip() or 'localhost',
            port=self.port.value(),
            database=self.database.text().strip(),
            schema=self.schema.text().strip() or 'public',
            username=self.username.text().strip(),
            password=self.password.text(),
        )

    def _set_form(self, p: PGProfile):
        self.host.setText(p.host)
        self.port.setValue(p.port)
        self.database.setText(p.database)
        self.schema.setText(p.schema)
        self.username.setText(p.username)
        self.password.setText(p.password)

    def _on_load(self):
        name = self.profile_cb.currentText().strip() or 'default'
        p = load_profile(name)
        self._set_form(p)
        self.log.append(f'[로드] "{name}" 프로파일 불러옴')

    def _on_save(self):
        name = self.profile_cb.currentText().strip() or 'default'
        save_profile(name, self._current_profile())
        self.log.append(f'[저장] "{name}" 프로파일 저장')
        self._refresh_profile_list()

    def _on_delete(self):
        name = self.profile_cb.currentText().strip()
        if not name or name == 'default':
            QMessageBox.warning(self, '삭제 불가', 'default 프로파일은 삭제 불가')
            return
        if QMessageBox.question(
                self, '삭제 확인', f'"{name}" 프로파일을 삭제할까요?',
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            delete_profile(name)
            self.log.append(f'[삭제] "{name}"')
            self._refresh_profile_list()

    def _on_test(self):
        p = self._current_profile()
        self.log.append(f'[테스트] {p.host}:{p.port} dbname={p.database or "(default)"}'
                        f' user={p.username}')
        QApplication.processEvents()
        ok, msg = test_connection(p, timeout_s=5)
        if ok:
            self.log.append(f'  ✅ 연결 성공\n{msg}')
            # parent_dialog가 활성 프로파일 알도록 저장
            self.parent_dialog.active_profile = p
        else:
            self.log.append(f'  ❌ 실패: {msg}')


# ============================================================
# Tab: 엑셀 → PostGIS 탑재 (행정리현황)
# ============================================================

class ExcelLoadTab(QWidget):
    """행정리현황 엑셀 업로드 → ri_status 테이블에 업서트.

    화면정의서 S12 요구사항. image5 스키마 기준.
    """

    def __init__(self, parent_dialog):
        super().__init__()
        self.parent_dialog = parent_dialog
        self._excel_path = None
        self._cached_rows = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        help_label = QLabel(
            '<i>행정리현황 엑셀 파일을 선택하면 컬럼이 자동 인식됩니다.'
            '<br>필수 컬럼: <b>ADM_CD, ADM_NM, RI_CD, RI_NM</b> '
            '(한글명·공백·대소문자 관용).'
            '<br>대상 테이블이 없으면 자동 생성, 있으면 (adm_cd, ri_cd) 기준 '
            '업서트됩니다.</i>')
        help_label.setWordWrap(True)
        help_label.setStyleSheet(
            'QLabel { padding: 6px; background: #f0f0f0; border-radius: 3px; }')
        layout.addWidget(help_label)

        # 1) 파일 선택
        path_box = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText('행정리현황 엑셀 파일 선택 (.xlsx)')
        self.btn_browse = QPushButton('파일 선택')
        self.btn_browse.clicked.connect(self._on_browse)
        path_box.addWidget(QLabel('엑셀:'))
        path_box.addWidget(self.path_edit, 1)
        path_box.addWidget(self.btn_browse)
        layout.addLayout(path_box)

        # 2) 대상 테이블 설정
        form = QFormLayout()
        self.schema_edit = QLineEdit('public')
        self.table_edit = QLineEdit('ri_status')
        form.addRow('Schema:', self.schema_edit)
        form.addRow('Table:', self.table_edit)
        layout.addLayout(form)

        # 3) 미리보기
        preview_box = QGroupBox('미리보기 (첫 10행)')
        pv_layout = QVBoxLayout(preview_box)
        self.preview = QTableWidget()
        self.preview.setMinimumHeight(200)
        pv_layout.addWidget(self.preview)
        self.column_info = QLabel('파일 미선택')
        self.column_info.setWordWrap(True)
        pv_layout.addWidget(self.column_info)
        layout.addWidget(preview_box)

        # 4) 실행
        btn_row = QHBoxLayout()
        self.btn_load = QPushButton('PostGIS에 탑재')
        self.btn_load.clicked.connect(self._on_load)
        self.btn_load.setEnabled(False)
        btn_row.addWidget(self.btn_load)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 로그
        self.log = QTextEdit(); self.log.setReadOnly(True)
        self.log.setMinimumHeight(100)
        self.log.setStyleSheet('QTextEdit { font-family: monospace; }')
        layout.addWidget(self.log)

    def _on_browse(self):
        p, _ = QFileDialog.getOpenFileName(
            self, '행정리현황 엑셀 선택', '',
            '엑셀 (*.xlsx *.xlsm);;모든 파일 (*)')
        if not p:
            return
        self.path_edit.setText(p)
        self._load_preview(p)

    def _load_preview(self, path):
        try:
            headers, rows, mapping, missing = excel_loader.read_excel(
                path, limit=500)
        except Exception as e:
            self.log.append(f'[오류] 엑셀 읽기 실패: {e}')
            return

        self._excel_path = path
        self._cached_rows = rows

        # 컬럼 매핑 상태
        mapped = [f'{orig}→{canon}' for orig, canon in mapping.items()]
        info = f'<b>총 유효행:</b> {len(rows)}건 (첫 500행까지 스캔)<br>'
        info += f'<b>매핑된 컬럼:</b> {", ".join(mapped) or "없음"}<br>'
        if missing:
            info += f'<b style="color:red">누락 필수 컬럼:</b> {", ".join(missing)}'
            self.btn_load.setEnabled(False)
        else:
            info += '<b style="color:green">✓ 모든 필수 컬럼 OK</b>'
            self.btn_load.setEnabled(True)
        self.column_info.setText(info)

        # 미리보기 테이블
        cols = ['sido_cd', 'sido_nm', 'sigungu_cd', 'sigungu_nm',
                'adm_cd', 'adm_nm', 'li_nm', 'ri_nm', 'ri_cd', 'remark']
        self.preview.setColumnCount(len(cols))
        self.preview.setHorizontalHeaderLabels(cols)
        show = rows[:10]
        self.preview.setRowCount(len(show))
        for i, r in enumerate(show):
            for j, c in enumerate(cols):
                self.preview.setItem(i, j, QTableWidgetItem(r.get(c, '')))
        self.preview.resizeColumnsToContents()

    def _on_load(self):
        profile = self.parent_dialog.active_profile
        if profile is None or not profile.database:
            # 프로파일 탭에서 현재 폼 값을 사용
            # (사용자가 테스트 누르지 않고 바로 탑재 시도 케이스)
            tab_pg = self.parent_dialog.tabs.widget(0)
            profile = tab_pg._current_profile()
            if not profile.database:
                QMessageBox.warning(self, '경고',
                                    '먼저 [1. PG 연결] 탭에서 연결 설정 필요')
                return
        if not self._cached_rows:
            QMessageBox.warning(self, '경고', '엑셀 파일을 먼저 선택하세요')
            return
        schema = self.schema_edit.text().strip() or 'public'
        table = self.table_edit.text().strip() or 'ri_status'
        self.log.append(f'[탑재 시작] {len(self._cached_rows)}행 → '
                        f'{profile.host}:{profile.port}/{profile.database} '
                        f'{schema}.{table}')
        QApplication.processEvents()
        try:
            result = excel_loader.upsert(
                profile, self._cached_rows,
                schema=schema, table=table)
            self.log.append(f'  ✅ 완료: affected={result["affected"]}행')
            if result.get('errors'):
                for e in result['errors']:
                    self.log.append(f'  ⚠ {e}')
        except Exception as e:
            self.log.append(f'  ❌ 실패: {e}')


# ============================================================
# Tab: 행정리 작업 (읍면동 리스트 + 더블클릭 줌 + 작업 시작/종료)
# ============================================================

class WorkListTab(QWidget):
    """화면정의서 S11-S13 대응 — 읍면동 작업리스트 + 지도 이동 + 레이어 제어.

    - bnd_adm_pg에서 읍면동 리스트 로드 (PG 쿼리)
    - 검색 + 더블클릭 = 맵 캔버스 줌
    - [작업 시작]: bnd_job_pg만 편집 가능, 나머지 readOnly
    - [작업 종료]: 편집 내역 저장 + 잠금 해제
    - (옵션) 워프 스캔 자동 로드 — admin 선택 시 해당 시트 레이어 추가
    """

    def __init__(self, parent_dialog):
        super().__init__()
        self.parent_dialog = parent_dialog
        self.iface = parent_dialog.iface
        self._bboxes = {}      # adm_cd → (xmin, ymin, xmax, ymax)
        self._current_admin = None
        self._work_snapshot = None  # 작업 시작 시 저장, 종료 시 복원
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        help_label = QLabel(
            '<i>읍면동 리스트 로드 후 <b>더블클릭</b>하면 맵이 해당 영역으로 이동합니다. '
            '<b>[작업 시작]</b>을 누르면 bnd_job_pg만 편집 가능, 나머지 레이어는 '
            '자동 잠금. <b>[작업 종료]</b>로 변경사항 저장+잠금 해제.</i>')
        help_label.setWordWrap(True)
        help_label.setStyleSheet(
            'QLabel { padding: 6px; background: #f0f0f0; border-radius: 3px; }')
        layout.addWidget(help_label)

        # 소스 테이블 + 워프 폴더
        src_box = QGroupBox('데이터 소스')
        src_form = QFormLayout(src_box)
        self.src_schema = QLineEdit('census_23p')
        self.src_table = QLineEdit('bnd_adm_pg')
        self.warped_dir = QLineEdit('')
        self.warped_dir.setPlaceholderText(
            '(선택) 워프 스캔 루트 폴더. admin 선택 시 시트 레이어 자동 로드')
        btn_browse = QPushButton('찾기')
        btn_browse.clicked.connect(self._browse_warped)

        src_form.addRow('bnd_adm_pg 스키마:', self.src_schema)
        src_form.addRow('bnd_adm_pg 테이블:', self.src_table)
        wrow = QHBoxLayout()
        wrow.addWidget(self.warped_dir, 1); wrow.addWidget(btn_browse)
        warp_w = QWidget(); warp_w.setLayout(wrow)
        src_form.addRow('워프 폴더:', warp_w)

        btn_refresh = QPushButton('리스트 로드')
        btn_refresh.clicked.connect(self._refresh)
        src_form.addRow(btn_refresh)
        layout.addWidget(src_box)

        # 검색 + 리스트
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel('검색:'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('읍면동 코드/명칭/시군구 (예: 21510110 / 기장읍)')
        self.search.textChanged.connect(self._on_search)
        search_row.addWidget(self.search)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ['읍면동 코드', '읍면동명', '시군구', '시도'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.currentCellChanged.connect(
            lambda r, *_: self._on_row_selected(r))
        self.table.setMinimumHeight(250)
        layout.addWidget(self.table, 1)

        # 작업 시작/종료
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton(
            '작업 시작 (bnd_job_pg 편집 활성, 기타 잠금)')
        self.btn_start.clicked.connect(self._on_start)
        self.btn_end = QPushButton('작업 종료 (저장 + 잠금 해제)')
        self.btn_end.clicked.connect(self._on_end)
        self.btn_end.setEnabled(False)
        btn_row.addWidget(self.btn_start); btn_row.addWidget(self.btn_end)
        layout.addLayout(btn_row)

        self.status = QLabel('리스트 미로드')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # --- 데이터 로드 ---

    def _get_profile(self):
        tab_pg = self.parent_dialog.tabs.widget(0)
        p = tab_pg._current_profile()
        return p if p.database else None

    def _browse_warped(self):
        d = QFileDialog.getExistingDirectory(
            self, '워프 스캔 루트 폴더 선택', self.warped_dir.text())
        if d:
            self.warped_dir.setText(d)

    def _refresh(self):
        profile = self._get_profile()
        if profile is None:
            QMessageBox.warning(self, '경고',
                                '[1. PG 연결] 탭에서 연결 설정 필요')
            return
        schema = self.src_schema.text().strip() or 'census_23p'
        table = self.src_table.text().strip() or 'bnd_adm_pg'
        try:
            admins = admin_list.load_admin_list(profile, schema, table)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'리스트 로드 실패: {e}')
            return
        self._bboxes = {}
        self.table.setRowCount(len(admins))
        for i, a in enumerate(admins):
            self.table.setItem(i, 0, QTableWidgetItem(a['adm_cd']))
            self.table.setItem(i, 1, QTableWidgetItem(a['adm_nm']))
            self.table.setItem(i, 2, QTableWidgetItem(a['sigungu_nm']))
            self.table.setItem(i, 3, QTableWidgetItem(a['sido_nm']))
            self._bboxes[a['adm_cd']] = (
                a['xmin'], a['ymin'], a['xmax'], a['ymax'])
        self.table.resizeColumnsToContents()
        self.status.setText(f'로드: {len(admins)}개 읍면동')

    # --- 검색 ---

    def _on_search(self, text):
        text = text.strip().lower()
        for r in range(self.table.rowCount()):
            if not text:
                self.table.setRowHidden(r, False); continue
            vals = ' '.join(
                self.table.item(r, c).text().lower() for c in range(4))
            self.table.setRowHidden(r, text not in vals)

    # --- 선택/맵 이동 ---

    def _on_row_selected(self, row):
        """싱글클릭 — 행 하이라이트만 (맵 이동 안 함)."""
        if row < 0:
            return
        item = self.table.item(row, 0)
        if item:
            self._current_admin = item.text()

    def _on_double_click(self, index):
        """더블클릭 — 맵 캔버스 줌 + (옵션) 워프 스캔 자동 로드."""
        row = index.row()
        cd = self.table.item(row, 0).text()
        bbox = self._bboxes.get(cd)
        if not bbox:
            return
        try:
            from qgis.core import QgsRectangle
            rect = QgsRectangle(bbox[0], bbox[1], bbox[2], bbox[3])
            canvas = self.iface.mapCanvas()
            canvas.setExtent(rect)
            canvas.refresh()
            self._current_admin = cd
            msg = f'맵 이동: {cd} ({self.table.item(row,1).text()})'
            # 워프 스캔 로드
            warp_root = self.warped_dir.text().strip()
            if warp_root:
                try:
                    layer_control.clear_warped_scans(
                        self.iface, exclude_admin=cd)
                    added = layer_control.load_warped_scans(
                        self.iface, cd, warp_root)
                    if added:
                        msg += f' | 워프 스캔 {len(added)}개 로드'
                except Exception as e:
                    msg += f' | 스캔 로드 오류: {e}'
            self.status.setText(msg)
        except Exception as e:
            self.status.setText(f'맵 이동 실패: {e}')

    # --- 작업 시작/종료 ---

    def _on_start(self):
        try:
            self._work_snapshot = layer_control.start_work_mode(self.iface)
            self.btn_start.setEnabled(False)
            self.btn_end.setEnabled(True)
            self.status.setText(
                '작업 시작 — bnd_job_pg 편집 가능, 나머지 readOnly')
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
                msg += f' | 오류: {len(errors)}건'
            self.status.setText(msg)
        except Exception as e:
            QMessageBox.critical(self, '오류', f'작업 종료 실패: {e}')


# ============================================================
# Tab: 플레이스홀더 — Phase 6에서 채워짐
# ============================================================

class PlaceholderTab(QWidget):
    def __init__(self, title, description):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'<h3>{title}</h3>'))
        lbl = QLabel(description)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            'QLabel { padding: 16px; color: #666; background: #f8f8f8; '
            'border-radius: 4px; }')
        layout.addWidget(lbl)
        layout.addStretch()


# ============================================================
# 메인 다이얼로그
# ============================================================

class DBEditorDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.active_profile = None
        self.setWindowTitle('GIS Scan Tools — DB 작업')
        self.resize(760, 620)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(PGConnectionTab(self), '1. PG 연결')
        self.tabs.addTab(ExcelLoadTab(self), '2. 엑셀 탑재')
        self.tabs.addTab(WorkListTab(self), '3. 행정리 작업')
        self.tabs.addTab(PlaceholderTab(
            '데이터 경량화 (Phase 6)',
            'QGIS simplify 래퍼. 구현 예정.'),
            '4. 경량화')
        layout.addWidget(self.tabs)

        # 닫기 버튼
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton('닫기')
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)
