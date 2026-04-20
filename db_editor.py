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
    QTextEdit, QMessageBox, QGroupBox, QApplication,
)
from qgis.PyQt.QtCore import Qt

from .db_tools.pg_connection import (
    PGProfile, save_profile, load_profile, list_profiles, delete_profile,
    test_connection, SETTINGS_PREFIX,
)


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
# Tab: 플레이스홀더 — Phase 3~6에서 채워짐
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
        self.tabs.addTab(PlaceholderTab(
            '엑셀 → PostGIS 탑재 (Phase 3)',
            '행정리현황 엑셀을 PostGIS에 업서트합니다. 구현 예정.'),
            '2. 엑셀 탑재')
        self.tabs.addTab(PlaceholderTab(
            '행정리 작업 (Phase 4~6)',
            '읍면동 리스트 + 지도 연동 + 행정리 경계 편집. 구현 예정.'),
            '3. 행정리 작업')
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
