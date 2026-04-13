"""GIS Scan Tools - 5-단계 파이프라인 UI

각 stage를 독립 탭으로 분리하여 단계별로 실행/오류 격리:
  Stage 1: 메인 PDF 좌표생성 (SHP 정합)
  Stage 2: 스캔 식별 (admin_code + sheet_id)
  Stage 3: 매칭 + 호모그래피 워핑
  Stage 4: 사분면 크롭 + 모자이크 병합
  Stage 5: 경계 검수
"""
import csv
import io
import os
import sys
import traceback

from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QWidget, QPushButton, QLabel, QLineEdit, QFileDialog,
    QTextEdit, QFormLayout, QMessageBox, QApplication,
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox,
    QSpinBox, QDoubleSpinBox,
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal


PLUGIN_DIR = os.path.dirname(__file__)


# ============================================================
# Worker Thread — 백그라운드 stage 실행
# ============================================================

class StageWorker(QThread):
    progress = pyqtSignal(str)        # 로그 라인
    finished_ok = pyqtSignal(dict)    # 결과 dict
    failed = pyqtSignal(str)          # 에러 메시지

    def __init__(self, stage_name, stage_main_callable, argv):
        super().__init__()
        self.stage_name = stage_name
        self.stage_main = stage_main_callable
        self.argv = argv

    def run(self):
        # stdout/stderr 캡처 → progress 시그널
        old_argv = sys.argv
        old_stdout, old_stderr = sys.stdout, sys.stderr
        buf = io.StringIO()
        try:
            sys.argv = [self.stage_name] + self.argv
            sys.stdout = sys.stderr = _LineEmitter(self.progress.emit, buf)
            self.stage_main()
            self.finished_ok.emit({'log': buf.getvalue()})
        except SystemExit as e:
            if e.code in (0, None):
                self.finished_ok.emit({'log': buf.getvalue()})
            else:
                self.failed.emit(f'SystemExit code={e.code}\n{buf.getvalue()}')
        except Exception as e:
            tb = traceback.format_exc()
            self.failed.emit(f'{e}\n{tb}\n--- stdout ---\n{buf.getvalue()}')
        finally:
            sys.argv = old_argv
            sys.stdout, sys.stderr = old_stdout, old_stderr


class _LineEmitter:
    """write 호출시 줄 단위로 emit하는 stdout 대체."""

    def __init__(self, emit_fn, buf):
        self.emit = emit_fn
        self.buf = buf
        self._line = ''

    def write(self, s):
        self.buf.write(s)
        self._line += s
        while '\n' in self._line:
            line, self._line = self._line.split('\n', 1)
            self.emit(line)

    def flush(self):
        pass


# ============================================================
# 공통 위젯 — 폴더/파일 입력 행
# ============================================================

class PathRow(QWidget):
    """라벨 + LineEdit + Browse 버튼."""

    def __init__(self, label, mode='dir', filter_str=''):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        btn = QPushButton('찾아보기')
        btn.setFixedWidth(80)
        btn.clicked.connect(self._browse)
        self.mode = mode
        self.filter_str = filter_str
        h.addWidget(QLabel(label), 0)
        h.addWidget(self.edit, 1)
        h.addWidget(btn, 0)

    def text(self):
        return self.edit.text().strip()

    def setText(self, s):
        self.edit.setText(s)

    def _browse(self):
        if self.mode == 'dir':
            p = QFileDialog.getExistingDirectory(self, '폴더 선택', self.text())
        elif self.mode == 'open':
            p, _ = QFileDialog.getOpenFileName(self, '파일 선택', self.text(),
                                                self.filter_str)
        else:
            p, _ = QFileDialog.getSaveFileName(self, '저장', self.text(),
                                                self.filter_str)
        if p:
            self.edit.setText(p)


# ============================================================
# Stage Tab — 공통 베이스
# ============================================================

class StageTab(QWidget):
    """5개 stage 탭의 공통 베이스."""

    stage_name = ''
    stage_module = None  # tools.stageX_*

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)

        # 입력 영역 (서브클래스가 채움)
        self.form_widget = QWidget()
        self.form_layout = QFormLayout(self.form_widget)
        layout.addWidget(self.form_widget)

        # 실행 + 로그 + 상태
        ctrl = QHBoxLayout()
        self.btn_run = QPushButton(f'{self.stage_name} 실행')
        self.btn_run.clicked.connect(self._on_run)
        self.btn_open_out = QPushButton('출력 폴더 열기')
        self.btn_open_out.clicked.connect(self._open_out_dir)
        self.btn_open_csv = QPushButton('상태 CSV 열기')
        self.btn_open_csv.clicked.connect(self._open_status_csv)
        ctrl.addWidget(self.btn_run)
        ctrl.addStretch()
        ctrl.addWidget(self.btn_open_out)
        ctrl.addWidget(self.btn_open_csv)
        layout.addLayout(ctrl)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(200)
        layout.addWidget(self.log, 1)

        self.status_table = QTableWidget()
        self.status_table.setMinimumHeight(150)
        layout.addWidget(self.status_table, 1)

        self.worker = None

        self.build_form()

    def build_form(self):
        """서브클래스에서 self.form_layout에 입력 행 추가."""
        raise NotImplementedError

    def get_argv(self):
        """서브클래스에서 stage 모듈에 넘길 argv 리스트 반환."""
        raise NotImplementedError

    def get_out_dir(self):
        """출력 폴더 경로 (서브클래스 override)."""
        return ''

    def get_status_csv_path(self):
        """상태 CSV 경로 (기본: out_dir/_status.csv)."""
        out = self.get_out_dir()
        return os.path.join(out, '_status.csv') if out else ''

    def _on_run(self):
        try:
            argv = self.get_argv()
        except ValueError as e:
            QMessageBox.warning(self, '입력 오류', str(e))
            return

        self.btn_run.setEnabled(False)
        self.log.clear()
        self.log.append(f'=== {self.stage_name} 시작 ===')
        self.log.append(f'argv: {" ".join(argv)}')

        self.worker = StageWorker(
            self.stage_name, self.stage_module.main, argv)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, line):
        self.log.append(line)
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum())
        QApplication.processEvents()

    def _on_done(self, result):
        self.log.append(f'\n=== {self.stage_name} 완료 ===')
        self.btn_run.setEnabled(True)
        self._load_status_csv()

    def _on_failed(self, msg):
        self.log.append(f'\n[ERROR]\n{msg}')
        self.btn_run.setEnabled(True)
        QMessageBox.critical(self, f'{self.stage_name} 실패', msg[:500])

    def _load_status_csv(self):
        p = self.get_status_csv_path()
        if not p or not os.path.exists(p):
            return
        with open(p, encoding='utf-8') as f:
            rows = list(csv.reader(f))
        if not rows:
            return
        hdr, body = rows[0], rows[1:]
        self.status_table.setColumnCount(len(hdr))
        self.status_table.setRowCount(len(body))
        self.status_table.setHorizontalHeaderLabels(hdr)
        for r, row in enumerate(body):
            for c, v in enumerate(row):
                item = QTableWidgetItem(v)
                if c < len(hdr) and 'status' in hdr[c].lower():
                    if v == 'OK':
                        item.setBackground(Qt.green)
                    elif v in ('FAIL', 'ERROR'):
                        item.setBackground(Qt.red)
                    elif v == 'WARN':
                        item.setBackground(Qt.yellow)
                self.status_table.setItem(r, c, item)
        self.status_table.resizeColumnsToContents()

    def _open_out_dir(self):
        p = self.get_out_dir()
        if p and os.path.isdir(p):
            os.system(f'xdg-open "{p}" &')
        else:
            QMessageBox.information(self, '안내', '출력 폴더가 아직 없습니다.')

    def _open_status_csv(self):
        p = self.get_status_csv_path()
        if p and os.path.exists(p):
            os.system(f'xdg-open "{p}" &')
        else:
            QMessageBox.information(self, '안내', 'CSV가 아직 없습니다.')


# ============================================================
# Stage 1
# ============================================================

class Stage1Tab(StageTab):
    stage_name = '[1] PDF 좌표생성'

    def __init__(self):
        from .tools import stage1_pdf_georef
        self.stage_module = stage1_pdf_georef
        super().__init__()

    def build_form(self):
        self.in_dir = PathRow('입력 PDF 폴더', 'dir')
        self.shp = PathRow('SHP 파일', 'open', 'Shapefile (*.shp)')
        self.out_dir = PathRow('출력 폴더', 'dir')
        self.cost_th = QDoubleSpinBox()
        self.cost_th.setRange(0.1, 100.0)
        self.cost_th.setValue(2.0)
        self.cost_th.setSingleStep(0.5)
        self.form_layout.addRow(self.in_dir)
        self.form_layout.addRow(self.shp)
        self.form_layout.addRow(self.out_dir)
        self.form_layout.addRow('cost 임계(px):', self.cost_th)

    def get_argv(self):
        if not self.in_dir.text():
            raise ValueError('입력 PDF 폴더 필수')
        if not self.shp.text():
            raise ValueError('SHP 파일 필수')
        if not self.out_dir.text():
            raise ValueError('출력 폴더 필수')
        return ['--in', self.in_dir.text(),
                '--shp', self.shp.text(),
                '--out', self.out_dir.text(),
                '--cost-threshold', str(self.cost_th.value())]

    def get_out_dir(self):
        return self.out_dir.text()


# ============================================================
# Stage 2
# ============================================================

class Stage2Tab(StageTab):
    stage_name = '[2] 스캔 식별'

    def __init__(self):
        from .tools import stage2_scan_identify
        self.stage_module = stage2_scan_identify
        super().__init__()

    def build_form(self):
        self.in_dir = PathRow('입력 스캔 폴더', 'dir')
        self.pdf_input = PathRow('원본 PDF 폴더 (메인+분할)', 'dir')
        self.pdf_main = PathRow('Stage 1 출력 폴더 (pdf_main_geo)', 'dir')
        self.out_dir = PathRow('출력 폴더', 'dir')
        self.fast = QCheckBox('OCR fast 모드 (1-variant)')
        self.copy_unmatched = QCheckBox('짝 못찾은 스캔을 _unmatched/에 복사')
        self.form_layout.addRow(self.in_dir)
        self.form_layout.addRow(self.pdf_input)
        self.form_layout.addRow(self.pdf_main)
        self.form_layout.addRow(self.out_dir)
        self.form_layout.addRow(self.fast)
        self.form_layout.addRow(self.copy_unmatched)

    def get_argv(self):
        for r, n in [(self.in_dir, '입력 스캔'), (self.pdf_input, '원본 PDF'),
                     (self.pdf_main, 'Stage 1 출력'), (self.out_dir, '출력')]:
            if not r.text():
                raise ValueError(f'{n} 폴더 필수')
        argv = ['--in', self.in_dir.text(),
                '--pdf-input', self.pdf_input.text(),
                '--pdf-main', self.pdf_main.text(),
                '--out', self.out_dir.text()]
        if self.fast.isChecked():
            argv.append('--fast')
        if self.copy_unmatched.isChecked():
            argv.append('--copy-unmatched')
        return argv

    def get_out_dir(self):
        return self.out_dir.text()

    def get_status_csv_path(self):
        return os.path.join(self.out_dir.text(), '_identification.csv') \
            if self.out_dir.text() else ''


# ============================================================
# Stage 3
# ============================================================

class Stage3Tab(StageTab):
    stage_name = '[3] 매칭+워핑'

    def __init__(self):
        from .tools import stage3_scan_warp
        self.stage_module = stage3_scan_warp
        super().__init__()

    def build_form(self):
        self.identification = PathRow(
            'Stage 2 _identification.csv', 'open', 'CSV (*.csv)')
        self.pdf_main = PathRow('Stage 1 출력 폴더', 'dir')
        self.out_dir = PathRow('출력 폴더', 'dir')
        self.no_intermediates = QCheckBox('중간산출 저장 안 함 (속도)')
        self.form_layout.addRow(self.identification)
        self.form_layout.addRow(self.pdf_main)
        self.form_layout.addRow(self.out_dir)
        self.form_layout.addRow(self.no_intermediates)

    def get_argv(self):
        for r, n in [(self.identification, 'identification CSV'),
                     (self.pdf_main, 'Stage 1 출력'),
                     (self.out_dir, '출력')]:
            if not r.text():
                raise ValueError(f'{n} 필수')
        argv = ['--identification', self.identification.text(),
                '--pdf-main', self.pdf_main.text(),
                '--out', self.out_dir.text()]
        if self.no_intermediates.isChecked():
            argv.append('--no-intermediates')
        return argv

    def get_out_dir(self):
        return self.out_dir.text()


# ============================================================
# Stage 4
# ============================================================

class Stage4Tab(StageTab):
    stage_name = '[4] 사분면 병합'

    def __init__(self):
        from .tools import stage4_merge
        self.stage_module = stage4_merge
        super().__init__()

    def build_form(self):
        self.warped = PathRow('Stage 3 출력 폴더', 'dir')
        self.sheet_bboxes = PathRow(
            'Stage 2 sheet_bboxes.json', 'open', 'JSON (*.json)')
        self.pdf_main = PathRow('Stage 1 출력 폴더', 'dir')
        self.out_dir = PathRow('출력 폴더', 'dir')
        self.form_layout.addRow(self.warped)
        self.form_layout.addRow(self.sheet_bboxes)
        self.form_layout.addRow(self.pdf_main)
        self.form_layout.addRow(self.out_dir)

    def get_argv(self):
        for r, n in [(self.warped, 'Stage 3 출력'),
                     (self.sheet_bboxes, 'sheet_bboxes.json'),
                     (self.pdf_main, 'Stage 1 출력'),
                     (self.out_dir, '출력')]:
            if not r.text():
                raise ValueError(f'{n} 필수')
        return ['--warped', self.warped.text(),
                '--sheet-bboxes', self.sheet_bboxes.text(),
                '--pdf-main', self.pdf_main.text(),
                '--out', self.out_dir.text()]

    def get_out_dir(self):
        return self.out_dir.text()


# ============================================================
# Stage 5
# ============================================================

class Stage5Tab(StageTab):
    stage_name = '[5] 경계 검수'

    def __init__(self):
        from .tools import stage5_validate
        self.stage_module = stage5_validate
        super().__init__()

    def build_form(self):
        self.merged = PathRow('Stage 4 출력 폴더', 'dir')
        self.shp = PathRow('SHP 파일', 'open', 'Shapefile (*.shp)')
        self.out_dir = PathRow('출력 폴더', 'dir')
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(1.0, 50.0)
        self.threshold.setValue(8.0)
        self.threshold.setSingleStep(1.0)
        self.form_layout.addRow(self.merged)
        self.form_layout.addRow(self.shp)
        self.form_layout.addRow(self.out_dir)
        self.form_layout.addRow('문제구간 임계(px):', self.threshold)

    def get_argv(self):
        for r, n in [(self.merged, 'Stage 4 출력'),
                     (self.shp, 'SHP 파일'),
                     (self.out_dir, '출력')]:
            if not r.text():
                raise ValueError(f'{n} 필수')
        return ['--merged', self.merged.text(),
                '--shp', self.shp.text(),
                '--out', self.out_dir.text(),
                '--threshold', str(self.threshold.value())]

    def get_out_dir(self):
        return self.out_dir.text()


# ============================================================
# 메인 다이얼로그
# ============================================================

class GISScanToolsDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle('GIS Scan Tools — 5-단계 파이프라인')
        self.resize(1100, 800)
        layout = QVBoxLayout(self)

        intro = QLabel(
            '<b>처리 흐름:</b> 각 단계를 위에서 아래 순서로 실행하세요. '
            '단계마다 출력 폴더가 다음 단계의 입력이 됩니다. '
            '오류가 나면 그 단계의 입력/로그/CSV로 원인 파악 후 단독 재실행할 수 있습니다.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        self.tabs.addTab(Stage1Tab(), '1. PDF 좌표생성')
        self.tabs.addTab(Stage2Tab(), '2. 스캔 식별')
        self.tabs.addTab(Stage3Tab(), '3. 매칭+워핑')
        self.tabs.addTab(Stage4Tab(), '4. 사분면 병합')
        self.tabs.addTab(Stage5Tab(), '5. 경계 검수')
        layout.addWidget(self.tabs, 1)


# ============================================================
# QGIS 플러그인 등록
# ============================================================

class GISScanToolsPlugin:
    """QGIS 플러그인 메인 클래스 — 툴바 + 다이얼로그."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(PLUGIN_DIR, 'resources', 'icon_georef.svg')
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self.action = QAction(icon, 'GIS Scan Tools (5-stage)',
                              self.iface.mainWindow())
        self.action.triggered.connect(self.show_dialog)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('&GIS Scan Tools', self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu('&GIS Scan Tools', self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.dialog:
            self.dialog.close()

    def show_dialog(self):
        if self.dialog is None:
            self.dialog = GISScanToolsDialog(self.iface,
                                             self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
