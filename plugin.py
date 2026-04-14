"""GIS Scan Tools - 5-단계 파이프라인 UI (공통입력 + 자동출력)

상단에 공통 입력 4가지를 한 번만 설정:
  - 입력 PDF 폴더
  - 입력 스캔 폴더
  - SHP 파일
  - 프로젝트 폴더 (산출물 루트)

각 stage는 실행만 — 하위 폴더(1_pdf_geo, 2_scan_id, ...)가 자동 생성.
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
    QTableWidget, QTableWidgetItem, QCheckBox, QDoubleSpinBox,
    QGroupBox,
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal


PLUGIN_DIR = os.path.dirname(__file__)

# 자동 서브폴더 이름
SUB_PDF_GEO = '1_pdf_geo'
SUB_SCAN_ID = '2_scan_id'
SUB_WARPED = '3_warped'
SUB_MERGED = '4_merged'
SUB_VALIDATION = '5_validation'


# ============================================================
# Worker Thread
# ============================================================

class StageWorker(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, stage_name, stage_main_callable, argv):
        super().__init__()
        self.stage_name = stage_name
        self.stage_main = stage_main_callable
        self.argv = argv

    def run(self):
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
# 공통 입력 위젯
# ============================================================

class PathRow(QWidget):
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
        else:
            p, _ = QFileDialog.getOpenFileName(self, '파일 선택', self.text(),
                                                self.filter_str)
        if p:
            self.edit.setText(p)


class CommonInputs(QGroupBox):
    """모든 stage가 공유하는 4가지 입력."""

    def __init__(self):
        super().__init__('공통 설정 (한 번만 입력하세요)')
        v = QVBoxLayout(self)
        self.pdf_input = PathRow('입력 PDF 폴더 (메인+분할 함께)', 'dir')
        self.scan_input = PathRow('입력 스캔 폴더', 'dir')
        self.shp = PathRow('SHP 파일', 'open', 'Shapefile (*.shp)')
        self.project_dir = PathRow('프로젝트 폴더 (모든 산출물 저장 루트)', 'dir')
        v.addWidget(self.pdf_input)
        v.addWidget(self.scan_input)
        v.addWidget(self.shp)
        v.addWidget(self.project_dir)

        note = QLabel(
            '<i>프로젝트 폴더 밑에 자동 생성됨:  '
            '1_pdf_geo / 2_scan_id / 3_warped / 4_merged / 5_validation</i>')
        note.setWordWrap(True)
        v.addWidget(note)

    def validate(self, need_pdf=True, need_scan=True, need_shp=True):
        if need_pdf and not self.pdf_input.text():
            raise ValueError('입력 PDF 폴더 지정 필요')
        if need_scan and not self.scan_input.text():
            raise ValueError('입력 스캔 폴더 지정 필요')
        if need_shp and not self.shp.text():
            raise ValueError('SHP 파일 지정 필요')
        if not self.project_dir.text():
            raise ValueError('프로젝트 폴더 지정 필요')

    def sub(self, name):
        """프로젝트 하위 자동 서브폴더 경로."""
        return os.path.join(self.project_dir.text(), name)


# ============================================================
# Stage Tab 공통 베이스 — 실행/로그/상태표만
# ============================================================

class StageTab(QWidget):
    stage_name = ''
    stage_module = None

    def __init__(self, common: CommonInputs):
        super().__init__()
        self.common = common
        layout = QVBoxLayout(self)

        # 옵션 영역 (stage별 고유 옵션만)
        self.opt_widget = QWidget()
        self.opt_layout = QFormLayout(self.opt_widget)
        layout.addWidget(self.opt_widget)

        # 입출력 요약 라벨
        self.io_label = QLabel()
        self.io_label.setWordWrap(True)
        self.io_label.setStyleSheet('QLabel { color: #444; padding: 4px; '
                                    'background: #f0f0f0; border-radius: 3px; }')
        layout.addWidget(self.io_label)

        # 실행 버튼
        ctrl = QHBoxLayout()
        self.btn_run = QPushButton(f'{self.stage_name} 실행')
        self.btn_run.clicked.connect(self._on_run)
        self.btn_stop = QPushButton('중단')
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_open_out = QPushButton('출력 폴더 열기')
        self.btn_open_out.clicked.connect(self._open_out_dir)
        self.btn_open_csv = QPushButton('상태 CSV 열기')
        self.btn_open_csv.clicked.connect(self._open_status_csv)
        ctrl.addWidget(self.btn_run)
        ctrl.addWidget(self.btn_stop)
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
        self.build_options()

        # 공통입력 변경 시 io_label 업데이트
        self.common.pdf_input.edit.textChanged.connect(self._update_io_label)
        self.common.scan_input.edit.textChanged.connect(self._update_io_label)
        self.common.shp.edit.textChanged.connect(self._update_io_label)
        self.common.project_dir.edit.textChanged.connect(self._update_io_label)
        self._update_io_label()

    def build_options(self):
        """서브클래스 override — stage별 옵션 위젯 추가."""
        pass

    def _update_io_label(self):
        try:
            ins, out = self.io_summary()
        except Exception:
            ins, out = ([], '(프로젝트 폴더 지정 전)')
        lines = []
        for k, v in ins:
            lines.append(f'<b>입력:</b> {k} = <code>{v or "?"}</code>')
        lines.append(f'<b>출력:</b> <code>{out}</code>')
        self.io_label.setText('<br>'.join(lines))

    def io_summary(self):
        """(입력 튜플 리스트, 출력 경로) — 서브클래스 override."""
        return ([], self.get_out_dir())

    def get_argv(self):
        raise NotImplementedError

    def get_out_dir(self):
        return ''

    def get_status_csv_path(self):
        out = self.get_out_dir()
        return os.path.join(out, '_status.csv') if out else ''

    def _on_run(self):
        try:
            argv = self.get_argv()
        except ValueError as e:
            QMessageBox.warning(self, '입력 오류', str(e))
            return

        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.log.clear()
        self.log.append(f'=== {self.stage_name} 시작 ===')
        self.log.append(f'argv: {" ".join(argv)}')

        self.worker = StageWorker(
            self.stage_name, self.stage_module.main, argv)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_stop(self):
        if not self.worker or not self.worker.isRunning():
            return
        if QMessageBox.question(
                self, '중단 확인',
                '실행 중인 작업을 강제 중단합니다.\n진행 중인 항목은 손실됩니다.'
        ) != QMessageBox.Yes:
            return
        self.worker.terminate()
        self.worker.wait(2000)
        self.log.append('\n[중단됨]')
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _on_progress(self, line):
        self.log.append(line)
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum())
        QApplication.processEvents()

    def _on_done(self, result):
        self.log.append(f'\n=== {self.stage_name} 완료 ===')
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._load_status_csv()

    def _on_failed(self, msg):
        self.log.append(f'\n[ERROR]\n{msg}')
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
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
            if sys.platform == 'win32':
                os.startfile(p)
            else:
                os.system(f'xdg-open "{p}" &')
        else:
            QMessageBox.information(self, '안내', '출력 폴더가 아직 없습니다.')

    def _open_status_csv(self):
        p = self.get_status_csv_path()
        if p and os.path.exists(p):
            if sys.platform == 'win32':
                os.startfile(p)
            else:
                os.system(f'xdg-open "{p}" &')
        else:
            QMessageBox.information(self, '안내', 'CSV가 아직 없습니다.')


# ============================================================
# Stage 1
# ============================================================

class Stage1Tab(StageTab):
    stage_name = '[1] PDF 좌표생성'

    def __init__(self, common):
        from .tools import stage1_pdf_georef
        self.stage_module = stage1_pdf_georef
        super().__init__(common)

    def build_options(self):
        self.cost_th = QDoubleSpinBox()
        self.cost_th.setRange(0.1, 100.0)
        self.cost_th.setValue(2.0)
        self.cost_th.setSingleStep(0.5)
        self.opt_layout.addRow('cost 임계(px):', self.cost_th)

    def io_summary(self):
        return ([('PDF 폴더', self.common.pdf_input.text()),
                 ('SHP', self.common.shp.text())],
                self.get_out_dir())

    def get_out_dir(self):
        p = self.common.project_dir.text()
        return os.path.join(p, SUB_PDF_GEO) if p else ''

    def get_argv(self):
        self.common.validate(need_pdf=True, need_scan=False, need_shp=True)
        return ['--in', self.common.pdf_input.text(),
                '--shp', self.common.shp.text(),
                '--out', self.get_out_dir(),
                '--cost-threshold', str(self.cost_th.value())]


# ============================================================
# Stage 2
# ============================================================

class Stage2Tab(StageTab):
    stage_name = '[2] 스캔 식별'

    def __init__(self, common):
        from .tools import stage2_scan_identify
        self.stage_module = stage2_scan_identify
        super().__init__(common)

    def build_options(self):
        self.fast = QCheckBox('OCR fast 모드 (1-variant)')
        self.copy_unmatched = QCheckBox('짝 못찾은 스캔을 _unmatched/에 복사')
        self.opt_layout.addRow(self.fast)
        self.opt_layout.addRow(self.copy_unmatched)

    def io_summary(self):
        return ([('스캔', self.common.scan_input.text()),
                 ('PDF', self.common.pdf_input.text()),
                 ('Stage 1 출력', self.common.sub(SUB_PDF_GEO)
                  if self.common.project_dir.text() else '')],
                self.get_out_dir())

    def get_out_dir(self):
        p = self.common.project_dir.text()
        return os.path.join(p, SUB_SCAN_ID) if p else ''

    def get_status_csv_path(self):
        return os.path.join(self.get_out_dir(), '_identification.csv') \
            if self.get_out_dir() else ''

    def get_argv(self):
        self.common.validate(need_pdf=True, need_scan=True, need_shp=False)
        argv = ['--in', self.common.scan_input.text(),
                '--pdf-input', self.common.pdf_input.text(),
                '--pdf-main', self.common.sub(SUB_PDF_GEO),
                '--out', self.get_out_dir()]
        if self.fast.isChecked():
            argv.append('--fast')
        if self.copy_unmatched.isChecked():
            argv.append('--copy-unmatched')
        return argv


# ============================================================
# Stage 3
# ============================================================

class Stage3Tab(StageTab):
    stage_name = '[3] 매칭+워핑'

    def __init__(self, common):
        from .tools import stage3_scan_warp
        self.stage_module = stage3_scan_warp
        super().__init__(common)

    def build_options(self):
        self.no_intermediates = QCheckBox('중간산출 저장 안 함 (속도)')
        self.opt_layout.addRow(self.no_intermediates)

    def io_summary(self):
        proj = self.common.project_dir.text()
        return ([('Stage 2 CSV', os.path.join(
                    self.common.sub(SUB_SCAN_ID),
                    '_identification.csv') if proj else ''),
                 ('Stage 1 출력', self.common.sub(SUB_PDF_GEO)
                  if proj else '')],
                self.get_out_dir())

    def get_out_dir(self):
        p = self.common.project_dir.text()
        return os.path.join(p, SUB_WARPED) if p else ''

    def get_argv(self):
        self.common.validate(need_pdf=False, need_scan=False, need_shp=False)
        argv = ['--identification',
                os.path.join(self.common.sub(SUB_SCAN_ID),
                             '_identification.csv'),
                '--pdf-main', self.common.sub(SUB_PDF_GEO),
                '--out', self.get_out_dir()]
        if self.no_intermediates.isChecked():
            argv.append('--no-intermediates')
        return argv


# ============================================================
# Stage 4
# ============================================================

class Stage4Tab(StageTab):
    stage_name = '[4] 사분면 병합'

    def __init__(self, common):
        from .tools import stage4_merge
        self.stage_module = stage4_merge
        super().__init__(common)

    def io_summary(self):
        proj = self.common.project_dir.text()
        return ([('Stage 3 출력', self.common.sub(SUB_WARPED)
                  if proj else ''),
                 ('sheet_bboxes', os.path.join(
                    self.common.sub(SUB_SCAN_ID),
                    'sheet_bboxes.json') if proj else ''),
                 ('Stage 1 출력', self.common.sub(SUB_PDF_GEO)
                  if proj else '')],
                self.get_out_dir())

    def get_out_dir(self):
        p = self.common.project_dir.text()
        return os.path.join(p, SUB_MERGED) if p else ''

    def get_argv(self):
        self.common.validate(need_pdf=False, need_scan=False, need_shp=False)
        return ['--warped', self.common.sub(SUB_WARPED),
                '--sheet-bboxes', os.path.join(
                    self.common.sub(SUB_SCAN_ID),
                    'sheet_bboxes.json'),
                '--pdf-main', self.common.sub(SUB_PDF_GEO),
                '--out', self.get_out_dir()]


# ============================================================
# Stage 5
# ============================================================

class Stage5Tab(StageTab):
    stage_name = '[5] 경계 검수'

    def __init__(self, common):
        from .tools import stage5_validate
        self.stage_module = stage5_validate
        super().__init__(common)

    def build_options(self):
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(1.0, 50.0)
        self.threshold.setValue(8.0)
        self.threshold.setSingleStep(1.0)
        self.opt_layout.addRow('문제구간 임계(px):', self.threshold)

    def io_summary(self):
        proj = self.common.project_dir.text()
        return ([('Stage 4 출력', self.common.sub(SUB_MERGED)
                  if proj else ''),
                 ('SHP', self.common.shp.text())],
                self.get_out_dir())

    def get_out_dir(self):
        p = self.common.project_dir.text()
        return os.path.join(p, SUB_VALIDATION) if p else ''

    def get_argv(self):
        self.common.validate(need_pdf=False, need_scan=False, need_shp=True)
        return ['--merged', self.common.sub(SUB_MERGED),
                '--shp', self.common.shp.text(),
                '--out', self.get_out_dir(),
                '--threshold', str(self.threshold.value())]


# ============================================================
# 메인 다이얼로그
# ============================================================

class GISScanToolsDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle('GIS Scan Tools — 5-단계 파이프라인')
        self.resize(1100, 900)
        layout = QVBoxLayout(self)

        intro = QLabel(
            '공통 설정을 먼저 채운 뒤, 1 → 2 → 3 → 4 순서로 탭에서 실행하세요. '
            '각 stage는 프로젝트 폴더 밑에 자동으로 하위 폴더를 만들어 산출합니다.')
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.common = CommonInputs()
        layout.addWidget(self.common)

        self.tabs = QTabWidget()
        self.tabs.addTab(Stage1Tab(self.common), '1. PDF 좌표생성')
        self.tabs.addTab(Stage2Tab(self.common), '2. 스캔 식별')
        self.tabs.addTab(Stage3Tab(self.common), '3. 매칭+워핑')
        self.tabs.addTab(Stage4Tab(self.common), '4. 사분면 병합')
        self.tabs.addTab(Stage5Tab(self.common), '5. 경계 검수')
        layout.addWidget(self.tabs, 1)


# ============================================================
# QGIS 플러그인 등록
# ============================================================

class GISScanToolsPlugin:
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
