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
    QGraphicsView, QGraphicsScene,
)
from qgis.PyQt.QtGui import (
    QIcon, QPixmap, QImage, QPainter, QPen, QBrush, QColor,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QSize


PLUGIN_DIR = os.path.dirname(__file__)

# 자동 서브폴더 이름
SUB_PDF_GEO = '1_pdf_geo'
SUB_SCAN_ID = '2_scan_id'
SUB_MAP_EXTRACTED = '3_map_extracted'   # 참조 템플릿 매칭 기반 지도영역 추출
SUB_WARPED = '4_warped'
SUB_MERGED = '5_merged'
SUB_VALIDATION = '6_validation'
SUB_PUBLISHED = '7_published'


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

    def validate(self, need_pdf=True, need_scan=True, need_shp=True,
                 need_proj=True):
        if need_pdf and not self.pdf_input.text():
            raise ValueError('입력 PDF 폴더 지정 필요')
        if need_scan and not self.scan_input.text():
            raise ValueError('입력 스캔 폴더 지정 필요')
        if need_shp and not self.shp.text():
            raise ValueError('SHP 파일 지정 필요')
        if need_proj and not self.project_dir.text():
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
                from .tools.common import PRJ_5179
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

class ScanCornerView(QGraphicsView):
    """스캔 표시 + 좌클릭으로 지도영역 4꼭지점(TL→TR→BR→BL) 지정.

    scene 좌표는 표시 픽셀. points() 는 원본 스캔 픽셀로 환산해 반환.
    휠로 줌(정밀 클릭), [초기화] 로 점 리셋.
    """
    pointsChanged = pyqtSignal(int)
    LABELS = ['TL', 'TR', 'BR', 'BL']

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setMinimumSize(420, 320)
        self.setStyleSheet('QGraphicsView { background: #222; }')
        self._disp_scale = 1.0    # 표시/원본 비율
        self._pix_item = None
        self._markers = []        # [(dot, txt), ...]
        self._pts_disp = []       # 표시 좌표 점
        self._user_zoomed = False

    def load(self, scan_path, max_disp=2200):
        import cv2
        from .tools.common import load_image
        self._scene.clear()
        self._pix_item = None
        self._markers, self._pts_disp = [], []
        self._user_zoomed = False
        img = load_image(scan_path)            # BGR 원본 (한글경로 안전)
        h, w = img.shape[:2]
        self._disp_scale = min(1.0, max_disp / float(max(h, w)))
        if self._disp_scale < 1.0:
            img = cv2.resize(img, None, fx=self._disp_scale, fy=self._disp_scale,
                             interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        dh, dw = rgb.shape[:2]
        qimg = QImage(rgb.tobytes(), dw, dh, 3 * dw, QImage.Format_RGB888)
        self._pix_item = self._scene.addPixmap(QPixmap.fromImage(qimg))
        self._scene.setSceneRect(0, 0, dw, dh)
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self.pointsChanged.emit(0)

    def reset_points(self):
        for dot, txt in self._markers:
            self._scene.removeItem(dot)
            self._scene.removeItem(txt)
        self._markers, self._pts_disp = [], []
        self.pointsChanged.emit(0)

    def points(self):
        """원본 스캔 픽셀 4점 (TL,TR,BR,BL). 4점 미만이면 None."""
        if len(self._pts_disp) != 4:
            return None
        s = self._disp_scale or 1.0
        return [(x / s, y / s) for x, y in self._pts_disp]

    def mousePressEvent(self, ev):
        if (ev.button() == Qt.LeftButton and self._pix_item is not None
                and len(self._pts_disp) < 4):
            sp = self.mapToScene(ev.pos())
            n = len(self._pts_disp)
            r = 7
            dot = self._scene.addEllipse(sp.x() - r, sp.y() - r, 2 * r, 2 * r,
                                         QPen(QColor('#ff3030'), 2),
                                         QBrush(QColor(255, 48, 48, 120)))
            txt = self._scene.addSimpleText(self.LABELS[n])
            txt.setBrush(QBrush(QColor('#ffd000')))
            txt.setPos(sp.x() + r, sp.y() - 2 * r)
            self._markers.append((dot, txt))
            self._pts_disp.append((sp.x(), sp.y()))
            self.pointsChanged.emit(len(self._pts_disp))
        super().mousePressEvent(ev)

    def wheelEvent(self, ev):
        if self._pix_item is None:
            return
        self._user_zoomed = True
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.scale(*((1.25, 1.25) if ev.angleDelta().y() > 0 else (0.8, 0.8)))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._pix_item is not None and not self._user_zoomed:
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)


class RecoveryTab(QWidget):
    """수동 정합 — 헤더 절단·OCR 실패·오식별 스캔을 사람이 직접 복구.

    절차: 파일 선택 → (admin_code, sheet_id) 지정 → 스캔 위 지도영역 4꼭지점
    클릭 → [저장]. admin 에 분할 PDF 가 있으면 'PDF 모드'(크롭만, world bbox 는
    PDF 메타로 자동, 정합은 Stage 3 SIFT), 없으면 'GCP 모드'(지도 위 4점까지
    찍어 직접 지오레퍼런싱, Stage 3 생략).
    """
    CORNER_KO = ['좌상(TL)', '우상(TR)', '우하(BR)', '좌하(BL)']

    def __init__(self, common, iface=None):
        super().__init__()
        self.common = common
        self.iface = iface
        self._shp_cache = None    # (items, name_map)
        self._pdf_cache = None    # {admin_code: [sheet_id]}
        self._sheet_cache_obj = None  # 재사용 SheetCache (PDF 재인덱싱 회피)
        # GCP 모드 상태
        self._map_tool = None
        self._prev_tool = None
        self._rubber = None
        self._world_pts = None    # [(x,y), ...] EPSG:5179, 4점
        self._build_ui()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        layout = QVBoxLayout(self)
        help_label = QLabel(
            '<i>헤더가 잘렸거나 OCR이 실패/오식별한 스캔을 수동 복구합니다. '
            '파일 선택 → 행정코드·시트번호 지정 → 오른쪽 스캔에서 지도영역 '
            '<b>4꼭지점(좌상→우상→우하→좌하)</b>을 클릭 → [저장].</i>')
        help_label.setWordWrap(True)
        help_label.setStyleSheet(
            'QLabel { padding: 4px; background: #f0f0f0; border-radius: 3px; }')
        layout.addWidget(help_label)

        top = QHBoxLayout()
        self.btn_refresh = QPushButton('리스트 새로고침')
        self.btn_refresh.clicked.connect(self.refresh_list)
        self.btn_dups = QPushButton('중복 수집')
        self.btn_dups.setToolTip(
            'identified/ 에서 같은 이름으로 중복 저장된(_2 등) 그룹을 찾아 '
            '전부 _unmatched/ 로 옮겨 재확인 대상으로 만듭니다.')
        self.btn_dups.clicked.connect(self._collect_identified_dups)
        self.btn_add = QPushButton('스캔 파일 추가…')
        self.btn_add.clicked.connect(self._on_add_files)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_dups)
        top.addWidget(self.btn_add)
        top.addStretch()
        self.count_label = QLabel('대상: 0건')
        top.addWidget(self.count_label)
        layout.addLayout(top)

        body = QHBoxLayout()
        self.file_list = QListWidget()
        self.file_list.setMinimumWidth(260)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        body.addWidget(self.file_list, 2)

        right = QVBoxLayout()
        self.scan_view = ScanCornerView()
        self.scan_view.pointsChanged.connect(self._on_pts_changed)
        right.addWidget(self.scan_view, 1)

        pts_row = QHBoxLayout()
        self.pts_label = QLabel('스캔 점: 0/4')
        self.btn_reset_pts = QPushButton('점 초기화')
        self.btn_reset_pts.clicked.connect(self.scan_view.reset_points)
        pts_row.addWidget(self.pts_label)
        pts_row.addStretch()
        pts_row.addWidget(self.btn_reset_pts)
        right.addLayout(pts_row)

        form = QFormLayout()
        self.admin_cb = QComboBox()
        self.admin_cb.setEditable(True)
        self.admin_cb.setInsertPolicy(QComboBox.NoInsert)
        self.admin_cb.currentIndexChanged.connect(self._on_admin_changed)
        form.addRow('행정코드:', self.admin_cb)
        self.sheet_cb = QComboBox()
        self.sheet_cb.setEditable(True)
        form.addRow('시트번호:', self.sheet_cb)
        right.addLayout(form)

        self.mode_label = QLabel('모드: —')
        self.mode_label.setWordWrap(True)
        right.addWidget(self.mode_label)

        # GCP 모드 전용 (PDF 없는 admin) — 지도에서 월드 4점 찍기
        self.btn_map_pick = QPushButton('지도에서 월드 4점 찍기 (PDF 없을 때만)')
        self.btn_map_pick.clicked.connect(self._start_map_pick)
        self.btn_map_pick.setEnabled(False)
        right.addWidget(self.btn_map_pick)

        btn_row = QHBoxLayout()
        self.btn_save = QPushButton('저장')
        self.btn_save.clicked.connect(self._on_save)
        self.btn_skip = QPushButton('건너뜀')
        self.btn_skip.clicked.connect(self._on_skip)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_skip)
        right.addLayout(btn_row)

        self.status_label = QLabel('')
        self.status_label.setWordWrap(True)
        right.addWidget(self.status_label)

        body.addLayout(right, 3)
        layout.addLayout(body)

    # ------------------------------------------------------------ 데이터
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
            items, nm_map = [], {}
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
        """{admin_code: [sheet_id]} — PDF 폴더에서 분할 PDF 인덱싱."""
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
                if m:
                    result.setdefault(m.group(1), set()).add(
                        f'{m.group(2)}-{m.group(3)}')
        self._pdf_cache = {
            k: sorted(v, key=lambda s: (int(s.split('-')[0]),
                                        int(s.split('-')[1])))
            for k, v in result.items()}
        return self._pdf_cache

    def _unmatched_dir(self):
        p = self.common.project_dir.text()
        return os.path.join(p, SUB_SCAN_ID, '_unmatched') if p else ''

    # ------------------------------------------------------------ 리스트
    def refresh_list(self):
        self.file_list.clear()
        self.status_label.setText('')
        self._shp_cache = None
        self._pdf_cache = None
        self._sheet_cache_obj = None

        self.admin_cb.clear()
        shp_items, _ = self._load_shp_codes()
        if shp_items:
            for disp, cd in shp_items:
                self.admin_cb.addItem(disp, cd)
            completer = QCompleter([x[0] for x in shp_items], self.admin_cb)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            self.admin_cb.setCompleter(completer)
        else:
            self.status_label.setText(
                'SHP 미설정 또는 로드 실패 — 상단 공통설정에서 SHP 지정 필요')

        d = self._unmatched_dir()
        n = 0
        if d and os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                    it = QListWidgetItem(f)
                    it.setData(Qt.UserRole, os.path.join(d, f))
                    self.file_list.addItem(it)
                    n += 1
        self.count_label.setText(f'대상: {n}건'
                                 + ('' if n else '  (미식별 폴더 없음/비어있음)'))

    def _on_add_files(self):
        """오식별·중복명 케이스 — 임의 스캔을 작업 리스트에 추가."""
        start = self.common.scan_input.text() or ''
        paths, _ = QFileDialog.getOpenFileNames(
            self, '스캔 파일 추가', start,
            'Images (*.jpg *.jpeg *.png *.JPG *.JPEG *.PNG)')
        existing = {self.file_list.item(i).data(Qt.UserRole)
                    for i in range(self.file_list.count())}
        for p in paths:
            if p in existing:
                continue
            it = QListWidgetItem(os.path.basename(p))
            it.setData(Qt.UserRole, p)
            self.file_list.addItem(it)
        self.count_label.setText(f'대상: {self.file_list.count()}건')

    def _collect_identified_dups(self):
        """identified/ 에서 같은 (code,sid) 로 중복 저장된 그룹(_2 등)을 찾아
        그룹 전원을 _unmatched/ 로 이동 → 자동 식별을 신뢰하지 않고 사람이
        각각 재확인하도록 큐에 올린다 (어느 쪽이 맞는지 보장 없음)."""
        import re as _re
        import shutil as _sh
        proj = self.common.project_dir.text()
        if not proj:
            self.status_label.setText('프로젝트 폴더를 지정하세요 (공통설정)')
            return
        idroot = os.path.join(proj, SUB_SCAN_ID, 'identified')
        if not os.path.isdir(idroot):
            self.status_label.setText('identified/ 폴더 없음 (Stage 2 먼저 실행)')
            return
        pat = _re.compile(r'^(\d{8}_\d+-\d+)(?:_\d+)?\.(jpg|jpeg|png)$', _re.I)
        groups = {}
        for root, _, files in os.walk(idroot):
            for f in files:
                m = pat.match(f)
                if m:
                    groups.setdefault(m.group(1), []).append(
                        os.path.join(root, f))
        dst = os.path.join(proj, SUB_SCAN_ID, '_unmatched')
        ndup = moved = 0
        for paths in groups.values():
            if len(paths) < 2:
                continue
            ndup += 1
            os.makedirs(dst, exist_ok=True)
            for p in paths:
                name = os.path.basename(p)
                d = os.path.join(dst, name)
                k = 2
                while os.path.exists(d):
                    stem, ext = os.path.splitext(name)
                    d = os.path.join(dst, f'{stem}_{k}{ext}')
                    k += 1
                _sh.move(p, d)
                moved += 1
        self.refresh_list()
        self.status_label.setText(
            f'중복 그룹 {ndup}개 / 파일 {moved}건을 _unmatched/ 로 이동했습니다. '
            '각 파일을 확인해 올바른 이름으로 재지정하세요.'
            if ndup else 'identified/ 에 중복 그룹이 없습니다.')

    # ------------------------------------------------------------ 이벤트
    def _on_file_selected(self, item, _prev=None):
        if not item:
            return
        path = item.data(Qt.UserRole)
        self._world_pts = None
        try:
            self.scan_view.load(path)
        except Exception as e:
            self.status_label.setText(f'이미지 로드 실패: {e}')
            return
        # 파일명에서 admin/sheet 힌트
        import re as _re
        base = os.path.basename(path)
        ma = _re.search(r'(\d{8})', base)
        if ma:
            idx = self.admin_cb.findData(ma.group(1))
            if idx >= 0:
                self.admin_cb.setCurrentIndex(idx)
        ms = _re.search(r'(\d+-\d+)', base)
        if ms:
            self.sheet_cb.setEditText(ms.group(1))
        self.status_label.setText(f'선택: {base}')

    def _on_admin_changed(self, _idx):
        code = self.admin_cb.currentData()
        cur = self.sheet_cb.currentText()
        self.sheet_cb.clear()
        sheets = self._load_pdf_sheets().get(code, []) if code else []
        has_pdf = bool(sheets)
        if sheets:
            self.sheet_cb.addItems(sheets)
        else:
            self.sheet_cb.lineEdit().setPlaceholderText('예: 4-1 (직접 입력)')
        if cur:
            self.sheet_cb.setEditText(cur)
        # 모드 표시 + GCP 버튼 토글
        self.btn_map_pick.setEnabled(not has_pdf and self.iface is not None)
        if has_pdf:
            self.mode_label.setText(
                '모드: <b>PDF</b> — 크롭만, world bbox 는 PDF 메타로 자동. '
                '정합은 Stage 3 SIFT 가 수행.')
        elif self.iface is None:
            self.mode_label.setText('모드: GCP 필요하나 iface 없음 — PDF 폴더 확인')
        else:
            self.mode_label.setText(
                '모드: <b>GCP</b> (PDF 없음) — 스캔 4점 + 지도 4점으로 직접 '
                '지오레퍼런싱. Stage 3 생략, Stage 4 부터 잇습니다.')

    def _on_pts_changed(self, n):
        self.pts_label.setText(f'스캔 점: {n}/4')

    def _on_skip(self):
        row = self.file_list.currentRow()
        if row >= 0:
            self.file_list.setCurrentRow(
                min(row + 1, self.file_list.count() - 1))

    # ------------------------------------------------------------ 지도 4점 (GCP)
    def _start_map_pick(self):
        if self.iface is None:
            return
        from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand
        from qgis.core import QgsWkbTypes
        canvas = self.iface.mapCanvas()
        self._world_pts = None
        self._world_canvas_pts = []
        if self._rubber is None:
            self._rubber = QgsRubberBand(canvas, QgsWkbTypes.PointGeometry)
            self._rubber.setColor(QColor('#ff3030'))
            self._rubber.setIconSize(11)
        self._rubber.reset(QgsWkbTypes.PointGeometry)
        self._prev_tool = canvas.mapTool()
        self._map_tool = QgsMapToolEmitPoint(canvas)
        self._map_tool.canvasClicked.connect(self._on_map_click)
        canvas.setMapTool(self._map_tool)
        self.status_label.setText(
            '지도에서 ' + ' → '.join(self.CORNER_KO) + ' 순으로 4점 클릭')

    def _on_map_click(self, point, _button):
        self._rubber.addPoint(point)
        self._world_canvas_pts.append(point)
        n = len(self._world_canvas_pts)
        if n < 4:
            self.status_label.setText(
                f'지도 점 {n}/4 — 다음: {self.CORNER_KO[n]}')
            return
        canvas = self.iface.mapCanvas()
        canvas.unsetMapTool(self._map_tool)
        if self._prev_tool is not None:
            canvas.setMapTool(self._prev_tool)
        # 캔버스 CRS → EPSG:5179
        from qgis.core import (QgsProject, QgsCoordinateReferenceSystem,
                               QgsCoordinateTransform)
        src = QgsProject.instance().crs()
        dst = QgsCoordinateReferenceSystem('EPSG:5179')
        xform = QgsCoordinateTransform(src, dst, QgsProject.instance())
        w = [xform.transform(c) for c in self._world_canvas_pts]
        self._world_pts = [(p.x(), p.y()) for p in w]
        self.status_label.setText('지도 4점 완료 — [저장] 가능')

    # ------------------------------------------------------------ 저장
    def _on_save(self):
        item = self.file_list.currentItem()
        if not item:
            self.status_label.setText('파일을 선택하세요')
            return
        code = self.admin_cb.currentData() or self.admin_cb.currentText().strip()
        sid = self.sheet_cb.currentText().strip()
        if not code or len(code) != 8 or not code.isdigit():
            self.status_label.setText('행정코드(8자리)를 올바르게 지정하세요')
            return
        if not sid or '-' not in sid:
            self.status_label.setText('시트번호를 올바르게 지정하세요 (예: 4-1)')
            return
        quad = self.scan_view.points()
        if quad is None:
            self.status_label.setText('스캔에서 4꼭지점을 모두 찍으세요')
            return
        proj = self.common.project_dir.text()
        if not proj:
            self.status_label.setText('프로젝트 폴더를 지정하세요 (공통설정)')
            return
        src = item.data(Qt.UserRole)
        has_pdf = sid in self._load_pdf_sheets().get(code, [])
        try:
            if has_pdf:
                self._save_pdf_mode(src, code, sid, quad, proj)
            else:
                self._save_gcp_mode(src, code, sid, quad, proj)
        except Exception as e:
            self.status_label.setText(f'저장 실패: {e}')
            return
        # 처리 끝난 원본 회수 (재목록화·재처리 방지) + 리스트에서 제거
        self._retire_src(src, proj)
        self.file_list.takeItem(self.file_list.currentRow())
        self.count_label.setText(f'대상: {self.file_list.count()}건')
        self.scan_view.reset_points()

    def _retire_src(self, src, proj):
        """처리 끝난 원본을 _recovered/ 로 이동. 프로젝트(2_scan_id) 내부
        파일만 — 외부에서 '스캔 파일 추가' 한 원본은 건드리지 않는다."""
        import shutil as _sh
        root = os.path.join(proj, SUB_SCAN_ID)
        rel = os.path.relpath(src, root)
        if rel.startswith('..') or os.path.isabs(rel) \
                or rel.split(os.sep, 1)[0] == '_recovered':
            return
        if not os.path.exists(src):   # 이미 _quarantine 으로 이동됨
            return
        dst = os.path.join(root, '_recovered', rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        k = 2
        while os.path.exists(dst):
            stem, ext = os.path.splitext(dst)
            dst = f'{stem}_{k}{ext}'
            k += 1
        _sh.move(src, dst)

    def _save_pdf_mode(self, src, code, sid, quad, proj):
        from .tools.manual_georef import crop_map_region
        out = os.path.join(proj, SUB_MAP_EXTRACTED, code[:2], code[:5],
                           f'{code}_{sid}.jpg')
        w, h = crop_map_region(src, quad, out)
        warn = self._update_sheet_geo(code, sid)
        # 2b(extract_map) 재실행이 이 수동 크롭을 덮어쓰지 못하게,
        # identified/ 의 동일 시트 원본을 _recovered/ 로 격리.
        moved = self._quarantine_identified(proj, code, sid)
        msg = (f'[PDF 모드] 크롭 저장 {w}×{h}px → {out}\n'
               '→ Stage 3(매칭) → Stage 4(병합) 재실행하세요.')
        if moved:
            msg += f'\nidentified/ 원본 {moved}건을 _recovered/ 로 격리 (2b 보호).'
        self.status_label.setText(msg + (f'\n주의: {warn}' if warn else ''))

    def _quarantine_identified(self, proj, code, sid):
        """identified/ 에 있는 동일 (code, sid) 원본을 _recovered/ 로 이동.

        2b(stage_extract_map)는 identified/ 를 os.walk 로 재귀 탐색·덮어쓰므로,
        identified/ '바깥'(_recovered/)으로 옮겨야 2b 재실행에도 수동 크롭
        (3_map_extracted/)이 보존된다.
        Returns: 이동한 파일 수.
        """
        import re as _re
        import shutil as _sh
        base = os.path.join(proj, SUB_SCAN_ID, 'identified', code[:2], code[:5])
        if not os.path.isdir(base):
            return 0
        pat = _re.compile(
            rf'^{code}_{_re.escape(sid)}(?:_\d+)?\.(jpg|jpeg|png)$', _re.I)
        dst_dir = os.path.join(proj, SUB_SCAN_ID, '_recovered',
                               code[:2], code[:5])
        moved = 0
        for f in os.listdir(base):
            if not pat.match(f):
                continue
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, f)
            k = 2
            while os.path.exists(dst):
                stem, ext = os.path.splitext(f)
                dst = os.path.join(dst_dir, f'{stem}_{k}{ext}')
                k += 1
            _sh.move(os.path.join(base, f), dst)
            moved += 1
        return moved

    def _save_gcp_mode(self, src, code, sid, quad, proj):
        if not self._world_pts or len(self._world_pts) != 4:
            raise ValueError('PDF 없는 admin — 지도에서 월드 4점을 먼저 찍으세요')
        from .tools.manual_georef import georef_from_gcps
        out = os.path.join(proj, SUB_WARPED, code[:2], code[:5],
                           f'{code}_{sid}', f'{code}_{sid}.jpg')
        bbox = georef_from_gcps(src, quad, self._world_pts, out)
        self._write_bbox_entry(proj, code, sid, bbox)
        self._world_pts = None
        self.status_label.setText(
            f'[GCP 모드] 지오레퍼런싱 저장 → {out}\n'
            '→ Stage 4(병합) 재실행하세요 (Stage 3 불필요).')

    def _update_sheet_geo(self, code, sid):
        """PDF 메타로 sheets_geo/{code}_{sid} + sheet_bboxes.json 갱신.
        Returns: 경고 메시지 (성공 시 None)."""
        proj = self.common.project_dir.text()
        pdf_input = self.common.pdf_input.text()
        if not proj or not pdf_input:
            return 'project_dir/pdf_input 미설정 — Stage 3 전 재실행 필요'
        sheets_geo = os.path.join(proj, SUB_SCAN_ID, 'sheets_geo')
        bbox_json = os.path.join(proj, SUB_SCAN_ID, 'sheet_bboxes.json')
        pdf_main = os.path.join(proj, SUB_PDF_GEO)
        if not os.path.isdir(pdf_main):
            return f'Stage 1 출력 폴더 없음: {pdf_main}'
        os.makedirs(sheets_geo, exist_ok=True)
        try:
            cache = self._get_sheet_cache(pdf_input, pdf_main, bbox_json)
            if cache.compute_sheet_world_bbox(code, sid) is None:
                return f'sheet bbox 계산 실패: {code} {sid}'
            cache.export_sheet_geo(code, sid, sheets_geo)
            import json as _json
            with open(bbox_json, 'w') as f:
                _json.dump(cache._sheet_world_bbox, f, indent=2)
        except Exception as e:
            return f'sheets_geo 생성 실패: {e}'
        return None

    def _get_sheet_cache(self, pdf_input, pdf_main, bbox_json):
        """SheetCache 1회 생성·재사용 — 시트마다 PDF 폴더 재인덱싱 회피.
        (refresh_list 에서 무효화)."""
        if self._sheet_cache_obj is None:
            from .tools.stage2_scan_identify import SheetCache
            proj = self.common.project_dir.text()
            self._sheet_cache_obj = SheetCache(
                pdf_input, pdf_main,
                cache_dir=os.path.join(proj, SUB_SCAN_ID, '_sheet_cache'),
                bbox_cache_path=(bbox_json if os.path.exists(bbox_json)
                                 else None))
        return self._sheet_cache_obj

    def _write_bbox_entry(self, proj, code, sid, bbox):
        """GCP 모드 — sheet_bboxes.json 에 단일 항목 병합."""
        import json as _json
        path = os.path.join(proj, SUB_SCAN_ID, 'sheet_bboxes.json')
        data = {}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = _json.load(f)
            except Exception:
                data = {}
        data.setdefault(code, {})[sid] = list(bbox)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            _json.dump(data, f, indent=2)

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
        scan_id_dir = self.common.sub(SUB_SCAN_ID)
        argv = ['--identified', os.path.join(scan_id_dir, 'identified'),
                '--out', self.get_out_dir()]
        # ORB 매칭용 body 템플릿 (Stage 2 산출)
        sheet_cache = os.path.join(scan_id_dir, '_sheet_cache')
        if os.path.isdir(sheet_cache):
            argv += ['--sheet-cache', sheet_cache]
        sheet_bboxes = os.path.join(scan_id_dir, 'sheet_bboxes.json')
        if os.path.exists(sheet_bboxes):
            argv += ['--sheet-bboxes', sheet_bboxes]
        return argv


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
        # 사용자가 공통설정에 SHP 지정했으면 행정리 폴리곤 필터에 활용
        # (지정 안 하면 패키지 기본 data/bnd_adm_pg.shp — LFS 미해결 시 폴백)
        if self.common.shp.text():
            argv += ['--shp', self.common.shp.text()]
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
                  if proj else ''),
                 ('지도영역 추출 (PDF-less 폴백용)',
                  self.common.sub(SUB_MAP_EXTRACTED) if proj else ''),
                 ('SHP (PDF-less 폴백용)',
                  self.common.shp.text() or '(미지정 → PDF-less admin SKIPPED)')],
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
        # PDF-less virtual merge 자동 활성화 — extract 디렉토리 + SHP 가 있으면 추가
        extract_dir = self.common.sub(SUB_MAP_EXTRACTED)
        shp_path = self.common.shp.text()
        if extract_dir and os.path.isdir(extract_dir) and shp_path:
            argv += ['--extract-dir', extract_dir,
                     '--shp', shp_path,
                     '--auto-scale']
            extract_csv = os.path.join(extract_dir, '_status.csv')
            if os.path.exists(extract_csv):
                argv += ['--extract-csv', extract_csv]
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
# Stage 6 — COG 게시 (서버 업로드)
# ============================================================

class Stage6Tab(StageTab):
    stage_name = '[7] COG 게시'

    def __init__(self, common):
        from .tools import stage6_publish
        self.stage_module = stage6_publish
        super().__init__(common)

    def build_options(self):
        """외부 병합 폴더 / 출력 폴더 override.

        - 비워두면 공통입력의 프로젝트 폴더 하위(5_merged, 7_published) 를 사용
        - 둘 다 지정하면 공통입력의 프로젝트 폴더 없이도 실행 가능
        """
        self.merged_override = PathRow(
            '병합 폴더 직접 지정 (비우면 프로젝트/5_merged)', 'dir')
        self.out_override = PathRow(
            '출력 폴더 직접 지정 (비우면 프로젝트/7_published)', 'dir')
        self.opt_layout.addRow(self.merged_override)
        self.opt_layout.addRow(self.out_override)
        self.merged_override.edit.textChanged.connect(self._update_io_label)
        self.out_override.edit.textChanged.connect(self._update_io_label)

    def _merged_dir(self):
        ov = self.merged_override.text()
        if ov:
            return ov
        proj = self.common.project_dir.text()
        return os.path.join(proj, SUB_MERGED) if proj else ''

    def io_summary(self):
        return ([('Stage 4 병합 출력', self._merged_dir()),
                 ('서버 설정',
                  'DB 작업 > 서버 연결 탭에서 저장한 URL/토큰/S3 키 사용')],
                self.get_out_dir())

    def get_out_dir(self):
        ov = self.out_override.text()
        if ov:
            return ov
        p = self.common.project_dir.text()
        return os.path.join(p, SUB_PUBLISHED) if p else ''

    def get_argv(self):
        # 병합/출력 둘 다 override 면 project_dir 불필요
        need_proj = not (self.merged_override.text() and self.out_override.text())
        self.common.validate(need_pdf=False, need_scan=False, need_shp=False,
                             need_proj=need_proj)
        merged = self._merged_dir()
        if not merged:
            raise ValueError('병합 폴더가 비어 있음 — 직접 지정하거나 '
                             '프로젝트 폴더를 입력하세요')
        out = self.get_out_dir()
        if not out:
            raise ValueError('출력 폴더가 비어 있음 — 직접 지정하거나 '
                             '프로젝트 폴더를 입력하세요')
        argv = ['--merged', merged, '--out', out]
        shp = self.common.shp.text().strip()
        if shp:
            argv += ['--shp', shp]
        return argv


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
        self.tabs.addTab(RecoveryTab(self.common, self.iface), '2a. 수동 정합')
        self.tabs.addTab(ExtractMapTab(self.common), '2b. 지도영역 추출')
        self.tabs.addTab(Stage3Tab(self.common), '3. 매칭+워핑')
        self.tabs.addTab(Stage4Tab(self.common), '4. 사분면 병합')
        self.tabs.addTab(Stage5Tab(self.common), '5. 경계 검수')
        self.tabs.addTab(Stage6Tab(self.common), '6. COG 게시')
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
        if self.dialog:
            self.dialog.close()
        if self.db_dialog:
            try:
                self.iface.removeDockWidget(self.db_dialog)
            except Exception:
                pass
            self.db_dialog.deleteLater()
            self.db_dialog = None

    def show_dialog(self):
        if self.dialog is None:
            self.dialog = GISScanToolsDialog(self.iface,
                                             self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def show_db_dialog(self):
        if self.db_dialog is None:
            from qgis.PyQt.QtCore import Qt
            from .db_editor import DBEditorDock
            self.db_dialog = DBEditorDock(self.iface,
                                          self.iface.mainWindow())
            self.iface.addDockWidget(
                Qt.RightDockWidgetArea, self.db_dialog)
        self.db_dialog.show()
        self.db_dialog.raise_()
