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
    QGroupBox, QComboBox, QListWidget, QListWidgetItem,
    QCompleter, QSpinBox,
)
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QSize


PLUGIN_DIR = os.path.dirname(__file__)

# 자동 서브폴더 이름
SUB_PDF_GEO = '1_pdf_geo'
SUB_SCAN_ID = '2_scan_id'
SUB_MAP_EXTRACTED = '3_map_extracted'   # 참조 템플릿 매칭 기반 지도영역 추출
SUB_WARPED = '4_warped'
SUB_MERGED = '5_merged'
SUB_VALIDATION = '6_validation'


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

        # 수동 JGW 가져오기 — Stage 1 실패 admin 수작업 복구
        self.btn_import_jgw = QPushButton(
            '외부 JGW 가져오기 (자동 정합 실패 admin 복구용)')
        self.btn_import_jgw.clicked.connect(self._import_jgw)
        self.opt_layout.addRow(self.btn_import_jgw)

    def _import_jgw(self):
        """사용자가 외부(QGIS Georeferencer 등)에서 만든 JGW를 가져옴.

        파일명에서 admin_code 추출({8자리}.jgw) →
        같은 이름의 .jpg, .prj 도 함께 가져와 1_pdf_geo/에 복사.
        """
        if not self.common.project_dir.text():
            QMessageBox.warning(self, '경고', '프로젝트 폴더 지정 필요')
            return
        jgw_path, _ = QFileDialog.getOpenFileName(
            self, '외부 JGW 파일 선택', '',
            'JGW (*.jgw *.jgW *.JGW);;모든 파일 (*)')
        if not jgw_path:
            return
        import re as _re
        base = os.path.splitext(os.path.basename(jgw_path))[0]
        m = _re.match(r'^(\d{8})$', base)
        if not m:
            QMessageBox.warning(
                self, '파일명 오류',
                f'파일명이 8자리 행정코드 형식이어야 합니다: {base}.jgw\n'
                f'예: 39010320.jgw')
            return
        code = m.group(1)
        out_dir = self.get_out_dir()
        os.makedirs(out_dir, exist_ok=True)

        import shutil as _sh
        src_base = os.path.splitext(jgw_path)[0]
        copied = []
        errors = []
        # jgw 복사
        try:
            _sh.copy2(jgw_path, os.path.join(out_dir, f'{code}.jgw'))
            copied.append(f'{code}.jgw')
        except Exception as e:
            errors.append(f'jgw: {e}')
        # 옆에 있는 prj, jpg 복사 (있으면)
        for ext in ('.prj', '.jpg', '.jpeg', '.tif', '.tiff'):
            for e in (ext, ext.upper()):
                src = src_base + e
                if os.path.exists(src):
                    try:
                        _sh.copy2(src, os.path.join(
                            out_dir, f'{code}{ext.lower()}'))
                        copied.append(f'{code}{ext.lower()}')
                    except Exception as ex:
                        errors.append(f'{e}: {ex}')
                    break
        # prj 없으면 기본 EPSG:5179 생성
        prj_out = os.path.join(out_dir, f'{code}.prj')
        if not os.path.exists(prj_out):
            try:
                from .tools._legacy.common import PRJ_5179
                with open(prj_out, 'w') as f:
                    f.write(PRJ_5179)
                copied.append(f'{code}.prj (기본 EPSG:5179)')
            except Exception as e:
                errors.append(f'prj 기본 작성: {e}')

        msg = f'복사 완료:\n' + '\n'.join(f'  {c}' for c in copied)
        if errors:
            msg += '\n\n오류:\n' + '\n'.join(f'  {e}' for e in errors)
        QMessageBox.information(self, '외부 JGW 가져오기', msg)

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
        self.no_rename = QCheckBox('성공 스캔 표준명 복사 안 함 '
                                   '(기본: identified/에 {admin}_{sheet} 이름으로 복사)')
        self.no_unmatched = QCheckBox('실패 스캔 격리 안 함 '
                                      '(기본: _unmatched/에 복사)')
        self.opt_layout.addRow(self.no_rename)
        self.opt_layout.addRow(self.no_unmatched)

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
        # SHP는 옵션 — 지정 시 OCR 회수율 향상 (자릿수 fuzzy + 한글명 lookup)
        self.common.validate(need_pdf=True, need_scan=True, need_shp=False)
        argv = ['--in', self.common.scan_input.text(),
                '--pdf-input', self.common.pdf_input.text(),
                '--pdf-main', self.common.sub(SUB_PDF_GEO),
                '--out', self.get_out_dir()]
        if self.common.shp.text():
            argv += ['--shp', self.common.shp.text()]
        if self.no_rename.isChecked():
            argv.append('--no-rename')
        if self.no_unmatched.isChecked():
            argv.append('--no-unmatched')
        return argv


# ============================================================
# 미식별 보강 (Stage 2 FAIL 수동 복구)
# ============================================================

class RecoveryTab(QWidget):
    """_unmatched/ 파일에 admin_code + sheet_id를 수동 지정 → identified/로 이동.

    CSV 편집 없이 드롭다운 UI로 간단 처리.
    """

    def __init__(self, common):
        super().__init__()
        self.common = common
        self._shp_cache = None   # (codes_list, code→name 맵)
        self._pdf_cache = None   # {admin_code: [sheet_id list]}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        help_label = QLabel(
            '<i>Stage 2 OCR이 실패한 파일을 수동 지정합니다. '
            '왼쪽 리스트에서 파일 선택 → 행정코드·시트번호 지정 → '
            '[저장]. 표준명으로 identified/ 폴더에 복사되어 다음 stage가 '
            '자동 인식합니다.</i>')
        help_label.setWordWrap(True)
        help_label.setStyleSheet(
            'QLabel { padding: 4px; background: #f0f0f0; border-radius: 3px; }')
        layout.addWidget(help_label)

        # 새로고침 + 출력경로
        top = QHBoxLayout()
        self.btn_refresh = QPushButton('리스트 새로고침')
        self.btn_refresh.clicked.connect(self.refresh_list)
        top.addWidget(self.btn_refresh)
        top.addStretch()
        self.count_label = QLabel('미식별: 0건')
        top.addWidget(self.count_label)
        layout.addLayout(top)

        # 좌(파일 리스트) + 우(편집 폼)
        body = QHBoxLayout()
        self.file_list = QListWidget()
        self.file_list.setMinimumWidth(300)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        body.addWidget(self.file_list, 2)

        right = QVBoxLayout()
        self.thumb = QLabel('파일 선택')
        self.thumb.setMinimumSize(400, 300)
        self.thumb.setStyleSheet(
            'QLabel { background: #ddd; border: 1px solid #888; }')
        self.thumb.setAlignment(Qt.AlignCenter)
        right.addWidget(self.thumb)

        form = QFormLayout()
        self.admin_cb = QComboBox()
        self.admin_cb.setEditable(True)  # 검색 가능
        self.admin_cb.setInsertPolicy(QComboBox.NoInsert)
        self.admin_cb.currentIndexChanged.connect(self._on_admin_changed)
        form.addRow('행정코드:', self.admin_cb)

        self.sheet_cb = QComboBox()
        # PDF 후보 외 임의값 입력 허용 (N 무관, PDF 없는 admin 대응)
        self.sheet_cb.setEditable(True)
        form.addRow('시트번호:', self.sheet_cb)
        right.addLayout(form)

        btn_row = QHBoxLayout()
        self.btn_save = QPushButton('저장 (identified/로 복사)')
        self.btn_save.clicked.connect(self._on_save)
        self.btn_skip = QPushButton('건너뜀')
        self.btn_skip.clicked.connect(self._on_skip)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_skip)
        right.addLayout(btn_row)

        self.status_label = QLabel('')
        self.status_label.setWordWrap(True)
        right.addWidget(self.status_label)
        right.addStretch()

        body.addLayout(right, 3)
        layout.addLayout(body)

    # --- 데이터 로드 ---

    def _load_shp_codes(self):
        if self._shp_cache is not None:
            return self._shp_cache
        shp_path = self.common.shp.text()
        if not shp_path or not os.path.exists(shp_path):
            return None, None
        try:
            import geopandas as gpd
            try:
                gdf = gpd.read_file(shp_path, encoding='cp949')
            except Exception:
                gdf = gpd.read_file(shp_path)
            items = []  # (display_text, admin_code)
            nm_map = {}
            for _, r in gdf.iterrows():
                cd = str(r.get('adm_cd', '')).strip()
                nm = str(r.get('adm_nm', '')).strip()
                if not cd.isdigit() or len(cd) != 8:
                    continue
                items.append((f'{cd}  {nm}', cd))
                nm_map[cd] = nm
            items.sort(key=lambda x: x[1])
            self._shp_cache = (items, nm_map)
            return items, nm_map
        except Exception as e:
            self.status_label.setText(f'SHP 로드 실패: {e}')
            return None, None

    def _load_pdf_sheets(self):
        """{admin_code: [sheet_id list]} — PDF 폴더에서 분할 PDF 스캔."""
        if self._pdf_cache is not None:
            return self._pdf_cache
        pdf_dir = self.common.pdf_input.text()
        if not pdf_dir or not os.path.exists(pdf_dir):
            return {}
        import re as _re
        pat = _re.compile(r'^(\d{8})_(\d+)-(\d+)\.pdf$')
        result = {}
        for root, _, files in os.walk(pdf_dir):
            for f in files:
                m = pat.match(f)
                if not m:
                    continue
                admin = m.group(1)
                sid = f'{m.group(2)}-{m.group(3)}'
                result.setdefault(admin, set()).add(sid)
        # set → sorted list
        self._pdf_cache = {k: sorted(v, key=lambda s: (
            int(s.split('-')[0]), int(s.split('-')[1]))) for k, v in result.items()}
        return self._pdf_cache

    def _unmatched_dir(self):
        p = self.common.project_dir.text()
        return os.path.join(p, SUB_SCAN_ID, '_unmatched') if p else ''

    def _identified_dir(self):
        p = self.common.project_dir.text()
        return os.path.join(p, SUB_SCAN_ID, 'identified') if p else ''

    # --- UI 이벤트 ---

    def refresh_list(self):
        self.file_list.clear()
        self.thumb.setText('파일 선택')
        self.status_label.setText('')
        self._shp_cache = None  # SHP 경로 바뀌었을 수 있음
        self._pdf_cache = None

        # admin 드롭다운 채우기
        self.admin_cb.clear()
        shp_items, _ = self._load_shp_codes()
        if shp_items:
            for disp, cd in shp_items:
                self.admin_cb.addItem(disp, cd)
            # 자동완성
            completer = QCompleter([x[0] for x in shp_items], self.admin_cb)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            self.admin_cb.setCompleter(completer)
        else:
            self.status_label.setText(
                'SHP 미설정 또는 로드 실패 — 상단 공통설정에서 SHP 지정 필요')

        # 미식별 파일 리스트
        d = self._unmatched_dir()
        if not d or not os.path.isdir(d):
            self.count_label.setText('미식별 폴더 없음 (Stage 2 먼저 실행)')
            return
        files = sorted(
            f for f in os.listdir(d)
            if f.lower().endswith(('.jpg', '.jpeg', '.png')))
        for f in files:
            item = QListWidgetItem(f)
            item.setData(Qt.UserRole, os.path.join(d, f))
            self.file_list.addItem(item)
        self.count_label.setText(f'미식별: {len(files)}건')

    def _on_file_selected(self, item, _prev=None):
        if not item:
            return
        path = item.data(Qt.UserRole)
        # 썸네일 — OCR에 쓰는 헤더 영역만 (admin_code + sheet_id 모두 포함).
        # y: 0~22%, x: 0~55% (admin crop_header 18%/40% + sheet ROI 20%/18% 합집합 + 여유)
        pm = self._load_header_thumbnail(path)
        if pm is None or pm.isNull():
            # 폴백: 전체 이미지
            pm = QPixmap(path)
        if not pm.isNull():
            self.thumb.setPixmap(pm.scaled(
                self.thumb.size(), Qt.KeepAspectRatio,
                Qt.SmoothTransformation))
        else:
            self.thumb.setText(f'이미지 로드 실패: {os.path.basename(path)}')
        self.status_label.setText(f'선택: {os.path.basename(path)}')

    def _load_header_thumbnail(self, path):
        """스캔에서 헤더 영역만 크롭해 QPixmap 반환 (OCR ROI와 동일 영역)."""
        try:
            import cv2
            import numpy as np
            data = np.fromfile(path, dtype=np.uint8)
            if data.size == 0:
                return None
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is None:
                return None
            h, w = img.shape[:2]
            crop = img[:int(h * 0.22), :int(w * 0.55)]
            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            ch, cw = rgb.shape[:2]
            from qgis.PyQt.QtGui import QImage
            qimg = QImage(bytes(rgb.data), cw, ch, 3 * cw,
                          QImage.Format_RGB888)
            return QPixmap.fromImage(qimg)
        except Exception:
            return None

    def _on_admin_changed(self, _idx):
        self.sheet_cb.clear()
        code = self.admin_cb.currentData()
        if not code:
            return
        sheets_map = self._load_pdf_sheets()
        sheets = sheets_map.get(code, [])
        if sheets:
            self.sheet_cb.addItems(sheets)
        else:
            # PDF 없는 admin — 일반 후보 + 자유 입력 안내
            self.sheet_cb.addItem('')
            self.sheet_cb.lineEdit().setPlaceholderText(
                '예: 4-1, 7-5 — 직접 입력 가능')
        # 현재 선택된 파일명에서 sheet 힌트 추출 — 우선 선택
        item = self.file_list.currentItem()
        if item:
            import re as _re
            m = _re.search(r'(\d+-\d+)', os.path.basename(
                item.data(Qt.UserRole)))
            if m:
                hint = m.group(1)
                idx = self.sheet_cb.findText(hint)
                if idx >= 0:
                    self.sheet_cb.setCurrentIndex(idx)
                else:
                    self.sheet_cb.setEditText(hint)

    def _on_save(self):
        item = self.file_list.currentItem()
        if not item:
            self.status_label.setText('파일을 선택하세요')
            return
        src = item.data(Qt.UserRole)
        code = self.admin_cb.currentData()
        sid = self.sheet_cb.currentText()
        if not code or not sid or '-' not in sid:
            self.status_label.setText('행정코드·시트번호를 올바르게 선택하세요')
            return
        ext = os.path.splitext(src)[1] or '.jpg'
        sub_dir = os.path.join(self._identified_dir(), code[:2], code[:5])
        os.makedirs(sub_dir, exist_ok=True)
        dst = os.path.join(sub_dir, f'{code}_{sid}{ext}')
        # 중복 방지
        k = 2
        while os.path.exists(dst):
            dst = os.path.join(sub_dir, f'{code}_{sid}_{k}{ext}')
            k += 1
        import shutil as _sh
        try:
            _sh.copy2(src, dst)
            self.status_label.setText(
                f'저장 완료: {dst} — 다음 Stage 실행 시 자동 처리됩니다.')
            # 리스트에서 현재 항목 제거
            row = self.file_list.currentRow()
            self.file_list.takeItem(row)
            self.count_label.setText(f'미식별: {self.file_list.count()}건')
        except Exception as e:
            self.status_label.setText(f'저장 실패: {e}')

    def _on_skip(self):
        row = self.file_list.currentRow()
        if row >= 0:
            next_row = min(row + 1, self.file_list.count() - 1)
            self.file_list.setCurrentRow(next_row)


# ============================================================
# 지도영역 추출 (화면정의서 S7) — Stage 2와 Stage 3 사이의 독립 탭
# ============================================================

class ExtractMapTab(StageTab):
    stage_name = '[3] 지도영역 추출'

    def __init__(self, common):
        from .tools import stage_extract_map
        self.stage_module = stage_extract_map
        super().__init__(common)

    def build_options(self):
        pass  # 파라미터 없음 (프레임선 색 매칭 자동)

    def io_summary(self):
        proj = self.common.project_dir.text()
        return ([('Stage 2 identified/', os.path.join(
                    self.common.sub(SUB_SCAN_ID),
                    'identified') if proj else '')],
                self.get_out_dir())

    def get_out_dir(self):
        p = self.common.project_dir.text()
        return os.path.join(p, SUB_MAP_EXTRACTED) if p else ''

    def get_argv(self):
        self.common.validate(need_pdf=False, need_scan=False, need_shp=False)
        return ['--identified',
                os.path.join(self.common.sub(SUB_SCAN_ID), 'identified'),
                '--out', self.get_out_dir()]


# ============================================================
# Stage 3
# ============================================================

class Stage3Tab(StageTab):
    stage_name = '[4] 매칭정합'

    def __init__(self, common):
        from .tools import stage3_scan_warp
        self.stage_module = stage3_scan_warp
        super().__init__(common)

    def build_options(self):
        self.no_intermediates = QCheckBox('중간산출 저장 안 함 (속도)')
        self.opt_layout.addRow(self.no_intermediates)

    def io_summary(self):
        proj = self.common.project_dir.text()
        return ([('Stage 3 map_extracted/', self.common.sub(SUB_MAP_EXTRACTED)
                  if proj else ''),
                 ('Stage 2 sheets_geo', os.path.join(
                    self.common.sub(SUB_SCAN_ID),
                    'sheets_geo') if proj else '')],
                self.get_out_dir())

    def get_out_dir(self):
        p = self.common.project_dir.text()
        return os.path.join(p, SUB_WARPED) if p else ''

    def get_argv(self):
        # 입력: Stage 3 map_extracted/ 폴더 (프레임 제거된 스캔)
        self.common.validate(need_pdf=False, need_scan=False, need_shp=False)
        argv = ['--identified', self.common.sub(SUB_MAP_EXTRACTED),
                '--sheets-geo', os.path.join(
                    self.common.sub(SUB_SCAN_ID), 'sheets_geo'),
                '--out', self.get_out_dir()]
        if self.no_intermediates.isChecked():
            argv.append('--no-intermediates')
        return argv


# ============================================================
# Stage 4
# ============================================================

class Stage4Tab(StageTab):
    stage_name = '[5] 사분면 병합'

    def __init__(self, common):
        from .tools import stage4_merge
        self.stage_module = stage4_merge
        super().__init__(common)

    def build_options(self):
        from qgis.PyQt.QtWidgets import QSpinBox
        self.inner_margin = QSpinBox()
        self.inner_margin.setRange(0, 200); self.inner_margin.setValue(0)
        self.opt_layout.addRow(
            '시트 안쪽 여유(px) — 경계 부정확 영역 제거:', self.inner_margin)

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
        argv = ['--warped', self.common.sub(SUB_WARPED),
                '--sheet-bboxes', os.path.join(
                    self.common.sub(SUB_SCAN_ID),
                    'sheet_bboxes.json'),
                '--pdf-main', self.common.sub(SUB_PDF_GEO),
                '--out', self.get_out_dir()]
        if self.inner_margin.value() > 0:
            argv += ['--inner-margin', str(self.inner_margin.value())]
        return argv


# ============================================================
# Stage 5
# ============================================================

class Stage5Tab(StageTab):
    stage_name = '[6] 경계 검수'

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
        self.tabs.addTab(RecoveryTab(self.common), '2a. 미식별 보강')
        self.tabs.addTab(ExtractMapTab(self.common), '2b. 지도영역 추출')
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
        self.action = None        # 파이프라인 다이얼로그
        self.action_db = None     # DB 작업 다이얼로그
        self.dialog = None
        self.db_dialog = None
        # 편집 툴바 (행정리 작업용)
        self.edit_toolbar = None
        self.act_simplify = None

    def initGui(self):
        def _icon(name):
            p = os.path.join(PLUGIN_DIR, 'resources', name)
            return QIcon(p) if os.path.exists(p) else QIcon()

        # 1. 파이프라인 — 좌표 부여 아이콘
        self.action = QAction(_icon('icon_georef.svg'),
                              'GIS Scan Tools — 파이프라인',
                              self.iface.mainWindow())
        self.action.triggered.connect(self.show_dialog)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('&GIS Scan Tools', self.action)
        # 2. DB 작업 — 행정경계 아이콘 (파이프라인과 구분)
        self.action_db = QAction(_icon('icon_adminbnd.svg'),
                                 'GIS Scan Tools — DB 작업',
                                 self.iface.mainWindow())
        self.action_db.triggered.connect(self.show_db_dialog)
        self.iface.addToolBarIcon(self.action_db)
        self.iface.addPluginToMenu('&GIS Scan Tools', self.action_db)

        # 3. 행정리 편집 툴바 — QGIS 내장 액션 재사용 (화면정의서 S11, S14)
        self._build_edit_toolbar()

    def _build_edit_toolbar(self):
        """행정리 편집 전용 툴바. QGIS 내장 편집 액션을 모아 노출."""
        self.edit_toolbar = self.iface.addToolBar('행정리 편집')
        self.edit_toolbar.setObjectName('GISScanRiEditToolbar')
        # QGIS 내장 액션 — 3.40 기준
        try:
            self.edit_toolbar.addAction(self.iface.actionToggleEditing())
            self.edit_toolbar.addAction(self.iface.actionSaveActiveLayerEdits())
            self.edit_toolbar.addSeparator()
            self.edit_toolbar.addAction(self.iface.actionSplitFeatures())
        except AttributeError:
            # 일부 QGIS 버전에서 이름 다를 때를 위한 가드
            pass
        # Simplify 커스텀 액션 — Processing 다이얼로그 직접 실행
        simplify_icon_path = os.path.join(
            PLUGIN_DIR, 'resources', 'icon_tracing.svg')
        simplify_icon = (QIcon(simplify_icon_path)
                         if os.path.exists(simplify_icon_path) else QIcon())
        self.act_simplify = QAction(simplify_icon, 'Simplify (단순화)',
                                    self.iface.mainWindow())
        self.act_simplify.triggered.connect(self._run_simplify)
        self.edit_toolbar.addSeparator()
        self.edit_toolbar.addAction(self.act_simplify)

    def _run_simplify(self):
        """Processing 'native:simplifygeometries' 다이얼로그 실행.

        화면정의서 S14 "Q-gis 기능인 단순화(simplify)를 아이콘으로 넣기".
        활성 레이어를 INPUT으로 사전 지정.
        """
        try:
            from qgis import processing
        except Exception as e:
            QMessageBox.warning(self.iface.mainWindow(), '오류',
                                f'Processing 불러오기 실패: {e}')
            return
        active = self.iface.activeLayer()
        initial = {'INPUT': active} if active else {}
        try:
            processing.execAlgorithmDialog('native:simplifygeometries', initial)
        except Exception as e:
            QMessageBox.warning(self.iface.mainWindow(), 'Simplify 오류', str(e))

    def unload(self):
        for a in (self.action, self.action_db):
            if a:
                self.iface.removePluginMenu('&GIS Scan Tools', a)
                self.iface.removeToolBarIcon(a)
        # 편집 툴바 정리 (내장 액션은 이미 iface 소유라 삭제 X, 툴바만 제거)
        if self.edit_toolbar:
            self.edit_toolbar.deleteLater()
            self.edit_toolbar = None
        self.act_simplify = None
        for d in (self.dialog, self.db_dialog):
            if d:
                d.close()

    def show_dialog(self):
        if self.dialog is None:
            self.dialog = GISScanToolsDialog(self.iface,
                                             self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def show_db_dialog(self):
        if self.db_dialog is None:
            from .db_editor import DBEditorDialog
            self.db_dialog = DBEditorDialog(self.iface,
                                            self.iface.mainWindow())
        self.db_dialog.show()
        self.db_dialog.raise_()
        self.db_dialog.activateWindow()
