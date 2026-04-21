"""Stage 2: 스캔 식별 (행정코드 + 시트번호)

각 스캔의 (admin_code, sheet_id)를 자동 식별.

Tier A: 헤더 OCR로 admin_code 추출
Tier B: pHash + SIFT로 admin_code 식별 (Tier A 실패 시)
시트 식별: 그 admin의 분할 PDF들과 빠른 SIFT 매칭 → best inlier sheet 선택
   동시에 분할 PDF ↔ 메인 PDF 아핀 매칭으로 sheet world bbox 계산 (캐시)

CLI:
  python -m gis_scan_tools.tools.stage2_scan_identify \\
      --in scan/ --pdf-input pdf/ --pdf-main pdf_main_geo/ --out scan_identified/

산출:
  scan_identified/
    _identification.csv  (scan_path, status, admin_code, sheet_id, ...)
    sheet_bboxes.json    ({admin_code: {sheet_id: [minx,miny,maxx,maxy]}})
    _unmatched/          (짝 못찾은 스캔 격리)
"""
import argparse
import csv
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter

import cv2
import fitz
import numpy as np


def _imread(path):
    """Unicode 경로 안전 imread (한글 경로 대응)."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _imwrite(path, img, params=None):
    ext = os.path.splitext(path)[1] or '.jpg'
    ok, buf = cv2.imencode(ext, img, params or [])
    if not ok:
        return False
    buf.tofile(path)
    return True

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from ._legacy.common import (
        parse_jgw, extract_map_region, find_main_image)
except ImportError:
    from gis_scan_tools.tools._legacy.common import (
        parse_jgw, extract_map_region, find_main_image,
    )

SHEET_PATTERN = re.compile(r'^(\d{8})_(\d+)-(\d+)\.pdf$', re.IGNORECASE)


# ============================================================
# Tier A: 헤더 OCR (admin_code)
# ============================================================

def crop_header(img, ratio_h=0.18, ratio_w=0.40):
    h, w = img.shape[:2]
    return img[:int(h * ratio_h), :int(w * ratio_w)]


_TESS_CMD = None
_TESS_CHECKED = False
_TESS_ERROR = None

_SUBPROCESS_KW = {}
if sys.platform == 'win32':
    # Windows: 콘솔창 깜빡임 방지
    _SUBPROCESS_KW['creationflags'] = 0x08000000  # CREATE_NO_WINDOW


def _find_tesseract():
    """tesseract 바이너리 다단계 자동 탐색. 크로스플랫폼."""
    import shutil as _sh

    # 1) 환경변수 오버라이드 (사용자가 명시적 지정)
    env_cmd = os.environ.get('TESSERACT_CMD')
    if env_cmd and os.path.exists(env_cmd):
        return env_cmd

    # 2) PATH
    cmd = _sh.which('tesseract')
    if cmd:
        return cmd

    # 3) 플랫폼별 표준 설치 경로
    if sys.platform == 'win32':
        candidates = [
            r'C:\Program Files\Tesseract-OCR\tesseract.exe',
            r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
            os.path.expanduser(r'~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'),
            os.path.expanduser(r'~\AppData\Local\Tesseract-OCR\tesseract.exe'),
            # chocolatey
            r'C:\ProgramData\chocolatey\bin\tesseract.exe',
            # scoop
            os.path.expanduser(r'~\scoop\apps\tesseract\current\tesseract.exe'),
        ]
    elif sys.platform == 'darwin':
        candidates = [
            '/opt/homebrew/bin/tesseract',      # Apple Silicon Homebrew
            '/usr/local/bin/tesseract',          # Intel Homebrew
            '/opt/local/bin/tesseract',          # MacPorts
        ]
    else:  # linux
        candidates = [
            '/usr/bin/tesseract',
            '/usr/local/bin/tesseract',
            '/opt/conda/bin/tesseract',
            '/opt/conda/envs/ocr/bin/tesseract',
        ]

    for c in candidates:
        if os.path.exists(c):
            return c

    # 4) conda 환경 자동 탐색 (활성 환경 + 주변 env)
    if 'CONDA_PREFIX' in os.environ:
        p = os.path.join(os.environ['CONDA_PREFIX'],
                         'Scripts' if sys.platform == 'win32' else 'bin',
                         'tesseract' + ('.exe' if sys.platform == 'win32' else ''))
        if os.path.exists(p):
            return p

    # 5) Windows 레지스트리 조회 (공식 인스톨러가 기록)
    if sys.platform == 'win32':
        try:
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for key in (r'SOFTWARE\Tesseract-OCR',
                            r'SOFTWARE\WOW6432Node\Tesseract-OCR'):
                    try:
                        with winreg.OpenKey(root, key) as k:
                            path, _ = winreg.QueryValueEx(k, 'Path')
                            exe = os.path.join(path, 'tesseract.exe')
                            if os.path.exists(exe):
                                return exe
                    except OSError:
                        continue
        except ImportError:
            pass

    return None


def check_tesseract():
    """tesseract 바이너리/언어팩 확인. (cmd, error_msg) 반환."""
    global _TESS_CMD, _TESS_CHECKED, _TESS_ERROR
    if _TESS_CHECKED:
        return _TESS_CMD, _TESS_ERROR
    _TESS_CHECKED = True
    cmd = _find_tesseract()
    if not cmd:
        _TESS_ERROR = ('tesseract 바이너리를 찾을 수 없습니다.\n'
                       'Windows: install.bat 실행 또는 '
                       'https://github.com/UB-Mannheim/tesseract/wiki 에서 설치\n'
                       'Linux/Mac: conda install -c conda-forge tesseract')
        return None, _TESS_ERROR
    try:
        r = subprocess.run([cmd, '--list-langs'],
                           capture_output=True, text=True, timeout=10,
                           **_SUBPROCESS_KW)
        langs = r.stdout
        if 'kor' not in langs:
            _TESS_ERROR = (f'tesseract 한국어 언어팩(kor) 없음. 설치된 언어:\n'
                           f'{langs[:300]}')
            _TESS_CMD = cmd  # 영어만으로도 8자리 숫자는 인식 가능 → 진행
            return cmd, _TESS_ERROR
    except Exception as e:
        _TESS_ERROR = f'tesseract 실행 오류: {e}'
        return None, _TESS_ERROR
    _TESS_CMD = cmd
    return cmd, None


def _tesseract(img, config='--psm 6', lang='kor+eng'):
    cmd, err = check_tesseract()
    if not cmd:
        return ''
    # 한국어 언어팩 없으면 영어만
    if err and 'kor' not in err:
        pass  # 다른 오류
    elif err:
        lang = 'eng'

    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    tmp.close()
    try:
        _imwrite(tmp.name, img)
        r = subprocess.run(
            [cmd, tmp.name, '-', '-l', lang] + config.split(),
            capture_output=True, text=True, timeout=30, **_SUBPROCESS_KW)
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ''
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _ocr_temp(img, psm='11', lang='eng', whitelist=''):
    """이미지를 PNG temp로 쓴 뒤 tesseract 호출, stdout 반환."""
    cmd, _ = check_tesseract()
    if not cmd:
        return ''
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    tmp.close()
    try:
        _imwrite(tmp.name, img)
        args = [cmd, tmp.name, '-', '-l', lang, '--psm', psm]
        if whitelist:
            args += ['-c', f'tessedit_char_whitelist={whitelist}']
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=20, **_SUBPROCESS_KW)
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ''
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def ocr_sheet_id(scan_img, candidate_sheets=None):
    """시트번호(N-i) OCR.

    헤더 제외 ROI(좌상단 y8~20%, x0~18%)에서 시트번호 인식.

    candidate_sheets 주어지면 (예: {'7-1','7-2',...,'7-7'}) PDF 폴더가
    알려준 N(분할수)을 활용:
      · prefix N은 무시하고 **suffix 1..N만 인식** — 7이 '['로 OCR 오인식
        되는 케이스 회수
      · 숫자만 화이트리스트 → 특수문자/하이픈 노이즈 제거
      · 여러 전처리 변형 다수결

    Returns:
        sheet_id 문자열 (예: "7-1") 또는 None
    """
    h, w = scan_img.shape[:2]
    crop = scan_img[int(h * 0.08):int(h * 0.20), :int(w * 0.18)]
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    cmd, _ = check_tesseract()
    if not cmd:
        return None

    # === Path 1: 기존 morphology + N-i 정규식 ===
    g_small = cv2.resize(g, None, fx=0.4, fy=0.4, interpolation=cv2.INTER_AREA)
    _, bw = cv2.threshold(g_small, 100, 255, cv2.THRESH_BINARY_INV)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel)
    txt = _ocr_temp(bw, psm='11', lang='eng', whitelist='0123456789-')
    for sid in re.findall(r'\d+-\d+', txt):
        if not candidate_sheets or sid in candidate_sheets:
            return sid

    # === Path 2: 후보 알면 — suffix만 인식 (N=prefix 무시) ===
    if candidate_sheets:
        prefixes = {s.split('-')[0] for s in candidate_sheets if '-' in s}
        valid_suffixes = {int(s.split('-')[1]) for s in candidate_sheets
                          if '-' in s and s.split('-')[1].isdigit()}
        if not valid_suffixes:
            return None

        suffix_votes = Counter()
        # 여러 전처리 변형
        variants = [
            ('raw', g),
            ('upscale2x', cv2.resize(g, None, fx=2, fy=2,
                                     interpolation=cv2.INTER_CUBIC)),
        ]
        for _, im in variants:
            txt = _ocr_temp(im, psm='11', lang='eng', whitelist='0123456789')
            for run in re.findall(r'\d+', txt):
                # 단자릿: suffix 후보
                # 다자릿: 마지막이 suffix (예: "71" → "7-1" suffix=1)
                last = int(run[-1])
                if last in valid_suffixes:
                    suffix_votes[last] += 1

        if suffix_votes:
            best = suffix_votes.most_common(1)[0][0]
            if len(prefixes) == 1:
                return f'{next(iter(prefixes))}-{best}'
            # 다중 prefix(드문 케이스): suffix 매칭되는 첫 후보
            for sid in candidate_sheets:
                if sid.endswith(f'-{best}'):
                    return sid

    return None


def load_shp_index(shp_path):
    """SHP에서 OCR 보조 인덱스 빌드.

    Returns dict:
        codes: set of 8자리 adm_cd 전부 (전국)
        by_name: {adm_nm(공백제거): [adm_cd, ...]}  - 동명이 여러 시군구 가능
    """
    import geopandas as gpd
    try:
        gdf = gpd.read_file(shp_path, encoding='cp949')
    except Exception:
        gdf = gpd.read_file(shp_path)
    cols = {c.lower(): c for c in gdf.columns}
    code_col = cols.get('adm_cd') or cols.get('emd_cd')
    name_col = cols.get('adm_nm') or cols.get('emd_nm')
    if not code_col or not name_col:
        raise RuntimeError(
            f'SHP에 adm_cd/adm_nm 컬럼 없음 (있는 컬럼: {list(gdf.columns)})')
    codes = set()
    by_name = {}
    for _, r in gdf.iterrows():
        cd = str(r[code_col]).strip()
        nm = str(r[name_col]).strip()
        if not cd.isdigit() or len(cd) != 8 or not nm:
            continue
        codes.add(cd)
        nm_compact = re.sub(r'\s+', '', nm)
        by_name.setdefault(nm_compact, []).append(cd)
    print(f'[SHP 인덱스] {len(codes)}개 코드, {len(by_name)}개 행정명')
    return {'codes': codes, 'by_name': by_name}


def _fuzzy_8digit_match(token, valid_codes):
    """7~9자리 token을 valid_codes(8자리 set) 중 1자리 차이로 매칭.

    - 7자리: 어느 위치에 어떤 자리(0-9) 삽입해도 valid면 후보
    - 8자리: 정확 일치 또는 1자리 substitution이 valid면 후보
    - 9자리: 어느 1자리 빼도 valid면 후보
    """
    if not token.isdigit():
        return []
    L = len(token)
    cands = set()
    if L == 8:
        if token in valid_codes:
            cands.add(token)
        for i in range(8):
            for d in '0123456789':
                if d == token[i]:
                    continue
                t = token[:i] + d + token[i + 1:]
                if t in valid_codes:
                    cands.add(t)
    elif L == 7:
        for i in range(8):
            for d in '0123456789':
                t = token[:i] + d + token[i:]
                if t in valid_codes:
                    cands.add(t)
    elif L == 9:
        for i in range(9):
            t = token[:i] + token[i + 1:]
            if t in valid_codes:
                cands.add(t)
    return list(cands)


def _extract_korean_admin_names(text, by_name):
    """OCR 텍스트에서 한글 행정명(읍/면/동) 매칭 → 후보 코드 list.

    OCR이 자주 자모 사이 공백 삽입함 ('한 림 읍'). 정규화 후 substring 매칭.
    """
    compact = re.sub(r'\s+', '', text)
    found = []
    for name, cds in by_name.items():
        if name and name in compact:
            found.extend(cds)
    return list(set(found))


def _extract_admin_codes(text, valid_codes=None, shp_index=None):
    """텍스트에서 8자리 행정코드 추출.

    우선순위:
    1. 괄호 안 정확 8자리 + valid_codes 매칭 (기존)
    2. SHP 한글명 + 자릿수 fuzzy 교차검증 (둘 다 같은 코드 가리키면 강한 신호)
    3. SHP 한글명 단독 매칭 (코드 OCR 실패해도 한글명 인식되면 회수)
    4. SHP fuzzy 7~9자리 단독 매칭
    5. 폴백: 모든 8자리 + 공백 복원 (기존)

    valid_codes (PDF 보유 admin) 주어지면 최종 필터링.
    """
    # Tier 1: 괄호 안 정확 8자리 (기존)
    paren_candidates = []
    for m in re.finditer(r'\(\s*([\d\s]+?)\s*\)', text):
        digits = re.sub(r'\s', '', m.group(1))
        if len(digits) == 8 and digits.isdigit():
            paren_candidates.append(digits)
    if valid_codes is not None:
        paren_valid = [c for c in paren_candidates if c in valid_codes]
        if paren_valid:
            return paren_valid
    elif paren_candidates:
        return paren_candidates

    # Tier 2~4: SHP 활용 회수
    if shp_index is not None:
        shp_codes = shp_index['codes']
        # 자릿수 fuzzy: 괄호 안 + 자유 위치 모두 시도
        fuzzy_cands = set()
        for m in re.finditer(r'\(\s*([\d\s]+?)\s*\)', text):
            d = re.sub(r'\s', '', m.group(1))
            if d.isdigit() and 7 <= len(d) <= 9:
                fuzzy_cands.update(_fuzzy_8digit_match(d, shp_codes))
        for tok in re.findall(r'\d{7,9}', text):
            fuzzy_cands.update(_fuzzy_8digit_match(tok, shp_codes))
        # 한글명 lookup
        name_cands = set(_extract_korean_admin_names(
            text, shp_index['by_name']))

        # Tier 2: 교차검증 — 둘 다 가리키는 코드
        if fuzzy_cands and name_cands:
            cross = fuzzy_cands & name_cands
            if cross:
                cross_list = list(cross)
                if valid_codes is not None:
                    f = [c for c in cross_list if c in valid_codes]
                    return f if f else cross_list
                return cross_list

        # Tier 3: 한글명 단독
        if name_cands:
            name_list = list(name_cands)
            if valid_codes is not None:
                f = [c for c in name_list if c in valid_codes]
                if f:
                    return f
            else:
                return name_list

        # Tier 4: fuzzy 단독 (false positive 위험 — valid_codes로 강제 필터)
        if fuzzy_cands:
            fuzzy_list = list(fuzzy_cands)
            if valid_codes is not None:
                f = [c for c in fuzzy_list if c in valid_codes]
                if f:
                    return f
            else:
                return fuzzy_list

    # Tier 5: 기존 폴백 (모든 8자리 + 공백 복원)
    candidates = set(re.findall(r'\d{8}', text))
    digit_runs = re.findall(r'\d+', text)
    for i in range(len(digit_runs)):
        merged = ''
        for j in range(i, min(i + 5, len(digit_runs))):
            merged += digit_runs[j]
            if len(merged) == 8:
                candidates.add(merged)
            elif len(merged) > 8:
                break
    if valid_codes is not None:
        candidates = {c for c in candidates if c in valid_codes}
    return list(candidates)


def ocr_admin_code(scan_img, valid_codes=None, shp_index=None, fast=False):
    hdr = crop_header(scan_img)
    g = cv2.cvtColor(hdr, cv2.COLOR_BGR2GRAY)

    # SHP 활용 시: 한글명 OCR 회수 위해 kor+eng 우선, char whitelist는 사용 안 함
    variants = [('raw', hdr, '--psm 6', 'kor+eng')]
    if not fast:
        _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if shp_index is None:
            variants.append(('otsu', bw,
                             '--psm 6 -c tessedit_char_whitelist=0123456789()', 'eng'))
        else:
            variants.append(('otsu', bw, '--psm 6', 'kor+eng'))
        bw2 = cv2.adaptiveThreshold(
            g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 31, 10)
        if shp_index is None:
            variants.append(('adaptive', bw2,
                             '--psm 11 -c tessedit_char_whitelist=0123456789()', 'eng'))
        else:
            variants.append(('adaptive', bw2, '--psm 11', 'kor+eng'))
        big = cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, bw3 = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(('upscale', bw3, '--psm 6', 'kor+eng'))

    all_codes = []
    for _, im, cfg, lang in variants:
        text = _tesseract(im, cfg, lang)
        codes = _extract_admin_codes(text, valid_codes, shp_index=shp_index)
        all_codes.extend(codes)

    if not all_codes:
        return None, 0.0
    most, votes = Counter(all_codes).most_common(1)[0]
    return most, votes / max(1, len(variants))


# ============================================================
# pHash 폴백
# ============================================================

def compute_phash(img, hash_size=16):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = cv2.resize(g, (hash_size * 4, hash_size * 4),
                   interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(g)
    bits = (dct[:hash_size, :hash_size] >
            np.median(dct[:hash_size, :hash_size].flatten()[1:])).flatten()
    return np.packbits(bits.astype(np.uint8))


def hamming(a, b):
    return int(np.unpackbits(a ^ b).sum())


# ============================================================
# 시트 PDF 캐시 + sheet bbox 계산
# ============================================================

# PDF 텍스트 라벨 → 셀 좌상단 보정 (검증: 5 admin × 15 시트, stdev ±0.32 pt)
# 라벨 텍스트의 글자 padding 때문에 라벨 좌상단이 셀 좌상단보다 안쪽에 있음.
# 모든 PDF가 동일 도구(Qt 4.8.5)로 생성되어 일정.
# 부호: PDF pt 좌표계 (좌상단 origin, y는 아래로 증가)에서 셀 좌상단으로
# 이동하기 위해 라벨에 더할 값.
LABEL_OFFSET_X_PT = -13.82  # 라벨이 셀 좌측 모서리에서 안쪽으로 들어가 있음
LABEL_OFFSET_Y_PT = -2.76   # 라벨이 셀 상단 모서리에서 안쪽(아래로) 들어가 있음


class SheetCache:
    """분할 PDF 렌더 + SIFT keypoint + 메인 정합 bbox 캐시."""

    def __init__(self, pdf_input_dir, pdf_main_dir,
                 sheet_match_scale=0.25, cache_dir=None,
                 bbox_cache_path=None):
        self.pdf_input_dir = pdf_input_dir
        self.pdf_main_dir = pdf_main_dir
        self.scale = sheet_match_scale
        self.cache_dir = cache_dir or '/tmp/_sheet_cache'
        os.makedirs(self.cache_dir, exist_ok=True)
        self._sheet_meta = {}      # admin_code → {sheet_id: pdf_path}
        self._main_pdfs = {}       # admin_code → main pdf path (PDF 라벨 추출용)
        self._main_sift = {}       # admin_code → (g_main_map, kp, des, main_bbox, main_jgw)
        self._sheet_world_bbox = {}
        self._label_bboxes = {}    # admin_code → {sheet_id: bbox} PDF 라벨 캐시
        self._label_logged = set() # 로그 1회/admin
        self._scan_index_pdfs()

        # 기존 sheet_bboxes.json 로드 — SIFT 재계산 회피
        if bbox_cache_path and os.path.exists(bbox_cache_path):
            try:
                import json as _json
                with open(bbox_cache_path, 'r') as f:
                    cached = _json.load(f)
                for code, sheets in cached.items():
                    for sid, bbox in sheets.items():
                        if bbox and len(bbox) == 4:
                            self._sheet_world_bbox.setdefault(code, {})[sid] = tuple(bbox)
                n = sum(len(v) for v in self._sheet_world_bbox.values())
                print(f'  [bbox 캐시 로드] {n}개 (admin×sheet)')
            except Exception as e:
                print(f'  [bbox 캐시 로드 실패] {e}')

    def _scan_index_pdfs(self):
        # 재귀 탐색 — pdf/21/21510/22510110_4-1.pdf 같은 중첩 구조 지원
        # 메인 PDF({8자리}.pdf)와 분할 PDF({8자리}_N-i.pdf) 둘 다 인덱싱
        main_re = re.compile(r'^(\d{8})\.pdf$')
        for root, _, files in os.walk(self.pdf_input_dir):
            for f in sorted(files):
                m = SHEET_PATTERN.match(f)
                if m:
                    admin = m.group(1)
                    sid = f'{m.group(2)}-{m.group(3)}'
                    self._sheet_meta.setdefault(admin, {})[sid] = os.path.join(
                        root, f)
                    continue
                mm = main_re.match(f)
                if mm:
                    self._main_pdfs[mm.group(1)] = os.path.join(root, f)

    def admins_with_sheets(self):
        return list(self._sheet_meta.keys())

    def get_sheets(self, admin_code):
        return list(self._sheet_meta.get(admin_code, {}).items())

    def _render_pdf(self, pdf_path, dpi=300):
        cache_jpg = os.path.join(self.cache_dir,
                                 os.path.basename(pdf_path) + '.jpg')
        if os.path.exists(cache_jpg):
            return _imread(cache_jpg)
        doc = fitz.open(pdf_path)
        pix = doc[0].get_pixmap(dpi=dpi)
        pix.save(cache_jpg)
        doc.close()
        return _imread(cache_jpg)

    def _preprocess(self, img, scale=1.0):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16, 16))
        g = clahe.apply(g)
        if scale != 1.0:
            g = cv2.resize(g, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(g, (0, 0), 0.8)

    def get_main_sift_for_sheet_align(self, admin_code):
        """메인 PDF의 지도영역 SIFT (분할 PDF↔메인 정합용) + 디스크 pickle 캐시."""
        import pickle
        if admin_code in self._main_sift:
            return self._main_sift[admin_code]
        main_jpg = find_main_image(self.pdf_main_dir, admin_code)
        if main_jpg is None:
            raise FileNotFoundError(
                f'메인 이미지 없음: {self.pdf_main_dir}/{admin_code}.{{tif,jpg}}')
        main_jgw = parse_jgw(os.path.join(self.pdf_main_dir,
                                          f'{admin_code}.jgw'))
        cache_pkl = os.path.join(
            self.cache_dir, f'main_sift_{admin_code}.pkl')

        if os.path.exists(cache_pkl):
            try:
                with open(cache_pkl, 'rb') as f:
                    data = pickle.load(f)
                # OpenCV KeyPoint 복원
                kp = [cv2.KeyPoint(x=p[0], y=p[1], size=p[2], angle=p[3])
                      for p in data['kp']]
                # g_main은 크기만 필요해서 재생성
                main_img = _imread(main_jpg)
                main_map, main_bbox = extract_map_region(main_img)
                g = self._preprocess(main_map, scale=self.scale)
                self._main_sift[admin_code] = (
                    g, kp, data['des'], main_bbox, main_jgw)
                return self._main_sift[admin_code]
            except Exception:
                pass

        main_img = _imread(main_jpg)
        main_map, main_bbox = extract_map_region(main_img)
        g = self._preprocess(main_map, scale=self.scale)
        sift = cv2.SIFT_create(nfeatures=20000, contrastThreshold=0.03)
        kp, des = sift.detectAndCompute(g, None)
        self._main_sift[admin_code] = (g, kp, des, main_bbox, main_jgw)
        try:
            with open(cache_pkl, 'wb') as f:
                pickle.dump({
                    'kp': [(k.pt[0], k.pt[1], k.size, k.angle) for k in kp],
                    'des': des,
                }, f)
        except Exception:
            pass
        return self._main_sift[admin_code]

    def _bbox_from_grid(self, admin_code, sheet_id):
        """SIFT 실패 폴백: sheet_id 'N-i' 패턴으로 그리드 위치 추정.

        가정: sqrt(N) x sqrt(N) 정방 그리드, row-major (좌→우, 위→아래).
        예: 4-1=NW, 4-2=NE, 4-3=SW, 4-4=SE.
        9-1=top-left, 9-9=bottom-right.
        """
        import math
        m = re.match(r'(\d+)-(\d+)', sheet_id)
        if not m:
            return None
        N, i = int(m.group(1)), int(m.group(2))
        cols = int(math.ceil(math.sqrt(N)))
        rows = int(math.ceil(N / cols))
        row = (i - 1) // cols
        col = (i - 1) % cols

        # 메인 지도영역 world bbox
        if admin_code not in self._main_sift:
            self.get_main_sift_for_sheet_align(admin_code)
        _, _, _, main_bbox, main_jgw = self._main_sift[admin_code]
        mbx, mby, mbw, mbh = main_bbox
        minx = main_jgw.top_left_x + mbx * main_jgw.pixel_size_x
        maxx = main_jgw.top_left_x + (mbx + mbw) * main_jgw.pixel_size_x
        maxy = main_jgw.top_left_y + mby * main_jgw.pixel_size_y
        miny = main_jgw.top_left_y + (mby + mbh) * main_jgw.pixel_size_y

        cell_w = (maxx - minx) / cols
        cell_h = (maxy - miny) / rows
        qx0 = minx + col * cell_w
        qx1 = minx + (col + 1) * cell_w
        qy1 = maxy - row * cell_h
        qy0 = maxy - (row + 1) * cell_h
        return (qx0, qy0, qx1, qy1)

    def _bbox_from_pdf_labels(self, admin_code):
        """메인 PDF 텍스트 라벨('N-i') 위치 + 고정 오프셋으로 모든 sheet bbox 계산.

        SIFT 매칭 우회 — PDF 텍스트만 파싱 (수십 ms). 정확도 ±2m
        (검증: 5 admin × 15 시트, vs SIFT 산출 비교 stdev ±0.32 pt).

        Returns:
            {sheet_id: (minx, miny, maxx, maxy)} 또는 None (라벨 추출 실패)
        """
        if admin_code in self._label_bboxes:
            return self._label_bboxes[admin_code]

        sheets = self._sheet_meta.get(admin_code, {})
        if not sheets:
            return None
        # 분할수 N
        try:
            n_split = int(next(iter(sheets)).split('-')[0])
        except (ValueError, IndexError):
            return None

        main_pdf = self._main_pdfs.get(admin_code)
        if not main_pdf or not os.path.exists(main_pdf):
            return None
        main_jgw_p = os.path.join(self.pdf_main_dir, f'{admin_code}.jgw')
        main_jpg = find_main_image(self.pdf_main_dir, admin_code)
        if not os.path.exists(main_jgw_p) or main_jpg is None:
            return None
        try:
            main_jgw = parse_jgw(main_jgw_p)
        except Exception:
            return None

        # 메인 PDF에서 'N-i' 라벨 추출
        try:
            doc = fitz.open(main_pdf)
            page = doc[0]
            page_w_pt = page.rect.width
            target_re = re.compile(rf'^{n_split}-(\d+)$')
            labels = {}
            for w in page.get_text("words"):
                x0, y0, _, _, text = w[:5]
                if target_re.fullmatch(text):
                    labels[text] = (x0, y0)
            doc.close()
        except Exception:
            return None

        if len(labels) < 2:
            return None  # 셀 크기 추론 불가

        # 셀 크기 (라벨 간 최소 간격)
        xs = sorted({round(b[0]) for b in labels.values()})
        ys = sorted({round(b[1]) for b in labels.values()})
        cell_w_pt = (min(xs[i + 1] - xs[i] for i in range(len(xs) - 1))
                     if len(xs) > 1 else None)
        cell_h_pt = (min(ys[i + 1] - ys[i] for i in range(len(ys) - 1))
                     if len(ys) > 1 else None)
        if not cell_w_pt or not cell_h_pt:
            return None

        # 메인 jpg 폭 (PIL로 헤더만 — 전체 디코드 회피, 수십 ms)
        try:
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = None  # 큰 스캔이미지 경고 억제
            with Image.open(main_jpg) as im:
                img_w = im.size[0]
        except Exception:
            return None
        px_per_pt = img_w / page_w_pt

        # 각 라벨 → 보정 → world
        out = {}
        for sid, (lx, ly) in labels.items():
            tx = lx + LABEL_OFFSET_X_PT  # 셀 좌상단(PDF pt)
            ty = ly + LABEL_OFFSET_Y_PT
            px0 = tx * px_per_pt
            py0 = ty * px_per_pt
            px1 = (tx + cell_w_pt) * px_per_pt
            py1 = (ty + cell_h_pt) * px_per_pt
            wx0 = main_jgw.top_left_x + px0 * main_jgw.pixel_size_x
            wy0 = main_jgw.top_left_y + py0 * main_jgw.pixel_size_y
            wx1 = main_jgw.top_left_x + px1 * main_jgw.pixel_size_x
            wy1 = main_jgw.top_left_y + py1 * main_jgw.pixel_size_y
            out[sid] = (min(wx0, wx1), min(wy0, wy1),
                        max(wx0, wx1), max(wy0, wy1))

        self._label_bboxes[admin_code] = out
        return out

    def compute_sheet_world_bbox(self, admin_code, sheet_id):
        """sheet world bbox. PDF 라벨(즉시) 우선, SIFT(수 초) 폴백.

        라벨 우선: 메인 PDF의 'N-i' 텍스트 좌표 + 고정 오프셋. ±2m 정확도.
        SIFT 폴백: 라벨 추출 실패(이미지 PDF 등) 시 기존 분할↔메인 매칭.
        """
        cached = self._sheet_world_bbox.get(admin_code, {}).get(sheet_id)
        if cached:
            return cached

        # === 1순위: PDF 라벨 ===
        try:
            label_bboxes = self._bbox_from_pdf_labels(admin_code)
        except Exception as e:
            print(f'  [PDF 라벨 추출 오류→SIFT 폴백] {admin_code}: {e}')
            label_bboxes = None
        if label_bboxes and sheet_id in label_bboxes:
            bbox = label_bboxes[sheet_id]
            self._sheet_world_bbox.setdefault(admin_code, {})[sheet_id] = bbox
            if admin_code not in self._label_logged:
                print(f'  [PDF 라벨] {admin_code}: '
                      f'{len(label_bboxes)}개 시트 bbox 추출 (SIFT 우회)')
                self._label_logged.add(admin_code)
            return bbox

        # === 2순위: SIFT (기존 폴백) ===
        # 사전 체크: Stage 1 산출(jgw)이 없으면 SIFT 자체 불가 → 즉시 None
        main_jgw_p = os.path.join(self.pdf_main_dir, f'{admin_code}.jgw')
        if not os.path.exists(main_jgw_p):
            print(f'  [Stage 1 jgw 없음→스킵] {admin_code} {sheet_id} '
                  f'(메인 PDF 정합 실패한 admin)')
            return None

        pdf_path = self._sheet_meta[admin_code][sheet_id]
        sheet_img = self._render_pdf(pdf_path)
        sheet_map, sheet_bbox = extract_map_region(sheet_img)

        g_sheet = self._preprocess(sheet_map, scale=self.scale)
        sift = cv2.SIFT_create(nfeatures=10000, contrastThreshold=0.03)
        kp_s, des_s = sift.detectAndCompute(g_sheet, None)

        try:
            g_main, kp_m, des_m, main_bbox, main_jgw = self.get_main_sift_for_sheet_align(
                admin_code)
        except Exception as e:
            print(f'  [메인 SIFT 실패→스킵] {admin_code} {sheet_id}: {e}')
            return None
        if des_s is None or des_m is None:
            return None

        matcher = cv2.FlannBasedMatcher(
            {'algorithm': 1, 'trees': 5}, {'checks': 50})
        pairs = matcher.knnMatch(des_s, des_m, k=2)
        good = [m for m, n in pairs if m.distance < 0.75 * n.distance]
        if len(good) < 50:
            return None

        src = np.float32([kp_s[m.queryIdx].pt for m in good]) / self.scale
        dst = np.float32([kp_m[m.trainIdx].pt for m in good]) / self.scale
        A, inl_mask = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
        if A is None:
            print(f'  [SIFT 실패→그리드 폴백] {admin_code} {sheet_id}: A=None')
            bbox = self._bbox_from_grid(admin_code, sheet_id)
            if bbox:
                self._sheet_world_bbox.setdefault(admin_code, {})[sheet_id] = bbox
            return bbox
        n_inl = int(inl_mask.sum()) if inl_mask is not None else 0
        # affine 스케일 검증
        det = (A[0, 0] * A[1, 1] - A[0, 1] * A[1, 0])
        scale = abs(det) ** 0.5
        if not (0.15 <= scale <= 0.7) or n_inl < 30:
            print(f'  [SIFT 부정합→그리드 폴백] {admin_code} {sheet_id}: '
                  f'scale={scale:.3f}, inliers={n_inl}/{len(good)}')
            bbox = self._bbox_from_grid(admin_code, sheet_id)
            if bbox:
                self._sheet_world_bbox.setdefault(admin_code, {})[sheet_id] = bbox
            return bbox

        # sheet_map 4 corners → main_map → main 전체 → world
        sh, sw = sheet_map.shape[:2]
        corners = np.float32([[0, 0], [sw, 0], [sw, sh], [0, sh]])
        m_corners = (A[:, :2] @ corners.T).T + A[:, 2]
        # main_map → main 전체
        mbx, mby = main_bbox[0], main_bbox[1]
        m_corners[:, 0] += mbx
        m_corners[:, 1] += mby
        # main → world
        wx = main_jgw.top_left_x + m_corners[:, 0] * main_jgw.pixel_size_x
        wy = main_jgw.top_left_y + m_corners[:, 1] * main_jgw.pixel_size_y
        bbox = (float(wx.min()), float(wy.min()),
                float(wx.max()), float(wy.max()))
        self._sheet_world_bbox.setdefault(admin_code, {})[sheet_id] = bbox
        return bbox

    def export_sheet_geo(self, admin_code, sheet_id, out_dir):
        """분할 PDF의 지도영역 JPG + JGW를 Stage 3 매칭용으로 저장.

        사전 조건: compute_sheet_world_bbox가 성공해 bbox가 캐시됨.
        저장: {out_dir}/{admin}_{sheet}.{jpg,jgw,prj}
        """
        bbox = self._sheet_world_bbox.get(admin_code, {}).get(sheet_id)
        if bbox is None:
            return None
        pdf_path = self._sheet_meta[admin_code][sheet_id]
        sheet_img = self._render_pdf(pdf_path)
        sheet_map, _ = extract_map_region(sheet_img)
        sh, sw = sheet_map.shape[:2]
        minx, miny, maxx, maxy = bbox
        jpg_path = os.path.join(out_dir, f'{admin_code}_{sheet_id}.jpg')
        jgw_path = os.path.join(out_dir, f'{admin_code}_{sheet_id}.jgw')
        prj_path = os.path.join(out_dir, f'{admin_code}_{sheet_id}.prj')
        os.makedirs(out_dir, exist_ok=True)

        # 이미 저장돼 있으면 스킵 (재실행 가속)
        if os.path.exists(jpg_path) and os.path.exists(jgw_path):
            return jpg_path

        from ._legacy.common import write_jgw, JGWParams, PRJ_5179
        # JPG 저장 (_imwrite는 Unicode 경로 안전)
        ok, buf = cv2.imencode('.jpg', sheet_map,
                               [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            buf.tofile(jpg_path)
        jgw = JGWParams(
            pixel_size_x=(maxx - minx) / sw,
            rotation_x=0.0, rotation_y=0.0,
            pixel_size_y=-(maxy - miny) / sh,
            top_left_x=minx, top_left_y=maxy,
        )
        write_jgw(jgw_path, jgw)
        with open(prj_path, 'w') as f:
            f.write(PRJ_5179)
        return jpg_path


# ============================================================
# 스캔 → 시트 매칭
# ============================================================

# ============================================================
# 통합 식별 (OCR-only)
# ============================================================

def identify_scan(scan_jpg, sheet_cache, valid_codes,
                  shp_index=None, fast_ocr=False):
    img = _imread(scan_jpg)
    if img is None:
        return {'status': 'ERROR', 'admin_code': None, 'sheet_id': None,
                'method': 'LOAD_FAIL', 'message': 'cv2.imread 실패'}

    # admin_code 식별 (OCR + SHP fuzzy/한글명 회수)
    code, conf = ocr_admin_code(img, valid_codes=valid_codes,
                                shp_index=shp_index, fast=fast_ocr)
    if code is None:
        # 진단: tesseract 자체가 안 도는지, OCR은 도는데 코드만 못찾는지
        cmd, err = check_tesseract()
        if not cmd:
            msg = f'OCR 환경 문제: {err}'
        elif err:
            msg = f'OCR 부분 동작 (한국어 언어팩 없음): {err[:200]}'
        else:
            # tesseract OK였는데도 못찾음 — raw text 일부 노출
            hdr = crop_header(img)
            raw = _tesseract(hdr, '--psm 6', 'kor+eng' if not err else 'eng')
            msg = (f'OCR은 동작하나 8자리 행정코드 미검출. '
                   f'헤더 raw text(일부): {raw[:150].strip()!r}')
        return {'status': 'FAIL', 'admin_code': None, 'sheet_id': None,
                'method': 'OCR_FAIL', 'message': msg}

    # sheet_id 식별 — admin이 식별됐으니 PDF 후보군(N-i)을 알고 있음
    candidate_sheets = set(sheet_cache._sheet_meta.get(code, {}).keys())
    sid = ocr_sheet_id(img, candidate_sheets=candidate_sheets)
    if sid is None:
        return {'status': 'FAIL', 'admin_code': code, 'sheet_id': None,
                'method': 'OCR', 'confidence': conf,
                'message': 'admin OK but sheet OCR 실패'}
    if sid not in sheet_cache._sheet_meta.get(code, {}):
        return {'status': 'FAIL', 'admin_code': code, 'sheet_id': sid,
                'method': 'OCR', 'confidence': conf,
                'message': f'sheet OCR={sid} but admin {code}의 분할 PDF에 없음'}

    # sheet world bbox 계산 (Stage 3 매칭용 + Stage 4 병합용)
    bbox = sheet_cache.compute_sheet_world_bbox(code, sid)
    if bbox is None:
        return {'status': 'FAIL', 'admin_code': code, 'sheet_id': sid,
                'method': 'OCR', 'confidence': conf,
                'message': 'sheet bbox 계산 실패 (분할 PDF↔메인 정합 실패)'}

    # 분할 PDF를 JGW와 함께 내보내 Stage 3 매칭 템플릿으로 사용
    if hasattr(sheet_cache, 'sheets_geo_dir'):
        sheet_cache.export_sheet_geo(code, sid, sheet_cache.sheets_geo_dir)

    return {'status': 'OK', 'admin_code': code, 'sheet_id': sid,
            'method': 'OCR', 'confidence': conf, 'message': ''}


# ============================================================
# CLI
# ============================================================

def main():
    ap = argparse.ArgumentParser(description='Stage 2: 스캔 식별 (admin+sheet)')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='스캔 폴더 (재귀)')
    ap.add_argument('--pdf-input', required=True,
                    help='원본 PDF 폴더 (메인+분할 함께)')
    ap.add_argument('--pdf-main', required=True,
                    help='Stage 1 산출 폴더 (pdf_main_geo)')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--thorough', action='store_true',
                    help='OCR 4-variant 다수결 (기본: fast 1-variant)')
    ap.add_argument('--no-rename', action='store_true',
                    help='성공 스캔을 표준명으로 복사 안 함 (기본: identified/에 복사)')
    ap.add_argument('--no-unmatched', action='store_true',
                    help='실패 스캔을 _unmatched/에 복사 안 함 (기본: 복사)')
    ap.add_argument('--shp', default=None,
                    help='행정경계 SHP. 지정 시 OCR 회수율 ↑ '
                         '(7~9자리 fuzzy + 한글명 lookup)')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    print(f'[Stage 2] 시트 캐시 초기화')
    bbox_path = os.path.join(args.out_dir, 'sheet_bboxes.json')
    cache = SheetCache(args.pdf_input, args.pdf_main,
                       cache_dir=os.path.join(args.out_dir, '_sheet_cache'),
                       bbox_cache_path=bbox_path)
    # Stage 3 매칭 템플릿용 sheet geo 출력 경로
    cache.sheets_geo_dir = os.path.join(args.out_dir, 'sheets_geo')
    os.makedirs(cache.sheets_geo_dir, exist_ok=True)
    valid_codes = set(cache.admins_with_sheets())
    print(f'  → {len(valid_codes)}개 admin 코드 (분할 PDF 보유)')
    if not valid_codes:
        print('ERROR: 분할 PDF가 없음 (filename: {8자리}_{N}-{i}.pdf)')
        sys.exit(1)

    shp_index = None
    if args.shp:
        try:
            shp_index = load_shp_index(args.shp)
        except Exception as e:
            print(f'  [SHP 로드 실패 → 폴백] {e}')
            shp_index = None
    else:
        print('  [SHP 미지정 → 기존 OCR 동작]')

    scans = sorted(set(
        glob.glob(os.path.join(args.in_dir, '**/*.jpg'), recursive=True)
        + glob.glob(os.path.join(args.in_dir, '**/*.JPG'), recursive=True)
    ))
    scans = [s for s in scans if 'checkpoint' not in s]
    print(f'[Stage 2] 스캔 {len(scans)}장 식별 시작')

    csv_path = os.path.join(args.out_dir, '_identification.csv')
    unmatched_dir = os.path.join(args.out_dir, '_unmatched')
    identified_dir = os.path.join(args.out_dir, 'identified')
    seen_admin_sheet = {}  # (admin, sheet) → scan_path (중복 감지용)

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scan_path', 'status', 'admin_code', 'sheet_id',
                    'confidence', 'method', 'message',
                    'renamed_path', 'elapsed_s'])
        n_ok = n_fail = n_err = 0
        t0 = time.time()
        for i, scan in enumerate(scans, 1):
            ti = time.time()
            r = identify_scan(scan, cache, valid_codes,
                              shp_index=shp_index,
                              fast_ocr=not args.thorough)
            dt = time.time() - ti
            renamed = ''
            if r['status'] == 'OK':
                # 중복 감지 — OCR이 다른 sheet를 같은 값으로 오인식한 경우.
                # 자동 해결 불가 (어느 게 정답인지 모름) → CONFLICT로 격리,
                # 사용자가 2a 탭에서 각각 재지정.
                key = (r['admin_code'], r['sheet_id'])
                if key in seen_admin_sheet:
                    prev = seen_admin_sheet[key]
                    r['status'] = 'CONFLICT'
                    r['message'] = (f'중복 sheet_id: "{prev}"과 같은 '
                                    f'({r["admin_code"]}_{r["sheet_id"]}) '
                                    f'— OCR 오인식 가능. 2a 탭에서 재지정 필요')
                    print(f'  ⚠ {r["message"]}')
                    n_fail += 1
                    # _unmatched/로 격리 (identified/에는 복사 안 함)
                    if not args.no_unmatched:
                        os.makedirs(unmatched_dir, exist_ok=True)
                        shutil.copy2(scan, os.path.join(
                            unmatched_dir, os.path.basename(scan)))
                else:
                    n_ok += 1
                    seen_admin_sheet[key] = os.path.basename(scan)
                    if not args.no_rename:
                        code = r['admin_code']
                        sub_dir = os.path.join(
                            identified_dir, code[:2], code[:5])
                        os.makedirs(sub_dir, exist_ok=True)
                        ext = os.path.splitext(scan)[1]
                        renamed = os.path.join(
                            sub_dir, f"{code}_{r['sheet_id']}{ext}")
                        if os.path.exists(renamed):
                            base = os.path.splitext(renamed)[0]
                            k = 2
                            while os.path.exists(f'{base}_{k}{ext}'):
                                k += 1
                            renamed = f'{base}_{k}{ext}'
                        shutil.copy2(scan, renamed)
            elif r['status'] == 'FAIL':
                n_fail += 1
                if not args.no_unmatched:
                    os.makedirs(unmatched_dir, exist_ok=True)
                    shutil.copy2(scan, os.path.join(
                        unmatched_dir, os.path.basename(scan)))
            else:
                n_err += 1
            w.writerow([scan, r['status'], r.get('admin_code') or '',
                        r.get('sheet_id') or '',
                        f"{r.get('confidence', 0):.3f}",
                        r['method'], r['message'], renamed, f'{dt:.2f}'])
            if i % 5 == 0 or i == len(scans):
                print(f'  [{i}/{len(scans)}] OK={n_ok} FAIL={n_fail} '
                      f'ERR={n_err} ({(time.time()-t0)/i:.1f}s/장)')

    # sheet_bboxes.json 저장
    bbox_path = os.path.join(args.out_dir, 'sheet_bboxes.json')
    with open(bbox_path, 'w') as f:
        json.dump(cache._sheet_world_bbox, f, indent=2)

    print(f'\n[Stage 2] 완료: OK={n_ok}, FAIL={n_fail}, ERROR={n_err}')
    print(f'  CSV: {csv_path}')
    print(f'  bbox: {bbox_path}')


if __name__ == '__main__':
    main()
