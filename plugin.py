"""
GIS Scan Tools - 메인 플러그인 클래스
"""

import os
import json
import csv
import re
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QWidget, QPushButton, QLabel, QLineEdit, QFileDialog,
    QTextEdit, QGroupBox, QFormLayout, QSpinBox, QMessageBox,
    QProgressBar, QApplication, QTableWidget, QTableWidgetItem,
    QComboBox, QHeaderView, QAbstractItemView, QRadioButton,
    QButtonGroup, QShortcut, QCheckBox
)
from qgis.PyQt.QtGui import QIcon, QColor, QKeySequence
from qgis.PyQt.QtCore import Qt, QTimer, QThread, pyqtSignal
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsVectorLayer, QgsRectangle,
    QgsFillSymbol, QgsLineSymbol, QgsDataSourceUri, QgsLayerTreeLayer,
)


class GISScanToolsPlugin:
    """QGIS 플러그인 메인 클래스"""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.shortcuts = []
        self.menu = '&GIS Scan Tools'
        self.toolbar = self.iface.addToolBar('GIS Scan Tools')
        self.toolbar.setObjectName('GISScanTools')
        self.dialog = None

    def initGui(self):
        """플러그인 GUI 초기화 — 작업흐름 순서 툴바"""
        res = os.path.join(self.plugin_dir, 'resources')
        main_window = self.iface.mainWindow()

        def _add_btn(icon_file, label, handler, shortcut_key=None):
            icon_path = os.path.join(res, icon_file)
            icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
            action = QAction(icon, label, main_window)
            action.triggered.connect(handler)
            if shortcut_key is not None:
                sc = QShortcut(QKeySequence(shortcut_key), main_window)
                sc.activated.connect(handler)
                self.shortcuts.append(sc)
            self.toolbar.addAction(action)
            self.iface.addPluginToMenu(self.menu, action)
            self.actions.append(action)

        # --- 작업 탭 버튼 ---
        _add_btn('icon_georef.svg',   '좌표 생성',   lambda: self._open_tab(0))
        _add_btn('icon_adminbnd.svg', '분할도 병합', lambda: self._open_tab(1))

        self.toolbar.addSeparator()

        # --- 검수 버튼 ---
        _add_btn('icon_review.svg',   '결과 검수',   lambda: self._open_tab(3))
        _add_btn('icon_boundary.svg', '경계 검수',   lambda: self._open_tab(4))

        self.toolbar.addSeparator()

        # --- 편집 버튼 (F1~F8) ---
        _add_btn('icon_select.svg',   '선택 (F1)',      self._trigger_select,          Qt.Key_F1)
        _add_btn('icon_deselect.svg', '해제 (F2)',      self._trigger_deselect,        Qt.Key_F2)
        _add_btn('icon_edit.svg',     '편집 (F3)',      self._trigger_toggle_editing,  Qt.Key_F3)
        _add_btn('icon_save.svg',     '저장 (F4)',      self._trigger_save_edits,      Qt.Key_F4)
        _add_btn('icon_cut.svg',      'CUT (F5)',       self._trigger_split,           Qt.Key_F5)
        _add_btn('icon_merge.svg',    'Merge (F6)',     self._trigger_merge,           Qt.Key_F6)
        _add_btn('icon_snap.svg',     'Snap (F7)',      self._trigger_snap,            Qt.Key_F7)
        _add_btn('icon_tracing.svg',  '트레이싱 (F8)', self._trigger_tracing,         Qt.Key_F8)

        self.toolbar.addSeparator()

        # --- 유틸리티 ---
        _add_btn('icon_adminbnd.svg', '행정경계', self._toggle_boundary_layer)

    def unload(self):
        """플러그인 언로드"""
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        for sc in self.shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self.shortcuts.clear()
        del self.toolbar

    # === 편집 핸들러 ===

    def _trigger_select(self):
        self.iface.actionSelect().trigger()

    def _trigger_deselect(self):
        layer = self.iface.activeLayer()
        if layer:
            layer.removeSelection()

    def _trigger_toggle_editing(self):
        self.iface.actionToggleEditing().trigger()

    def _trigger_save_edits(self):
        self.iface.actionSaveActiveLayerEdits().trigger()

    def _trigger_split(self):
        for action in self.iface.mainWindow().findChildren(QAction):
            if action.objectName() == 'mActionSplitFeatures':
                action.trigger()
                return

    def _trigger_merge(self):
        for action in self.iface.mainWindow().findChildren(QAction):
            if action.objectName() == 'mActionMergeFeatures':
                action.trigger()
                return

    def _trigger_snap(self):
        proj = QgsProject.instance()
        config = proj.snappingConfig()
        config.setEnabled(not config.enabled())
        proj.setSnappingConfig(config)
        state = '활성화' if config.enabled() else '비활성화'
        self.iface.messageBar().pushInfo('스냅', f'스냅핑 {state}')

    def _trigger_tracing(self):
        # QGIS 메인 윈도우에서 트레이싱 액션 검색
        for action in self.iface.mainWindow().findChildren(QAction):
            if 'trac' in action.objectName().lower() or 'trac' in action.text().lower():
                action.trigger()
                return
        self.iface.messageBar().pushWarning('트레이싱', '트레이싱 액션을 찾을 수 없습니다')

    def _open_tab(self, index):
        """다이얼로그를 열고 지정 탭으로 전환"""
        if self.dialog is None:
            self.dialog = GISScanToolsDialog(self.iface, self.plugin_dir)
        self.dialog.tabs.setCurrentIndex(index)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def _toggle_boundary_layer(self):
        """내장 행정경계 SHP 레이어 토글 (원클릭, 다이얼로그 불필요)"""
        layer_name = '행정경계 (읍면동)'

        # 이미 추가된 경우 토글
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name() == layer_name:
                root = QgsProject.instance().layerTreeRoot()
                node = root.findLayer(lyr.id())
                if node:
                    node.setItemVisibilityChecked(not node.isVisible())
                    self.iface.mapCanvas().refresh()
                return

        shp_path = os.path.join(self.plugin_dir, 'data', 'bnd_adm_pg.shp')
        if not os.path.exists(shp_path):
            self.iface.messageBar().pushWarning(
                '경고', f'내장 SHP 파일을 찾을 수 없습니다: {shp_path}')
            return

        layer = QgsVectorLayer(shp_path, layer_name, 'ogr')
        if not layer.isValid():
            self.iface.messageBar().pushWarning('경고', 'SHP 파일을 로드할 수 없습니다.')
            return

        # 스타일: 채우기 없음 + 검정 외곽선
        symbol = QgsFillSymbol.createSimple({
            'color': '0,0,0,0',
            'outline_color': '0,0,0,255',
            'outline_width': '0.3',
        })
        layer.renderer().setSymbol(symbol)
        layer.triggerRepaint()

        QgsProject.instance().addMapLayer(layer)


class GISScanToolsDialog(QDialog):
    """메인 다이얼로그"""

    def __init__(self, iface, plugin_dir, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.plugin_dir = plugin_dir
        self.setWindowTitle('GIS Scan Tools')
        self.setMinimumSize(700, 500)
        self._review_items = []
        self._review_current_layer = None
        self.setup_ui()

    def setup_ui(self):
        """UI 구성"""
        layout = QVBoxLayout()

        # 실행 버튼들 저장 (비활성화용) - 탭 생성 전에 초기화
        self.run_buttons = []

        # 탭 위젯 (작업흐름 순서)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_shp_georef_tab(), '좌표 생성')         # 0
        self.tabs.addTab(self.create_merger_tab(), '분할도 병합')           # 1
        self.tabs.addTab(self.create_filename_tab(), '파일명 관리')        # 2
        self.tabs.addTab(self.create_review_tab(), '결과 검수')            # 3
        self.tabs.addTab(self.create_boundary_tab(), '경계 검수')          # 4
        self.tabs.addTab(self.create_image_validator_tab(), '이미지 검증')  # 5

        layout.addWidget(self.tabs)

        # 진행 상태 표시
        status_group = QGroupBox('작업 상태')
        status_layout = QVBoxLayout()

        # 상태 라벨
        self.status_label = QLabel('대기 중')
        self.status_label.setStyleSheet('font-weight: bold; padding: 5px;')
        status_layout.addWidget(self.status_label)

        # 진행률 바
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # 무한 진행 표시
        self.progress_bar.setVisible(False)
        status_layout.addWidget(self.progress_bar)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        # 로그 출력
        log_group = QGroupBox('로그')
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

        self.setLayout(layout)

    def create_shp_georef_tab(self):
        """좌표 생성 탭 — 이미지 파일 또는 폴더 입력"""
        widget = QWidget()
        layout = QVBoxLayout()

        desc_label = QLabel(
            'SHP 경계 매칭(FFT+Powell)으로 이미지에 좌표를 부여합니다.\n'
            '이미지 파일 하나 또는 폴더(일괄)를 선택하세요.'
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet('color: #666; margin-bottom: 10px;')
        layout.addWidget(desc_label)

        input_group = QGroupBox('입력 설정')
        form = QFormLayout()

        # 이미지 파일 (단일)
        self.georef_image_edit = QLineEdit()
        self.georef_image_edit.setPlaceholderText('이미지 파일 하나 선택 (폴더 입력 시 비워둘 것)')
        image_btn = QPushButton('찾기...')
        image_btn.clicked.connect(lambda: self.browse_file(
            self.georef_image_edit, '이미지 (*.jpg *.tif *.pdf)'))
        image_row = QHBoxLayout()
        image_row.addWidget(self.georef_image_edit)
        image_row.addWidget(image_btn)
        form.addRow('이미지 파일:', image_row)

        # 이미지 폴더 (일괄)
        self.georef_folder_edit = QLineEdit()
        self.georef_folder_edit.setPlaceholderText('폴더 선택 시 내부 모든 이미지를 일괄 처리')
        folder_btn = QPushButton('찾기...')
        folder_btn.clicked.connect(lambda: self.browse_folder(self.georef_folder_edit))
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.georef_folder_edit)
        folder_row.addWidget(folder_btn)
        form.addRow('이미지 폴더:', folder_row)

        # 전국 행정경계 SHP (내장 SHP 기본값)
        self.georef_shp_edit = QLineEdit()
        builtin_shp = os.path.join(self.plugin_dir, 'data', 'bnd_adm_pg.shp')
        if os.path.exists(builtin_shp):
            self.georef_shp_edit.setText(builtin_shp)
        self.georef_shp_edit.setPlaceholderText('전국 행정경계 SHP (adm_cd 컬럼 필요)')
        shp_btn = QPushButton('찾기...')
        shp_btn.clicked.connect(lambda: self.browse_file(
            self.georef_shp_edit, 'Shapefile (*.shp)'))
        shp_row = QHBoxLayout()
        shp_row.addWidget(self.georef_shp_edit)
        shp_row.addWidget(shp_btn)
        form.addRow('전국 행정경계 SHP:', shp_row)

        # 출력 폴더 (선택)
        self.georef_output_edit = QLineEdit()
        self.georef_output_edit.setPlaceholderText('비워두면 원본 이미지 위치에 저장')
        output_btn = QPushButton('찾기...')
        output_btn.clicked.connect(lambda: self.browse_folder(self.georef_output_edit))
        output_row = QHBoxLayout()
        output_row.addWidget(self.georef_output_edit)
        output_row.addWidget(output_btn)
        form.addRow('출력 폴더 (선택):', output_row)

        input_group.setLayout(form)
        layout.addWidget(input_group)

        # PostGIS 참조 레이어 설정
        pg_group = QGroupBox('PostGIS 참조 레이어')
        pg_form = QFormLayout()

        self.pg_enabled = QCheckBox('좌표 생성 후 참조 레이어 자동 로드')
        pg_form.addRow(self.pg_enabled)

        self.pg_host_edit = QLineEdit('localhost')
        pg_form.addRow('Host:', self.pg_host_edit)

        self.pg_port_edit = QLineEdit('5432')
        self.pg_port_edit.setMaximumWidth(80)
        pg_form.addRow('Port:', self.pg_port_edit)

        self.pg_db_edit = QLineEdit('census')
        pg_form.addRow('Database:', self.pg_db_edit)

        self.pg_user_edit = QLineEdit('postgres')
        pg_form.addRow('User:', self.pg_user_edit)

        self.pg_pw_edit = QLineEdit()
        self.pg_pw_edit.setEchoMode(QLineEdit.Password)
        pg_form.addRow('Password:', self.pg_pw_edit)

        pg_group.setLayout(pg_form)
        layout.addWidget(pg_group)

        # 실행 버튼
        run_btn = QPushButton('좌표 생성 실행')
        run_btn.clicked.connect(self.run_georef)
        run_btn.setStyleSheet('padding: 10px; font-weight: bold;')
        self.run_buttons.append(run_btn)
        layout.addWidget(run_btn)

        layout.addStretch()
        widget.setLayout(layout)
        return widget

    def create_boundary_tab(self):
        """경계 검수 탭 — 폴더 일괄 처리"""
        widget = QWidget()
        layout = QVBoxLayout()

        desc_label = QLabel(
            'JGW가 있는 이미지를 일괄 경계검수합니다.\n'
            '결과는 폴더 내 boundary_check/에 PNG로 저장됩니다.'
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet('color: #666; margin-bottom: 10px;')
        layout.addWidget(desc_label)

        input_group = QGroupBox('입력 설정')
        form = QFormLayout()

        self.boundary_folder_edit = QLineEdit()
        self.boundary_folder_edit.setPlaceholderText('JGW가 있는 이미지 폴더를 선택하세요')
        folder_btn = QPushButton('찾기...')
        folder_btn.clicked.connect(lambda: self.browse_folder(self.boundary_folder_edit))
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.boundary_folder_edit)
        folder_row.addWidget(folder_btn)
        form.addRow('이미지 폴더:', folder_row)

        input_group.setLayout(form)
        layout.addWidget(input_group)

        run_btn = QPushButton('경계 검수 실행')
        run_btn.clicked.connect(self.run_boundary_validation)
        run_btn.setStyleSheet('padding: 10px; font-weight: bold;')
        self.run_buttons.append(run_btn)
        layout.addWidget(run_btn)

        layout.addStretch()
        widget.setLayout(layout)
        return widget

    def create_image_validator_tab(self):
        """이미지 속성 검증 탭"""
        widget = QWidget()
        layout = QVBoxLayout()

        input_group = QGroupBox('입력 설정')
        form = QFormLayout()

        self.imgval_folder_edit = QLineEdit()
        folder_btn = QPushButton('찾기...')
        folder_btn.clicked.connect(lambda: self.browse_folder(self.imgval_folder_edit))
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.imgval_folder_edit)
        folder_row.addWidget(folder_btn)
        form.addRow('이미지 폴더:', folder_row)

        self.imgval_min_dpi = QSpinBox()
        self.imgval_min_dpi.setRange(100, 1200)
        self.imgval_min_dpi.setValue(300)
        form.addRow('최소 DPI:', self.imgval_min_dpi)

        input_group.setLayout(form)
        layout.addWidget(input_group)

        run_btn = QPushButton('이미지 속성 검증 실행')
        run_btn.clicked.connect(self.run_image_validation)
        run_btn.setStyleSheet('padding: 10px; font-weight: bold;')
        self.run_buttons.append(run_btn)
        layout.addWidget(run_btn)

        layout.addStretch()
        widget.setLayout(layout)
        return widget

    def create_merger_tab(self):
        """분할도 병합 탭"""
        widget = QWidget()
        layout = QVBoxLayout()

        desc_label = QLabel(
            '분할도를 병합하여 하나의 이미지로 만듭니다.\n'
            'N-분할 자동 감지, 메인 이미지 기준 AKAZE 매칭으로 좌표 생성합니다.\n'
            '메인 이미지(JGW 필요)를 반드시 지정하세요.'
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet('color: #666; margin-bottom: 10px;')
        layout.addWidget(desc_label)

        input_group = QGroupBox('입력 설정')
        form = QFormLayout()

        self.merger_main_edit = QLineEdit()
        self.merger_main_edit.setPlaceholderText('SHP와 정합된 메인 이미지 (JGW 필요)')
        main_btn = QPushButton('찾기...')
        main_btn.clicked.connect(lambda: self.browse_file(
            self.merger_main_edit, '이미지 (*.jpg *.tif)'))
        main_row = QHBoxLayout()
        main_row.addWidget(self.merger_main_edit)
        main_row.addWidget(main_btn)
        form.addRow('메인 이미지:', main_row)

        self.merger_input_edit = QLineEdit()
        input_btn = QPushButton('찾기...')
        input_btn.clicked.connect(self.browse_merger_input_folder)
        input_row = QHBoxLayout()
        input_row.addWidget(self.merger_input_edit)
        input_row.addWidget(input_btn)
        form.addRow('분할도 폴더:', input_row)

        self.merger_output_edit = QLineEdit()
        self.merger_output_edit.setPlaceholderText('분할도 폴더 선택 시 자동 설정')
        output_btn = QPushButton('찾기...')
        output_btn.clicked.connect(lambda: self.browse_save_file(
            self.merger_output_edit, '이미지 (*.jpg)'))
        output_row = QHBoxLayout()
        output_row.addWidget(self.merger_output_edit)
        output_row.addWidget(output_btn)
        form.addRow('출력 파일:', output_row)

        input_group.setLayout(form)
        layout.addWidget(input_group)

        run_btn = QPushButton('분할도 병합 실행')
        run_btn.clicked.connect(self.run_merging)
        run_btn.setStyleSheet('padding: 10px; font-weight: bold;')
        self.run_buttons.append(run_btn)
        layout.addWidget(run_btn)

        layout.addStretch()
        widget.setLayout(layout)
        return widget

    # === 파일명 관리 탭 ===

    def create_filename_tab(self):
        """파일명 관리 탭 — OCR 행정코드 추출 → 파일명 자동 제안"""
        widget = QWidget()
        layout = QVBoxLayout()

        desc_label = QLabel(
            'OCR로 이미지의 행정코드를 읽어 파일명을 자동 제안합니다.\n'
            '메인이미지: {코드}.jpg, 분할도: {코드}_{N-M}.jpg'
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet('color: #666; margin-bottom: 10px;')
        layout.addWidget(desc_label)

        # 입력 설정
        input_group = QGroupBox('입력 설정')
        form = QFormLayout()

        self.fname_folder_edit = QLineEdit()
        self.fname_folder_edit.setPlaceholderText('이미지가 있는 폴더를 선택하세요')
        folder_btn = QPushButton('찾기...')
        folder_btn.clicked.connect(lambda: self.browse_folder(self.fname_folder_edit))
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.fname_folder_edit)
        folder_row.addWidget(folder_btn)
        form.addRow('이미지 폴더:', folder_row)

        # 파일 유형 라디오 버튼
        type_row = QHBoxLayout()
        self.fname_type_group = QButtonGroup()
        self.fname_type_main = QRadioButton('메인이미지')
        self.fname_type_sub = QRadioButton('분할도')
        self.fname_type_main.setChecked(True)
        self.fname_type_group.addButton(self.fname_type_main, 0)
        self.fname_type_group.addButton(self.fname_type_sub, 1)
        type_row.addWidget(self.fname_type_main)
        type_row.addWidget(self.fname_type_sub)
        type_row.addStretch()
        form.addRow('파일 유형:', type_row)

        input_group.setLayout(form)
        layout.addWidget(input_group)

        # 파일 목록 테이블
        list_group = QGroupBox('파일 목록')
        list_layout = QVBoxLayout()

        self.fname_table = QTableWidget()
        self.fname_table.setColumnCount(4)
        self.fname_table.setHorizontalHeaderLabels(
            ['현재 파일명', 'OCR 코드', '제안 파일명', '상태'])
        self.fname_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.fname_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        header = self.fname_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)

        list_layout.addWidget(self.fname_table)
        list_group.setLayout(list_layout)
        layout.addWidget(list_group)

        # 하단 버튼
        btn_row = QHBoxLayout()

        scan_btn = QPushButton('폴더 스캔')
        scan_btn.clicked.connect(self.scan_filenames)
        scan_btn.setStyleSheet('padding: 8px; font-weight: bold;')
        btn_row.addWidget(scan_btn)

        rename_btn = QPushButton('일괄 리네임')
        rename_btn.clicked.connect(self.run_rename)
        rename_btn.setStyleSheet('padding: 8px; font-weight: bold;')
        btn_row.addWidget(rename_btn)

        csv_btn = QPushButton('CSV 내보내기')
        csv_btn.clicked.connect(self.export_filename_csv)
        csv_btn.setStyleSheet('padding: 8px;')
        btn_row.addWidget(csv_btn)

        layout.addLayout(btn_row)

        widget.setLayout(layout)
        return widget

    def scan_filenames(self):
        """폴더 스캔 → OCR 행정코드 추출 → 테이블 채우기"""
        folder = self.fname_folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, '경고', '유효한 폴더를 선택해주세요.')
            return

        is_subdivision = self.fname_type_sub.isChecked()
        image_exts = ('.jpg', '.jpeg', '.tif', '.tiff', '.pdf')

        # 이미지 파일 수집
        files = [f for f in sorted(os.listdir(folder))
                 if any(f.lower().endswith(ext) for ext in image_exts)]

        if not files:
            QMessageBox.warning(self, '경고', '폴더에 이미지 파일이 없습니다.')
            return

        self.start_task('파일명 스캔 (OCR)')
        self._fname_items = []
        self.fname_table.setRowCount(0)

        try:
            import pytesseract
            from PIL import Image
            import numpy as np

            # Tesseract 경로 설정 (Windows 기본 설치 경로)
            tesseract_path = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
            if os.path.exists(tesseract_path):
                pytesseract.pytesseract.tesseract_cmd = tesseract_path

            for i, fname in enumerate(files, 1):
                self.update_status(f'OCR 처리: {fname} ({i}/{len(files)})')

                fpath = os.path.join(folder, fname)
                admin_code = ''
                suggested = ''
                status = ''

                try:
                    img = Image.open(fpath)
                    # 상단 20% 영역에서 행정코드 추출
                    w, h = img.size
                    crop_h = max(int(h * 0.2), 200)
                    crop = img.crop((0, 0, w, crop_h))

                    # 그레이스케일 + OCR
                    gray = crop.convert('L')
                    text = pytesseract.image_to_string(
                        gray, lang='kor+eng',
                        config='--psm 6 --oem 3')

                    # 8자리 행정코드 추출
                    code_match = re.search(r'(\d{8})', text)
                    if code_match:
                        admin_code = code_match.group(1)

                        if is_subdivision:
                            # 분할도: N-M 패턴 추출 시도
                            nm_match = re.search(r'(\d+)\s*[-/]\s*(\d+)', text)
                            if nm_match:
                                n, m = nm_match.group(1), nm_match.group(2)
                                ext = os.path.splitext(fname)[1]
                                suggested = f'{admin_code}_{n}-{m}{ext}'
                            else:
                                ext = os.path.splitext(fname)[1]
                                suggested = f'{admin_code}{ext}'
                        else:
                            ext = os.path.splitext(fname)[1]
                            suggested = f'{admin_code}{ext}'

                        # 상태 판정
                        if fname == suggested:
                            status = '일치'
                        elif admin_code:
                            status = '변경필요'
                        else:
                            status = 'OCR실패'
                    else:
                        status = 'OCR실패'

                    img.close()
                except Exception as e:
                    status = f'오류: {e}'

                self._fname_items.append({
                    'original': fname,
                    'folder': folder,
                    'admin_code': admin_code,
                    'suggested': suggested,
                    'status': status,
                })

                QApplication.processEvents()

            # 테이블 채우기
            self.fname_table.setRowCount(len(self._fname_items))
            for row, item in enumerate(self._fname_items):
                self.fname_table.setItem(row, 0, QTableWidgetItem(item['original']))
                self.fname_table.setItem(row, 1, QTableWidgetItem(item['admin_code']))
                self.fname_table.setItem(row, 2, QTableWidgetItem(item['suggested']))

                status_item = QTableWidgetItem(item['status'])
                if item['status'] == '일치':
                    status_item.setForeground(QColor('#008800'))
                elif item['status'] == '변경필요':
                    status_item.setForeground(QColor('#cc6600'))
                elif 'OCR실패' in item['status'] or '오류' in item['status']:
                    status_item.setForeground(QColor('#cc0000'))
                self.fname_table.setItem(row, 3, status_item)

            n_change = sum(1 for it in self._fname_items if it['status'] == '변경필요')
            n_ok = sum(1 for it in self._fname_items if it['status'] == '일치')
            n_fail = sum(1 for it in self._fname_items if 'OCR실패' in it['status'])
            self.log(f'파일명 스캔: {len(files)}개 (일치:{n_ok}, 변경필요:{n_change}, OCR실패:{n_fail})')
            self.finish_task(True, f'{len(files)}개 스캔 완료')

        except Exception as e:
            self.log(f'오류: {str(e)}')
            self.finish_task(False, str(e))
            QMessageBox.critical(self, '오류', str(e))

    def run_rename(self):
        """일괄 리네임 실행"""
        if not hasattr(self, '_fname_items') or not self._fname_items:
            QMessageBox.warning(self, '경고', '먼저 폴더를 스캔해주세요.')
            return

        targets = [it for it in self._fname_items if it['status'] == '변경필요']
        if not targets:
            QMessageBox.information(self, '알림', '변경이 필요한 파일이 없습니다.')
            return

        reply = QMessageBox.question(
            self, '확인',
            f'{len(targets)}개 파일의 이름을 변경하시겠습니까?',
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        n_ok = 0
        n_err = 0
        for item in targets:
            src = os.path.join(item['folder'], item['original'])
            dst = os.path.join(item['folder'], item['suggested'])
            try:
                if os.path.exists(dst):
                    self.log(f'  건너뜀 (대상 존재): {item["original"]} → {item["suggested"]}')
                    n_err += 1
                    continue
                os.rename(src, dst)
                # JGW/meta.json 등 부속 파일도 리네임
                base_old = os.path.splitext(item['original'])[0]
                base_new = os.path.splitext(item['suggested'])[0]
                for ext in ('.jgw', '.meta.json', '.tfw'):
                    aux_src = os.path.join(item['folder'], base_old + ext)
                    aux_dst = os.path.join(item['folder'], base_new + ext)
                    if os.path.exists(aux_src) and not os.path.exists(aux_dst):
                        os.rename(aux_src, aux_dst)
                item['status'] = '완료'
                n_ok += 1
            except Exception as e:
                item['status'] = f'오류: {e}'
                n_err += 1

        # 테이블 갱신
        for row, item in enumerate(self._fname_items):
            status_item = QTableWidgetItem(item['status'])
            if item['status'] == '완료':
                status_item.setForeground(QColor('#008800'))
            elif '오류' in item['status']:
                status_item.setForeground(QColor('#cc0000'))
            self.fname_table.setItem(row, 3, status_item)

        self.log(f'리네임 완료: {n_ok}개 성공, {n_err}개 실패')
        QMessageBox.information(self, '완료', f'리네임: {n_ok}개 성공, {n_err}개 실패')

    def export_filename_csv(self):
        """파일명 관리 결과 CSV 내보내기"""
        if not hasattr(self, '_fname_items') or not self._fname_items:
            QMessageBox.warning(self, '경고', '먼저 폴더를 스캔해주세요.')
            return

        path, _ = QFileDialog.getSaveFileName(
            self, 'CSV 저장', '', 'CSV (*.csv)')
        if not path:
            return

        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['현재 파일명', 'OCR 코드', '제안 파일명', '상태'])
            for item in self._fname_items:
                writer.writerow([
                    item['original'], item['admin_code'],
                    item['suggested'], item['status']
                ])

        self.log(f'CSV 내보내기 완료: {path}')
        QMessageBox.information(self, '완료', f'CSV 파일이 저장되었습니다.\n{path}')

    # === 결과 검수 탭 ===

    def create_review_tab(self):
        """결과 검수 탭 — 좌표 생성 결과를 테이블로 확인·판정"""
        widget = QWidget()
        layout = QVBoxLayout()

        desc_label = QLabel(
            '좌표 생성 결과를 일괄 검수합니다.\n'
            '행 클릭 시 해당 위치로 줌 + 레이어 표시, 판정 후 CSV 내보내기.'
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet('color: #666; margin-bottom: 10px;')
        layout.addWidget(desc_label)

        # 입력 설정
        input_group = QGroupBox('입력 설정')
        form = QFormLayout()

        self.review_folder_edit = QLineEdit()
        self.review_folder_edit.setPlaceholderText('좌표 생성 결과가 있는 폴더 (JGW 파일 포함)')
        folder_btn = QPushButton('찾기...')
        folder_btn.clicked.connect(lambda: self.browse_folder(self.review_folder_edit))
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.review_folder_edit)
        folder_row.addWidget(folder_btn)
        form.addRow('결과 폴더:', folder_row)

        input_group.setLayout(form)
        layout.addWidget(input_group)

        # 결과 목록
        results_group = QGroupBox('결과 목록')
        results_layout = QVBoxLayout()

        top_btn_row = QHBoxLayout()

        scan_btn = QPushButton('폴더 스캔')
        scan_btn.clicked.connect(self.scan_review_folder)
        scan_btn.setStyleSheet('padding: 6px; font-weight: bold;')
        top_btn_row.addWidget(scan_btn)

        shp_btn = QPushButton('행정경계 표시')
        shp_btn.clicked.connect(self._load_boundary_layer)
        shp_btn.setStyleSheet('padding: 6px;')
        top_btn_row.addWidget(shp_btn)

        results_layout.addLayout(top_btn_row)

        self.review_table = QTableWidget()
        self.review_table.setColumnCount(5)
        self.review_table.setHorizontalHeaderLabels(
            ['✓', '파일명', '행정코드', 'cost(px)', '판정'])
        self.review_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.review_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.review_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.review_table.cellClicked.connect(self._on_review_row_clicked)

        header = self.review_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        results_layout.addWidget(self.review_table)
        results_group.setLayout(results_layout)
        layout.addWidget(results_group)

        # 하단 버튼
        btn_row = QHBoxLayout()

        pass_all_btn = QPushButton('전체 합격')
        pass_all_btn.clicked.connect(lambda: self.set_all_verdict('합격'))
        btn_row.addWidget(pass_all_btn)

        fail_all_btn = QPushButton('전체 불합격')
        fail_all_btn.clicked.connect(lambda: self.set_all_verdict('불합격'))
        btn_row.addWidget(fail_all_btn)

        export_btn = QPushButton('CSV 내보내기')
        export_btn.clicked.connect(self.export_review_csv)
        btn_row.addWidget(export_btn)

        layout.addLayout(btn_row)

        widget.setLayout(layout)
        return widget

    def scan_review_folder(self):
        """결과 폴더 스캔 → 테이블 채우기"""
        folder = self.review_folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, '경고', '유효한 폴더를 선택해주세요.')
            return

        self._review_items = []
        self.review_table.setRowCount(0)
        self._review_current_layer = None

        image_exts = ('.jpg', '.jpeg', '.tif', '.tiff')

        # JGW 기준 탐색: JGW 찾고 → 동일 이름 또는 *_georeferenced 등 이미지 매칭
        for fname in sorted(os.listdir(folder)):
            if not fname.lower().endswith('.jgw'):
                continue
            base = os.path.splitext(fname)[0]
            jgw_path = os.path.join(folder, fname)

            # 매칭 이미지 탐색: 정확히 같은 이름 우선, 없으면 base를 포함하는 이미지
            image_path = None
            for ext in image_exts:
                candidate = os.path.join(folder, base + ext)
                if os.path.exists(candidate):
                    image_path = candidate
                    break
            if image_path is None:
                # base를 포함하는 이미지 검색 (예: 23510310_georeferenced.jpg)
                for img_name in sorted(os.listdir(folder)):
                    if not any(img_name.lower().endswith(ext) for ext in image_exts):
                        continue
                    img_base = os.path.splitext(img_name)[0]
                    if img_base.startswith(base):
                        image_path = os.path.join(folder, img_name)
                        break
            if image_path is None:
                continue

            # JGW 파싱 → extent 계산
            try:
                extent = self._compute_extent_from_jgw(jgw_path, image_path)
            except Exception:
                continue

            # 메타데이터 로드 (JGW와 같은 base name)
            meta_path = os.path.join(folder, base + '.meta.json')
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                except Exception as e:
                    self.log(f'meta.json 로드 실패 ({base}): {e}')

            # 행정코드: 파일명에서 8자리 추출, 없으면 메타에서
            code_match = re.match(r'(\d{8})', base)
            admin_code = code_match.group(1) if code_match else meta.get('admin_code', '-')

            self._review_items.append({
                'path': image_path,
                'filename': os.path.basename(image_path),
                'jgw_path': jgw_path,
                'extent': extent,
                'admin_code': admin_code,
                'cost': meta.get('cost'),
                'gcp_count': meta.get('gcp_count'),
                'scale': meta.get('scale'),
            })

        # cost 높은 순 정렬 (불량 우선), 메타 없는 항목은 맨 뒤
        self._review_items.sort(
            key=lambda x: -(x['cost'] if x['cost'] is not None else float('-inf')))

        # 테이블 채우기
        self.review_table.setRowCount(len(self._review_items))
        for row, item in enumerate(self._review_items):
            # 체크박스
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
            self.review_table.setItem(row, 0, chk)

            # 파일명
            self.review_table.setItem(row, 1, QTableWidgetItem(item['filename']))

            # 행정코드
            self.review_table.setItem(row, 2, QTableWidgetItem(item['admin_code']))

            # cost
            cost_str = f"{item['cost']:.2f}" if item['cost'] is not None else '-'
            cost_item = QTableWidgetItem(cost_str)
            cost_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.review_table.setItem(row, 3, cost_item)

            # 판정 콤보박스
            combo = QComboBox()
            combo.addItems(['미검수', '합격', '불합격'])
            self.review_table.setCellWidget(row, 4, combo)

        self.log(f'검수 폴더 스캔: {len(self._review_items)}개 이미지 발견 ({folder})')

    def _on_review_row_clicked(self, row, col):
        """행 클릭 → 해당 위치 줌 + 레이어 토글"""
        if row < 0 or row >= len(self._review_items):
            return

        # 이전 레이어 숨기기
        if self._review_current_layer:
            root = QgsProject.instance().layerTreeRoot()
            node = root.findLayer(self._review_current_layer.id())
            if node:
                node.setItemVisibilityChecked(False)

        # 새 레이어 표시 + 줌
        item = self._review_items[row]
        layer = self._find_or_add_layer(item['path'])
        if layer:
            root = QgsProject.instance().layerTreeRoot()
            node = root.findLayer(layer.id())
            if node:
                node.setItemVisibilityChecked(True)

            self.iface.mapCanvas().setExtent(item['extent'])
            self.iface.mapCanvas().refresh()
            self._review_current_layer = layer

    def _load_boundary_layer(self):
        """내장 행정경계 SHP를 레이어에 추가 (브러시 없음, 선만 표시)"""
        layer_name = '행정경계 (읍면동)'

        # 이미 추가된 경우 토글
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name() == layer_name:
                root = QgsProject.instance().layerTreeRoot()
                node = root.findLayer(lyr.id())
                if node:
                    node.setItemVisibilityChecked(not node.isVisible())
                    self.iface.mapCanvas().refresh()
                self.log(f'행정경계 레이어 토글: {node.isVisible() if node else "?"}')
                return

        shp_path = os.path.join(self.plugin_dir, 'data', 'bnd_adm_pg.shp')
        if not os.path.exists(shp_path):
            QMessageBox.warning(self, '경고',
                '내장 SHP 파일을 찾을 수 없습니다.\n' + shp_path)
            return

        layer = QgsVectorLayer(shp_path, layer_name, 'ogr')
        if not layer.isValid():
            QMessageBox.warning(self, '경고', 'SHP 파일을 로드할 수 없습니다.')
            return

        # 스타일: 채우기 없음 + 검정 외곽선
        symbol = QgsFillSymbol.createSimple({
            'color': '0,0,0,0',
            'outline_color': '0,0,0,255',
            'outline_width': '0.3',
        })
        layer.renderer().setSymbol(symbol)
        layer.triggerRepaint()

        QgsProject.instance().addMapLayer(layer)
        self.log('행정경계 레이어 추가됨 (읍면동, 선만 표시)')

    def _find_or_add_layer(self, image_path):
        """QGIS 레이어 찾기 / 없으면 추가"""
        name = os.path.basename(image_path)
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name() == name:
                return layer
        layer = QgsRasterLayer(image_path, name)
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            return layer
        return None

    def _compute_extent_from_jgw(self, jgw_path, image_path):
        """JGW + 이미지 크기 → QgsRectangle"""
        from .tools.common import parse_jgw
        from PIL import Image

        jgw = parse_jgw(jgw_path)
        img = Image.open(image_path)
        w, h = img.size
        img.close()

        minx = jgw.top_left_x
        maxy = jgw.top_left_y
        maxx = minx + w * jgw.pixel_size_x
        miny = maxy + h * jgw.pixel_size_y  # pixel_size_y < 0
        return QgsRectangle(minx, miny, maxx, maxy)

    def export_review_csv(self):
        """검수 결과 CSV 내보내기"""
        if not self._review_items:
            QMessageBox.warning(self, '경고', '먼저 폴더를 스캔해주세요.')
            return

        path, _ = QFileDialog.getSaveFileName(
            self, 'CSV 저장', '', 'CSV (*.csv)')
        if not path:
            return

        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['파일명', '행정코드', 'cost(px)', '판정'])
            for row_idx, item in enumerate(self._review_items):
                combo = self.review_table.cellWidget(row_idx, 4)
                verdict = combo.currentText() if combo else '미검수'
                cost_str = f"{item['cost']:.2f}" if item['cost'] is not None else '-'
                writer.writerow([
                    item['filename'], item['admin_code'],
                    cost_str, verdict
                ])

        self.log(f'CSV 내보내기 완료: {path}')
        QMessageBox.information(self, '완료', f'CSV 파일이 저장되었습니다.\n{path}')

    def set_all_verdict(self, verdict):
        """전체 행 판정 일괄 설정"""
        for row_idx in range(self.review_table.rowCount()):
            combo = self.review_table.cellWidget(row_idx, 4)
            if combo:
                idx = combo.findText(verdict)
                if idx >= 0:
                    combo.setCurrentIndex(idx)

    @staticmethod
    def _to_python(val):
        """numpy 타입 → Python 네이티브 변환"""
        if val is None:
            return None
        try:
            import numpy as np
            if isinstance(val, (np.integer,)):
                return int(val)
            if isinstance(val, (np.floating,)):
                return float(val)
            if isinstance(val, np.ndarray):
                return val.tolist()
        except ImportError:
            pass
        return val

    def _save_georef_meta(self, result):
        """좌표 생성 결과 메타데이터 저장 (검수 탭용)"""
        jgw_path = result.get('jgw_path', '')
        if not jgw_path:
            return
        try:
            meta_path = os.path.splitext(jgw_path)[0] + '.meta.json'
            _p = self._to_python
            meta = {
                'cost': _p(result.get('cost')),
                'gcp_count': _p(result.get('gcp_count')),
                'scale': _p(result.get('scale')),
                'admin_code': result.get('admin_code', ''),
                'admin_name': result.get('admin_name', ''),
                'pixel_size': _p(result.get('pixel_size')),
                'elapsed': _p(result.get('elapsed')),
            }
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f'meta.json 저장 실패: {e}')

    # === PostGIS 참조 레이어 ===

    def _load_postgis_reference_layers(self, results):
        """좌표 생성 결과를 기반으로 PostGIS 참조 레이어를 로드한다.

        Args:
            results: 단일 result dict 또는 result dict 리스트
        """
        if not self.pg_enabled.isChecked():
            return

        if isinstance(results, dict):
            results = [results]
        if not results:
            return

        # DB 접속 정보
        host = self.pg_host_edit.text().strip() or 'localhost'
        port = self.pg_port_edit.text().strip() or '5432'
        dbname = self.pg_db_edit.text().strip() or 'census'
        user = self.pg_user_edit.text().strip() or 'postgres'
        password = self.pg_pw_edit.text().strip()

        # 전체 결과에서 sido_cd 집합과 adm_cd 집합, 통합 extent 계산
        sido_codes = set()
        adm_codes = set()
        xmin = ymin = float('inf')
        xmax = ymax = float('-inf')

        for r in results:
            adm_cd = r.get('admin_code', '')
            if len(adm_cd) >= 2:
                sido_codes.add(adm_cd[:2])
                adm_codes.add(adm_cd)

            # JGW에서 extent 계산 — result에서 직접 가져오기
            jgw_path = r.get('jgw_path', '')
            img_path = r.get('image_path', '')
            top_left = r.get('top_left')  # (x, y) 튜플
            pixel_size = r.get('pixel_size')

            if top_left and pixel_size and img_path:
                try:
                    from PIL import Image
                    with Image.open(img_path) as im:
                        w, h = im.size
                    x0 = top_left[0]
                    y0 = top_left[1]
                    x1 = x0 + w * pixel_size
                    y1 = y0 - h * pixel_size  # y는 아래로 감소
                    xmin = min(xmin, x0, x1)
                    xmax = max(xmax, x0, x1)
                    ymin = min(ymin, y0, y1)
                    ymax = max(ymax, y0, y1)
                except Exception as e:
                    self.log(f'PostGIS: extent 계산 오류 — {e}')
            elif jgw_path and os.path.exists(jgw_path) and img_path and os.path.exists(img_path):
                try:
                    from .tools.common import parse_jgw
                    from PIL import Image
                    jgw = parse_jgw(jgw_path)
                    with Image.open(img_path) as im:
                        w, h = im.size
                    x0 = jgw.offset_x
                    y0 = jgw.offset_y
                    x1 = x0 + w * jgw.pixel_size_x
                    y1 = y0 + h * jgw.pixel_size_y
                    xmin = min(xmin, x0, x1)
                    xmax = max(xmax, x0, x1)
                    ymin = min(ymin, y0, y1)
                    ymax = max(ymax, y0, y1)
                except Exception as e:
                    self.log(f'PostGIS: extent 계산 오류 — {e}')

        if not sido_codes or xmin == float('inf'):
            self.log('PostGIS: sido_cd 또는 extent를 계산할 수 없습니다.')
            return

        # extent에 10% 버퍼 추가
        dx = (xmax - xmin) * 0.1
        dy = (ymax - ymin) * 0.1
        xmin -= dx; xmax += dx; ymin -= dy; ymax += dy

        self.update_status('PostGIS 참조 레이어 로드 중...')

        try:
            import psycopg2

            conn = psycopg2.connect(
                host=host, port=port, dbname=dbname,
                user=user, password=password)
            cur = conn.cursor()

            # 1) sido_cd가 포함된 스키마 탐색
            cur.execute("""
                SELECT schema_name FROM information_schema.schemata
                WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'public')
            """)
            all_schemas = [row[0] for row in cur.fetchall()]

            target_schemas = []
            for schema in all_schemas:
                for sc in sido_codes:
                    if sc in schema:
                        target_schemas.append(schema)
                        break

            if not target_schemas:
                self.log(f'PostGIS: sido {sido_codes}에 해당하는 스키마를 찾지 못했습니다.')
                conn.close()
                return

            self.log(f'PostGIS: 스키마 발견 → {target_schemas}')

            # 2) geometry 컬럼이 있는 테이블 조회
            cur.execute("""
                SELECT f_table_schema, f_table_name, f_geometry_column, srid
                FROM geometry_columns
                WHERE f_table_schema = ANY(%s)
            """, (target_schemas,))
            geo_tables = cur.fetchall()

            conn.close()

            if not geo_tables:
                self.log('PostGIS: geometry 테이블을 찾지 못했습니다.')
                return

            # 3) 테이블 분류 (키워드 매칭)
            # 역할: jijuk(지적도), buld(건물), road(도로)
            LAYER_RULES = [
                {
                    'keywords': ['jijuk'],
                    'name': '지적도',
                    'match_adm_cd': True,  # 테이블명에 adm_cd가 있어야 함
                    'style': {'type': 'fill', 'color': '200,200,200,40',
                              'outline_color': '150,150,150,180', 'outline_width': '0.2'},
                },
                {
                    'keywords': ['buld', 'building'],
                    'name': '건물',
                    'match_adm_cd': False,
                    'style': {'type': 'fill', 'color': '200,180,220,60',
                              'outline_color': '160,140,180,180', 'outline_width': '0.2'},
                },
                {
                    'keywords': ['sprd', 'manage', 'road'],
                    'name': '도로',
                    'match_adm_cd': False,
                    'style': {'type': 'line', 'color': '180,180,180,150',
                              'width': '0.3'},
                },
            ]

            loaded_count = 0
            project = QgsProject.instance()
            root = project.layerTreeRoot()

            for schema, table, geom_col, srid in geo_tables:
                for rule in LAYER_RULES:
                    # 키워드 매칭
                    if not any(kw in table.lower() for kw in rule['keywords']):
                        continue

                    # jijuk는 adm_cd가 테이블명에 포함된 것만
                    if rule['match_adm_cd']:
                        if not any(ac in table for ac in adm_codes):
                            continue

                    # 이미 로드된 레이어인지 확인
                    layer_name = f"[참조] {rule['name']} ({table})"
                    already = any(
                        lyr.name() == layer_name
                        for lyr in project.mapLayers().values())
                    if already:
                        loaded_count += 1
                        break

                    # QgsDataSourceUri로 PostGIS 레이어 생성
                    uri = QgsDataSourceUri()
                    uri.setConnection(host, port, dbname, user, password)
                    uri.setDataSource(schema, table, geom_col)

                    # extent 공간 필터
                    uri.setSql(
                        f'ST_Intersects("{geom_col}", '
                        f"ST_MakeEnvelope({xmin},{ymin},{xmax},{ymax},{srid}))"
                    )

                    layer = QgsVectorLayer(uri.uri(False), layer_name, 'postgres')
                    if not layer.isValid():
                        self.log(f'PostGIS: 레이어 로드 실패 — {schema}.{table}')
                        break

                    # read-only + non-selectable
                    layer.setReadOnly(True)

                    # 스타일 적용
                    style = rule['style']
                    if style['type'] == 'fill':
                        symbol = QgsFillSymbol.createSimple({
                            'color': style['color'],
                            'outline_color': style['outline_color'],
                            'outline_width': style['outline_width'],
                        })
                        layer.renderer().setSymbol(symbol)
                    elif style['type'] == 'line':
                        symbol = QgsLineSymbol.createSimple({
                            'color': style['color'],
                            'width': style['width'],
                        })
                        layer.renderer().setSymbol(symbol)

                    layer.triggerRepaint()

                    # 레이어 추가 (맨 아래 배치)
                    project.addMapLayer(layer, False)
                    root.insertChildNode(-1, QgsLayerTreeLayer(layer))

                    # non-selectable 설정
                    try:
                        project.setLayerIsSelectable(layer.id(), False)
                    except AttributeError:
                        pass

                    loaded_count += 1
                    self.log(f'PostGIS: {layer_name} 로드 완료')
                    break

            if loaded_count > 0:
                self.log(f'PostGIS: 참조 레이어 {loaded_count}개 로드 완료')
                self.iface.mapCanvas().refresh()
            else:
                self.log('PostGIS: 조건에 맞는 참조 레이어가 없습니다.')

        except ImportError:
            self.log('PostGIS: psycopg2 모듈이 설치되지 않았습니다. '
                     'pip install psycopg2-binary')
        except Exception as e:
            self.log(f'PostGIS 참조 레이어 로드 실패: {e}')

    # === 유틸리티 함수 ===

    def browse_file(self, line_edit, filter_str):
        """파일 선택 다이얼로그"""
        path, _ = QFileDialog.getOpenFileName(self, '파일 선택', '', filter_str)
        if path:
            line_edit.setText(path)

    def browse_folder(self, line_edit):
        """폴더 선택 다이얼로그"""
        path = QFileDialog.getExistingDirectory(self, '폴더 선택')
        if path:
            line_edit.setText(path)

    def browse_merger_input_folder(self):
        """분할도 병합 - 입력 폴더 선택 시 출력 파일 자동 설정"""
        path = QFileDialog.getExistingDirectory(self, '분할도 폴더 선택')
        if path:
            self.merger_input_edit.setText(path)
            output_path = os.path.join(path, 'merged_map.jpg')
            self.merger_output_edit.setText(output_path)

    def browse_save_file(self, line_edit, filter_str):
        """저장 파일 선택 다이얼로그"""
        path, _ = QFileDialog.getSaveFileName(self, '저장 위치 선택', '', filter_str)
        if path:
            line_edit.setText(path)

    def log(self, message):
        """로그 메시지 출력"""
        self.log_text.append(message)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )
        QApplication.processEvents()

    def start_task(self, task_name):
        """작업 시작 - UI 상태 변경"""
        self.status_label.setText(f'🔄 {task_name} 진행 중...')
        self.status_label.setStyleSheet(
            'font-weight: bold; padding: 5px; color: #0066cc; background-color: #e6f0ff;')
        self.progress_bar.setVisible(True)

        for btn in self.run_buttons:
            btn.setEnabled(False)

        self.log(f'\n{"="*50}')
        self.log(f'▶ {task_name} 시작')
        self.log(f'{"="*50}')
        QApplication.processEvents()

    def finish_task(self, success=True, message=''):
        """작업 완료 - UI 상태 변경"""
        self.progress_bar.setVisible(False)

        if success:
            self.status_label.setText(f'✅ 완료: {message}')
            self.status_label.setStyleSheet(
                'font-weight: bold; padding: 5px; color: #008800; background-color: #e6ffe6;')
        else:
            self.status_label.setText(f'❌ 실패: {message}')
            self.status_label.setStyleSheet(
                'font-weight: bold; padding: 5px; color: #cc0000; background-color: #ffe6e6;')

        for btn in self.run_buttons:
            btn.setEnabled(True)

        QApplication.processEvents()

    def update_status(self, message):
        """작업 중 상태 업데이트"""
        self.status_label.setText(f'🔄 {message}')
        QApplication.processEvents()

    # === 실행 함수 ===

    def run_georef(self):
        """좌표 생성 실행 — 단일 이미지 또는 폴더 일괄"""
        image_path = self.georef_image_edit.text().strip()
        folder_path = self.georef_folder_edit.text().strip()
        shp_path = self.georef_shp_edit.text().strip()
        output_dir = self.georef_output_edit.text().strip() or None

        if not shp_path:
            QMessageBox.warning(self, '경고', '전국 행정경계 SHP를 선택해주세요.')
            return
        if not image_path and not folder_path:
            QMessageBox.warning(self, '경고', '이미지 파일 또는 이미지 폴더를 선택해주세요.')
            return

        is_batch = bool(folder_path)

        if is_batch:
            self._run_georef_folder(folder_path, shp_path, output_dir)
        else:
            self._run_georef_single(image_path, shp_path, output_dir)

    def _run_georef_single(self, image_path, shp_path, output_dir):
        """단일 이미지 좌표 생성"""
        self.start_task('좌표 생성 (FFT+Powell)')
        self.log(f'이미지: {os.path.basename(image_path)}')
        self.log(f'SHP: {os.path.basename(shp_path)}')

        try:
            from .tools.shp_georeferencer import SHPGeoreferencer

            self.update_status('SHP 파일 로딩 중...')
            georef = SHPGeoreferencer(shp_path)

            self.update_status('FFT+Powell 좌표 계산 중...')
            result = georef.georeference_image(image_path, output_dir)

            self._log_result(result)

            self.iface.messageBar().pushSuccess(
                '완료', f'좌표 생성 완료: {result["admin_name"]}')

            # 메타데이터 저장 (검수 탭용)
            self._save_georef_meta(result)

            # QGIS에 레이어 추가 (TIFF 우선, 없으면 원본 이미지)
            layer_path = result.get('tif_path') or result.get('image_path', '')
            if layer_path and os.path.exists(layer_path):
                layer = QgsRasterLayer(layer_path, os.path.basename(layer_path))
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                    self.log(f'QGIS에 레이어 추가됨: {os.path.basename(layer_path)}')

            # PostGIS 참조 레이어 로드
            self._load_postgis_reference_layers(result)

            self.finish_task(True,
                f'{result["admin_name"]} ({result["elapsed"]:.1f}초, cost={result["cost"]:.2f}px)')

        except Exception as e:
            self.log(f'오류: {str(e)}')
            self.finish_task(False, str(e))
            QMessageBox.critical(self, '오류', str(e))

    def _run_georef_folder(self, folder_path, shp_path, output_dir):
        """폴더 일괄 좌표 생성"""
        self.start_task('일괄 좌표 생성 (FFT+Powell)')
        self.log(f'폴더: {folder_path}')
        self.log(f'SHP: {os.path.basename(shp_path)}')

        try:
            from .tools.shp_georeferencer import SHPGeoreferencer

            self.update_status('SHP 파일 로딩 중...')
            georef = SHPGeoreferencer(shp_path)

            def on_progress(current, total, filename):
                self.update_status(f'처리 중: {filename} ({current}/{total})')
                self.log(f'\n[{current}/{total}] {filename}')
                QApplication.processEvents()

            batch_result = georef.georeference_folder(
                folder_path, output_dir, progress_callback=on_progress)

            # 개별 결과 로그
            for result in batch_result['results']:
                self._log_result(result)

            # 실패 파일 로그
            if batch_result['errors']:
                self.log(f'\n실패한 파일:')
                for err in batch_result['errors']:
                    self.log(f'  - {err["filename"]}: {err["error"]}')

            n_ok = len(batch_result['results'])
            n_total = batch_result['total']
            n_err = len(batch_result['errors'])

            # 메타데이터 저장 + QGIS에 추가 (TIFF 우선)
            for result in batch_result['results']:
                self._save_georef_meta(result)
                layer_path = result.get('tif_path') or result.get('image_path', '')
                if layer_path and os.path.exists(layer_path):
                    layer = QgsRasterLayer(layer_path, os.path.basename(layer_path))
                    if layer.isValid():
                        QgsProject.instance().addMapLayer(layer)

            if n_ok > 0:
                self.log(f'\nQGIS에 {n_ok}개 레이어 추가됨')

            # PostGIS 참조 레이어 로드 (전체 결과의 통합 extent)
            self._load_postgis_reference_layers(batch_result['results'])

            self.iface.messageBar().pushSuccess(
                '완료', f'일괄 좌표 생성: {n_ok}/{n_total} 성공')

            if n_err > 0:
                self.finish_task(True, f'{n_ok}/{n_total} 완료 ({n_err}개 실패)')
            else:
                self.finish_task(True, f'{n_ok}/{n_total} 완료')

        except Exception as e:
            self.log(f'오류: {str(e)}')
            self.finish_task(False, str(e))
            QMessageBox.critical(self, '오류', str(e))

    def _log_result(self, result):
        """개별 결과 로그 출력"""
        self.log(f'완료: {result["admin_name"]} ({result["admin_code"]}) → {result["base_name"]}')
        scale = result.get("scale")
        scale_str = f'1:{scale:,}' if scale else 'N/A'
        self.log(f'  축척: {scale_str}, DPI: {result.get("dpi", "?")}')
        self.log(f'  픽셀 크기: {result["pixel_size"]:.4f} m/px')
        self.log(f'  cost: {result["cost"]:.2f}px')
        self.log(f'  GCP 수: {result["gcp_count"]}개')
        self.log(f'  소요시간: {result["elapsed"]:.1f}초')
        self.log(f'  JGW 저장: {result["jgw_path"]}')

    def run_boundary_validation(self):
        """경계 검수 실행 — 폴더 내 JGW 있는 이미지 일괄 처리"""
        folder = self.boundary_folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, '경고', '유효한 이미지 폴더를 선택해주세요.')
            return

        shp_path = os.path.join(self.plugin_dir, 'data', 'bnd_adm_pg.shp')
        if not os.path.exists(shp_path):
            QMessageBox.warning(self, '경고', f'내장 SHP를 찾을 수 없습니다:\n{shp_path}')
            return

        # JGW가 있는 이미지 수집
        image_exts = ('.jpg', '.jpeg', '.tif', '.tiff')
        pairs = []
        for fname in sorted(os.listdir(folder)):
            if not any(fname.lower().endswith(ext) for ext in image_exts):
                continue
            img_path = os.path.join(folder, fname)
            base = os.path.splitext(fname)[0]
            jgw_path = os.path.join(folder, base + '.jgw')
            if os.path.exists(jgw_path):
                pairs.append((img_path, jgw_path))

        if not pairs:
            QMessageBox.warning(self, '경고', '폴더에 JGW가 있는 이미지가 없습니다.')
            return

        output_dir = os.path.join(folder, 'boundary_check')

        self.start_task('일괄 경계 검수')
        self.log(f'폴더: {folder}')
        self.log(f'대상: {len(pairs)}개 이미지')

        try:
            from .tools.boundary_validator import BoundaryValidator

            n_ok = 0
            for i, (img_path, jgw_path) in enumerate(pairs, 1):
                fname = os.path.basename(img_path)
                self.update_status(f'경계 검수 중: {fname} ({i}/{len(pairs)})')
                self.log(f'\n[{i}/{len(pairs)}] {fname}')

                try:
                    validator = BoundaryValidator(img_path, jgw_path, shp_path)
                    result = validator.run(output_dir)
                    n_ok += 1
                    self.log(f'  → {result["admin_code"]}: '
                             f'문제 {result["problem_count"]}개소, '
                             f'{result["problem_length"]:.1f}m')
                except Exception as e:
                    self.log(f'  오류: {e}')

                QApplication.processEvents()

            self.log(f'\n결과 저장: {output_dir}')
            self.iface.messageBar().pushSuccess(
                '완료', f'경계 검수: {n_ok}/{len(pairs)} 완료')
            self.finish_task(True, f'{n_ok}/{len(pairs)} 완료')

        except Exception as e:
            self.log(f'오류: {str(e)}')
            self.finish_task(False, str(e))
            QMessageBox.critical(self, '오류', str(e))

    def run_image_validation(self):
        """이미지 속성 검증 실행"""
        folder = self.imgval_folder_edit.text()
        min_dpi = self.imgval_min_dpi.value()

        if not folder:
            QMessageBox.warning(self, '경고', '이미지 폴더를 선택해주세요.')
            return

        self.start_task('이미지 속성 검증')
        try:
            from .tools.image_validator import validate_images

            self.update_status('이미지 파일 검색 중...')
            results = validate_images(folder, min_dpi=min_dpi)

            errors = [r for r in results if r.get('errors')]
            self.log(f'검사 완료: {len(results)}개 파일, {len(errors)}개 오류')

            if errors:
                for r in errors[:5]:
                    self.log(f"  - {r['filename']}: {r['errors']}")
                self.finish_task(True, f'{len(results)}개 파일 중 {len(errors)}개 오류 발견')
            else:
                self.log('모든 이미지가 검증을 통과했습니다.')
                self.finish_task(True, f'{len(results)}개 파일 모두 통과')

        except Exception as e:
            self.log(f'오류: {str(e)}')
            self.finish_task(False, str(e))
            QMessageBox.critical(self, '오류', str(e))

    def run_merging(self):
        """분할도 병합 실행 (N-분할 통합 처리)"""
        main_image = self.merger_main_edit.text().strip()
        input_dir = self.merger_input_edit.text().strip()
        output_file = self.merger_output_edit.text().strip()

        if not all([main_image, input_dir, output_file]):
            QMessageBox.warning(self, '경고',
                '메인 이미지, 분할도 폴더, 출력 파일을 모두 입력해주세요.')
            return

        main_jgw_path = os.path.splitext(main_image)[0] + '.jgw'
        if not os.path.exists(main_jgw_path):
            QMessageBox.warning(self, '경고',
                f'메인 이미지의 JGW 파일이 없습니다:\n{main_jgw_path}\n\n'
                '좌표 생성 탭에서 먼저 메인 이미지의 JGW를 생성하세요.')
            return

        self.start_task('분할도 병합')
        try:
            from .tools.subdivision_processor import process_subdivisions

            self.log(f'메인 이미지: {os.path.basename(main_image)}')
            self.log(f'분할도 폴더: {input_dir}')

            def on_progress(current, total, filename):
                self.update_status(f'분할도 매칭: {filename} ({current}/{total})')
                self.log(f'  [{current}/{total}] {filename}')
                QApplication.processEvents()

            # SHP 경로 (좌표생성 탭에서 가져옴, 필수)
            shp_path = self.georef_shp_edit.text().strip() if hasattr(self, 'georef_shp_edit') else None
            if not shp_path or not os.path.exists(shp_path):
                QMessageBox.warning(self, '경고',
                    'SHP 경로가 필요합니다.\n좌표 생성 탭에서 SHP 파일을 지정해주세요.')
                return

            self.update_status('분할도 처리 중...')

            # print 출력을 로그 창에 캡처
            import io, sys
            _old_stdout = sys.stdout
            _capture = io.StringIO()
            sys.stdout = _capture

            try:
                result = process_subdivisions(
                    input_dir, output_file,
                    main_image_path=main_image,
                    main_jgw_path=main_jgw_path,
                    progress_callback=on_progress,
                    shp_path=shp_path,
                )
            finally:
                sys.stdout = _old_stdout
                captured = _capture.getvalue()
                if captured.strip():
                    self.log(captured.rstrip())

            grid = result.get('grid', '?')
            n_ok = result.get('n_success', 0)
            n_total = result.get('n_total', 0)
            scale = result.get('scale', 0)

            method = result.get('method', '?')
            self.log(f'그리드: {grid}, 스케일: {scale:.2f}, 매칭: {method}')
            self.log(f'성공: {n_ok}/{n_total}')

            if result.get('errors'):
                for err in result['errors']:
                    self.log(f'  실패: 시트 {err["sheet"]} — {err["error"]}')

            merged_path = result.get('output')
            if merged_path and os.path.exists(merged_path):
                self.update_status('QGIS에 레이어 추가 중...')
                layer = QgsRasterLayer(merged_path, os.path.basename(merged_path))
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                    self.log(f'QGIS에 레이어 추가됨: {os.path.basename(merged_path)}')

            self.finish_task(True, f'병합 완료 ({grid}, {n_ok}/{n_total})')

        except Exception as e:
            self.log(f'오류: {str(e)}')
            self.finish_task(False, str(e))
            QMessageBox.critical(self, '오류', str(e))
