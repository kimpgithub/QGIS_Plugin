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


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from ._legacy.common import (
        parse_jgw, extract_map_region, find_main_image,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )
except ImportError:
    from gis_scan_tools.tools._legacy.common import (
        parse_jgw, extract_map_region, find_main_image,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )

SHEET_PATTERN = re.compile(r'^(\d{8})_(\d+)-(\d+)\.pdf$', re.IGNORECASE)

# Sheet bbox skeleton ICP 파라미터
ICP_MIN_COMPONENT_PX = 200   # skeleton component 최소 길이 (격자 tick 제거)
ICP_MAX_SHIFT_M = 50.0       # ICP translation safety gate — 초과 시 rollback
ICP_SHP_BUFFER_M = 500       # 주변 admin SHP 샘플 범위


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


HEADER_OCR_TARGET_W = 1080  # 0.25x for 4320-px header — Tesseract 훈련 분포 정합
SCAN_SIFT_TARGET_W = 1600   # scan SIFT 추출 전 다운스케일 폭 — SIFT 비용 핵심 파라미터
VISUAL_INLIER_MIN = 30      # 정답 확신 최소 inlier — 이하면 FAIL/unmatched
VISUAL_EARLY_EXIT = 800     # 이 이상 inlier면 남은 후보 스킵 (확실한 정답)


def _downscale_to_width(img, target_w):
    h, w = img.shape[:2]
    if w <= target_w:
        return img
    s = target_w / w
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)


_SIFT_SCAN = None
_FLANN = None


def _get_sift_scan():
    """식별용 SIFT — feature 수를 5000으로 제한 (매칭 비용 5배 절감).
    식별은 '같은 sheet인지 아닌지'만 판정하면 되므로 5K면 충분.
    (Stage 3 정합용은 30K로 별도 유지.)"""
    global _SIFT_SCAN
    if _SIFT_SCAN is None:
        _SIFT_SCAN = cv2.SIFT_create(nfeatures=5000, contrastThreshold=0.04)
    return _SIFT_SCAN


def _get_flann():
    # trees 4, checks 32 — 식별 정확도에 충분, 기본값보다 빠름
    global _FLANN
    if _FLANN is None:
        _FLANN = cv2.FlannBasedMatcher(
            {'algorithm': 1, 'trees': 4}, {'checks': 32})
    return _FLANN


def _scan_sift(scan_img):
    """스캔 이미지 SIFT 피처 추출 (다운스케일 + CLAHE)."""
    g = cv2.cvtColor(scan_img, cv2.COLOR_BGR2GRAY) if scan_img.ndim == 3 \
        else scan_img
    h, w = g.shape
    if w > SCAN_SIFT_TARGET_W:
        s = SCAN_SIFT_TARGET_W / w
        g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16, 16))
    g = clahe.apply(g)
    return _get_sift_scan().detectAndCompute(g, None)


def _match_inliers(des_s, kp_s, des_t, kp_t):
    """FLANN knn + Lowe ratio + RANSAC → inlier 수."""
    if (des_s is None or des_t is None
            or len(des_s) < 20 or len(des_t) < 20):
        return 0
    pairs = _get_flann().knnMatch(des_s, des_t, k=2)
    good = [m for m, n in pairs if len(pairs[0]) == 2
            and m.distance < 0.75 * n.distance]
    if len(good) < 10:
        return 0
    src = np.float32([kp_s[m.queryIdx].pt for m in good])
    dst = np.float32([kp_t[m.trainIdx].pt for m in good])
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if mask is None:
        return 0
    return int(mask.sum())


def visual_sheet_match(scan_kp, scan_des, admin_code, sheet_cache):
    """스캔 SIFT와 해당 admin의 모든 split PDF SIFT 매칭 → inlier dict.

    Returns:
        {sheet_id: inlier_count} — Hungarian 할당용 원시 점수
        (조기 종료 시 미평가 sheet는 누락됨)
    """
    scores = {}
    sheets = sheet_cache.get_sheets(admin_code)
    # 큰 sheet부터 평가(더 구별력 높음) — 정렬 불가능하니 그대로 진행
    for sid, _ in sheets:
        sheet_data = sheet_cache.get_sheet_sift(admin_code, sid)
        if sheet_data is None:
            scores[sid] = 0
            continue
        kp_t, des_t = sheet_data
        scores[sid] = _match_inliers(scan_des, scan_kp, des_t, kp_t)
        if scores[sid] >= VISUAL_EARLY_EXIT:
            break  # 남은 sheet는 여기보다 낮을 거라 가정 (정답 확정)
    return scores


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
    """7~11자리 token을 valid_codes(8자리 set)에 매칭.

    - 7자리: 어느 위치에 어떤 자리 삽입해도 valid면 후보
    - 8자리: 정확 일치 또는 1자리 substitution이 valid면 후보
    - 9자리: 어느 1자리 빼도 valid면 후보
    - 10~11자리: 모든 8자리 연속 substring을 valid와 교차검증
      (OCR이 '(39010330)'을 '2539010330'처럼 앞뒤 노이즈와 병합한 케이스)
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
    elif L in (10, 11):
        for i in range(L - 7):
            t = token[i:i + 8]
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
            if d.isdigit() and 7 <= len(d) <= 11:
                fuzzy_cands.update(_fuzzy_8digit_match(d, shp_codes))
        for tok in re.findall(r'\d{7,11}', text):
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

    # Tier 4.5: valid_codes 상대 2-sub fuzzy (valid_codes가 작을 때만 안전)
    # OCR이 prefix 2자리를 한 번에 오독한 경우 회수 (예: "80020310" → "39020310").
    # shp_codes(3561개)로 확장하면 false positive 폭증이라 valid_codes로만 한정.
    if valid_codes is not None and len(valid_codes) <= 100:
        tokens = {re.sub(r'\s', '', m.group(1))
                  for m in re.finditer(r'\(\s*([\d\s]+?)\s*\)', text)}
        tokens.update(re.findall(r'\d{7,11}', text))
        two_sub = set()
        for tok in tokens:
            if not (tok.isdigit() and 7 <= len(tok) <= 11):
                continue
            # 8자리가 아니면 모든 8자리 윈도우로 쪼개 비교
            windows = ([tok] if len(tok) == 8
                       else [tok[i:i + 8] for i in range(len(tok) - 7)])
            for win in windows:
                for vc in valid_codes:
                    diff = sum(a != b for a, b in zip(win, vc))
                    if diff <= 2:
                        two_sub.add(vc)
        if two_sub:
            return list(two_sub)

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


def ocr_admin_code(scan_img, valid_codes=None, shp_index=None):
    """헤더 crop → 0.25x 다운스케일 → 단일 OCR 패스 → admin_code 추출.

    다운스케일만으로 정확도+속도가 다중 variant보다 낫다는 벤치 결과에 따라
    thorough 모드는 제거. 실패 시 _extract_admin_codes의 5-티어 회수가 담당.
    """
    hdr = crop_header(scan_img)
    hdr = _downscale_to_width(hdr, HEADER_OCR_TARGET_W)
    text = _tesseract(hdr, '--psm 6', 'kor+eng')
    codes = _extract_admin_codes(text, valid_codes, shp_index=shp_index)
    if not codes:
        return None, 0.0
    most, votes = Counter(codes).most_common(1)[0]
    return most, votes / max(1, len(codes))


# ============================================================
# 시트 PDF 캐시 + sheet bbox 계산
# ============================================================
# LABEL_OFFSET_X/Y_PT: _legacy/common.py에서 import (Stage 1과 공유)


class SheetCache:
    """분할 PDF 렌더 + SIFT keypoint + 메인 정합 bbox 캐시."""

    def __init__(self, pdf_input_dir, pdf_main_dir,
                 sheet_match_scale=0.25, cache_dir=None,
                 bbox_cache_path=None, shp_path=None,
                 render_dpi=300):
        self.pdf_input_dir = pdf_input_dir
        self.pdf_main_dir = pdf_main_dir
        self.scale = sheet_match_scale
        self.cache_dir = cache_dir or '/tmp/_sheet_cache'
        self.shp_path = shp_path            # skeleton ICP용 SHP
        self.render_dpi = render_dpi        # split PDF 렌더 DPI (기본 300)
        os.makedirs(self.cache_dir, exist_ok=True)
        self._sheet_meta = {}      # admin_code → {sheet_id: pdf_path}
        self._main_pdfs = {}       # admin_code → main pdf path (PDF 라벨 추출용)
        self._main_sift = {}       # admin_code → (g_main_map, kp, des, main_bbox, main_jgw)
        self._sheet_sift = {}      # (admin, sheet) → (kp, des)  scan 매칭용
        self._sheet_world_bbox = {}
        self._label_bboxes = {}    # admin_code → {sheet_id: bbox} 계산 캐시
        self._label_logged = set() # 로그 1회/admin
        self._shp_gdf = None       # SHP lazy load
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

    def get_sheet_sift(self, admin_code, sheet_id):
        """분할 PDF의 지도영역 SIFT — scan ↔ sheet 매칭용.

        식별 단계(Stage 2)에서 스캔의 sheet_id를 OCR 대신 SIFT로 결정하기 위해
        사용. 메인 정합용 SIFT(get_main_sift_for_sheet_align)와 별개 캐시.
        디스크 pickle 캐시 → 재실행 가속.
        """
        import pickle
        key = (admin_code, sheet_id)
        if key in self._sheet_sift:
            return self._sheet_sift[key]
        meta = self._sheet_meta.get(admin_code, {})
        pdf_path = meta.get(sheet_id)
        if pdf_path is None:
            return None
        cache_pkl = os.path.join(
            self.cache_dir, f'sheet_sift_{admin_code}_{sheet_id}.pkl')
        if os.path.exists(cache_pkl):
            try:
                with open(cache_pkl, 'rb') as f:
                    data = pickle.load(f)
                kp = [cv2.KeyPoint(x=p[0], y=p[1], size=p[2], angle=p[3])
                      for p in data['kp']]
                self._sheet_sift[key] = (kp, data['des'])
                return self._sheet_sift[key]
            except Exception:
                pass
        # 300dpi 캐시 공유 (Stage 3 export와) + 메모리에서 scale=0.5 다운 → SIFT
        sheet_img = self._render_pdf(pdf_path)
        sheet_map, _ = extract_map_region(sheet_img)
        g = self._preprocess(sheet_map, scale=0.5)
        sift = cv2.SIFT_create(nfeatures=5000, contrastThreshold=0.04)
        kp, des = sift.detectAndCompute(g, None)
        self._sheet_sift[key] = (kp, des)
        try:
            with open(cache_pkl, 'wb') as f:
                pickle.dump({
                    'kp': [(k.pt[0], k.pt[1], k.size, k.angle) for k in kp],
                    'des': des,
                }, f)
        except Exception:
            pass
        return self._sheet_sift[key]

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

    _body_cache = None
    def _split_body(self, pdf_path):
        """split PDF 지도영역 crop + bbox (margin=0 캐시)."""
        if self._body_cache is None:
            self._body_cache = {}
        if pdf_path in self._body_cache:
            return self._body_cache[pdf_path]
        try:
            img = self._render_pdf(pdf_path)
            sheet_map, body_bbox = extract_map_region(img, margin=0)
        except Exception:
            self._body_cache[pdf_path] = (None, None)
            return None, None
        self._body_cache[pdf_path] = (sheet_map, body_bbox)
        return sheet_map, body_bbox

    def _shp(self):
        """SHP GeoDataFrame + spatial index lazy load."""
        if self._shp_gdf is not None or self.shp_path is None:
            return self._shp_gdf
        try:
            import geopandas as gpd
            self._shp_gdf = gpd.read_file(self.shp_path)
            _ = self._shp_gdf.sindex  # spatial index 구축 (첫 호출 1회)
        except Exception:
            self._shp_gdf = None
        return self._shp_gdf

    _scale_cache = None
    def _parse_split_scale(self, pdf_path):
        """split PDF 텍스트에서 '1:N' 축척 파싱 (결과 캐시)."""
        if self._scale_cache is None:
            self._scale_cache = {}
        if pdf_path in self._scale_cache:
            return self._scale_cache[pdf_path]
        scale = None
        try:
            doc = fitz.open(pdf_path)
            t = doc[0].get_text()
            doc.close()
            for m in re.finditer(r'1\s*:\s*([\d,]+)', t):
                try:
                    scale = int(m.group(1).replace(',', ''))
                    break
                except ValueError:
                    continue
        except Exception:
            pass
        self._scale_cache[pdf_path] = scale
        return scale

    def _extract_orange_skeleton(self, img_bgr, downscale=2):
        """이미지에서 주황 중심선 skeleton (짧은 컴포넌트 제거).

        속도 최적화: downscale=2 로 1/2 축소 후 skeletonize (픽셀 4배↓).
        반환 좌표는 원본 스케일로 환산.

        Returns:
            (N, 2) pixel coords (x, y, 원본 스케일) 또는 None
        """
        try:
            from skimage.morphology import skeletonize
            from skimage.measure import label as sklabel, regionprops
        except ImportError:
            return None
        if downscale > 1:
            img_small = cv2.resize(img_bgr, None, fx=1.0/downscale,
                                   fy=1.0/downscale, interpolation=cv2.INTER_AREA)
        else:
            img_small = img_bgr
        hsv = cv2.cvtColor(img_small, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (5, 100, 100), (25, 255, 255))
        if mask.sum() == 0:
            return None
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask_c = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        skel = skeletonize(mask_c > 0)
        lbl = sklabel(skel, connectivity=2)
        keep = np.zeros_like(skel, dtype=bool)
        # downscale 적용 시 컴포넌트 길이도 1/downscale
        min_comp = max(20, ICP_MIN_COMPONENT_PX // downscale)
        for p in regionprops(lbl):
            if p.area >= min_comp:
                keep[lbl == p.label] = True
        if not keep.any():
            return None
        ys, xs = np.where(keep)
        coords = np.column_stack([xs, ys]).astype(np.float64)
        if downscale > 1:
            coords *= downscale   # 원본 이미지 좌표로 환산
        return coords

    def _sample_nearby_shp_boundary(self, sheet_bbox, buffer=ICP_SHP_BUFFER_M):
        """sheet bbox 주변 admin 경계 점 샘플 (~1m 간격). spatial index 활용."""
        gdf = self._shp()
        if gdf is None:
            return None
        try:
            from shapely.geometry import MultiPolygon, LineString, box
        except ImportError:
            return None
        minx, miny, maxx, maxy = sheet_bbox
        q = box(minx - buffer, miny - buffer, maxx + buffer, maxy + buffer)
        # spatial index로 후보 축소 (선형 스캔 3561개 → 근방 수~수십개)
        cand_idx = list(gdf.sindex.intersection(q.bounds))
        if not cand_idx:
            return None
        near = gdf.iloc[cand_idx]
        near = near[near.geometry.intersects(q)]
        pts = []
        for _, row in near.iterrows():
            geom = row.geometry
            try:
                clipped = geom.intersection(q) if not geom.within(q) else geom
            except Exception:
                continue
            if clipped.is_empty:
                continue
            subs = clipped.geoms if hasattr(clipped, 'geoms') else [clipped]
            for s in subs:
                if not hasattr(s, 'exterior'):
                    continue
                ls = LineString(list(s.exterior.coords))
                # 샘플 간격 ~3m (ICP 수렴엔 충분)
                n = max(50, int(ls.length / 3))
                for i in range(n + 1):
                    p2 = ls.interpolate(i / n, normalized=True)
                    pts.append((p2.x, p2.y))
        return np.array(pts) if pts else None

    def _icp_translation(self, skel_px, rough_bbox, sheet_pixel_size,
                         shp_tree, max_iter=20, tol=0.01):
        """translation-only ICP. rough_bbox 기준 (dx, dy) 반환.

        MAD outlier 거부. |dx|,|dy| > MAX_SHIFT 초과 시 (0, 0, None) 반환.
        """
        minx0, miny0, maxx0, maxy0 = rough_bbox
        ps_x = sheet_pixel_size
        ps_y = -sheet_pixel_size
        tlx0, tly0 = minx0, maxy0
        dx, dy = 0.0, 0.0
        cost = None
        for _ in range(max_iter):
            wx = (tlx0 + dx) + skel_px[:, 0] * ps_x
            wy = (tly0 + dy) + skel_px[:, 1] * ps_y
            world = np.column_stack([wx, wy])
            d, idx = shp_tree.query(world)
            med = np.median(d)
            mad = np.median(np.abs(d - med)) * 1.4826
            inl = d < med + 3 * mad
            n_inl = int(inl.sum())
            if n_inl < 10:
                break
            shp_p = shp_tree.data[idx[inl]]
            offs = shp_p - world[inl]
            ddx, ddy = float(offs[:, 0].mean()), float(offs[:, 1].mean())
            dx += ddx
            dy += ddy
            cost = float(d[inl].mean())
            if abs(ddx) < tol and abs(ddy) < tol:
                break
        if cost is None or abs(dx) > ICP_MAX_SHIFT_M or abs(dy) > ICP_MAX_SHIFT_M:
            return 0.0, 0.0, None   # 발산 → rollback
        return dx, dy, cost

    def _bbox_from_body_grid(self, admin_code):
        """분할도 메타 축척 + skeleton ICP 기반 sheet world bbox.

        1. 메인 PDF 라벨 위치로 그리드 토폴로지 (row, col) 파악
        2. Stage 1 main_jgw + extract_map_region 으로 main body 영역 → 각 cell 중심 world 좌표
        3. split PDF 메타의 1:N 축척으로 정확한 pixel_size 산출
           (sheet 크기 = 이미지 pixel × metadata_ps)
        4. 이미지 주황 중심선을 skeleton으로 뽑아 주변 admin SHP 경계와 translation ICP
           → 평행이동 미세보정 (|Δ|<50m 초과 시 rollback)

        검증 (제주 14 sheet): 스케일 오차 0.03%, ICP 후 chamfer cost 0.3~0.5m
        (이전 body-grid 방식: 0.08% 스케일, ~1m 오차)

        Returns:
            {sheet_id: (minx, miny, maxx, maxy)} 또는 None
        """
        if admin_code in self._label_bboxes:
            return self._label_bboxes[admin_code]

        sheets = self._sheet_meta.get(admin_code, {})
        if not sheets:
            return None
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

        # 1) 메인 PDF 라벨 추출
        try:
            doc = fitz.open(main_pdf)
            page = doc[0]
            target_re = re.compile(rf'^{n_split}-\d+$')
            labels = {}
            for w in page.get_text("words"):
                x0, y0, _, _, text = w[:5]
                if target_re.fullmatch(text):
                    labels[text] = (x0, y0)
            doc.close()
        except Exception:
            return None
        if len(labels) < 2:
            return None

        # 2) 그리드 토폴로지
        xs_s = sorted({round(v[0] / 10) * 10 for v in labels.values()})
        ys_s = sorted({round(v[1] / 10) * 10 for v in labels.values()})
        cols, rows = len(xs_s), len(ys_s)

        def _cell(lx, ly):
            col = min(range(cols), key=lambda i: abs(xs_s[i] - round(lx / 10) * 10))
            row = min(range(rows), key=lambda i: abs(ys_s[i] - round(ly / 10) * 10))
            return row, col

        # 3) 메인 body → cell 중심 world 좌표
        try:
            main_img = _imread(main_jpg)
            _, body_bbox_pix = extract_map_region(main_img, margin=0)
        except Exception:
            return None
        bx, by, bw, bh = body_bbox_pix
        body_minx = main_jgw.top_left_x + bx * main_jgw.pixel_size_x
        body_maxx = main_jgw.top_left_x + (bx + bw) * main_jgw.pixel_size_x
        body_maxy = main_jgw.top_left_y + by * main_jgw.pixel_size_y
        body_miny = main_jgw.top_left_y + (by + bh) * main_jgw.pixel_size_y
        if body_minx > body_maxx: body_minx, body_maxx = body_maxx, body_minx
        if body_miny > body_maxy: body_miny, body_maxy = body_maxy, body_miny
        cell_w_m = (body_maxx - body_minx) / cols
        cell_h_m = (body_maxy - body_miny) / rows

        out = {}
        icp_stats = {'ok': 0, 'rollback': 0, 'no_icp': 0}
        for sid, (lx, ly) in labels.items():
            row, col = _cell(lx, ly)
            cx = body_minx + (col + 0.5) * cell_w_m
            cy = body_maxy - (row + 0.5) * cell_h_m

            # split 메타 축척 → 정확한 pixel_size
            split_pdf = sheets.get(sid)
            scale = self._parse_split_scale(split_pdf) if split_pdf else None
            if not scale:
                # 메타 없으면 cell_size/N 로 폴백
                out[sid] = (body_minx + col * cell_w_m,
                            body_maxy - (row + 1) * cell_h_m,
                            body_minx + (col + 1) * cell_w_m,
                            body_maxy - row * cell_h_m)
                icp_stats['no_icp'] += 1
                continue
            true_ps = (25.4 / self.render_dpi) * scale / 1000  # m/px

            # split body 캐시 조회 (_render_pdf + extract_map_region 1회만)
            sheet_map, body_bbox = self._split_body(split_pdf)
            if sheet_map is None:
                icp_stats['no_icp'] += 1
                continue
            sh_px, sw_px = sheet_map.shape[:2]
            w_world = sw_px * true_ps
            h_world = sh_px * true_ps
            rough = (cx - w_world / 2, cy - h_world / 2,
                     cx + w_world / 2, cy + h_world / 2)

            # skeleton ICP (SHP 있을 때만)
            gdf = self._shp()
            if gdf is None:
                out[sid] = rough
                icp_stats['no_icp'] += 1
                continue
            skel = self._extract_orange_skeleton(sheet_map)
            if skel is None or len(skel) < 100:
                out[sid] = rough
                icp_stats['no_icp'] += 1
                continue
            shp_pts = self._sample_nearby_shp_boundary(rough)
            if shp_pts is None or len(shp_pts) < 100:
                out[sid] = rough
                icp_stats['no_icp'] += 1
                continue
            from scipy.spatial import cKDTree
            shp_tree = cKDTree(shp_pts)
            dx, dy, cost = self._icp_translation(skel, rough, true_ps, shp_tree)
            if cost is None:
                out[sid] = rough
                icp_stats['rollback'] += 1
                continue
            out[sid] = (rough[0] + dx, rough[1] + dy,
                        rough[2] + dx, rough[3] + dy)
            icp_stats['ok'] += 1

        self._label_bboxes[admin_code] = out
        if icp_stats != {'ok': 0, 'rollback': 0, 'no_icp': 0}:
            self._label_bboxes[f'_stats_{admin_code}'] = icp_stats
        return out

    def compute_sheet_world_bbox(self, admin_code, sheet_id):
        """sheet world bbox. PDF 라벨(즉시) 우선, SIFT(수 초) 폴백.

        라벨 우선: 메인 PDF의 'N-i' 텍스트 좌표 + 고정 오프셋. ±2m 정확도.
        SIFT 폴백: 라벨 추출 실패(이미지 PDF 등) 시 기존 분할↔메인 매칭.
        """
        cached = self._sheet_world_bbox.get(admin_code, {}).get(sheet_id)
        if cached:
            return cached

        # === 1순위: 분할도 메타 축척 + skeleton ICP 정합 ===
        try:
            label_bboxes = self._bbox_from_body_grid(admin_code)
        except Exception as e:
            print(f'  [메타+ICP 오류→SIFT 폴백] {admin_code}: {e}')
            label_bboxes = None
        if label_bboxes and sheet_id in label_bboxes:
            bbox = label_bboxes[sheet_id]
            self._sheet_world_bbox.setdefault(admin_code, {})[sheet_id] = bbox
            if admin_code not in self._label_logged:
                stats = self._label_bboxes.get(f'_stats_{admin_code}', {})
                s = (f'ICP={stats.get("ok",0)} rollback={stats.get("rollback",0)} '
                     f'no-icp={stats.get("no_icp",0)}' if stats else '')
                n = sum(1 for k in label_bboxes if not k.startswith('_'))
                print(f'  [메타+ICP] {admin_code}: {n}개 시트 bbox 산출 {s}')
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
        sheet_map, sheet_bbox = extract_map_region(sheet_img, margin=0)

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
        # _bbox_from_body_grid 와 동일한 body crop 재사용 (margin=0 일관성)
        sheet_map, _ = self._split_body(pdf_path)
        if sheet_map is None:
            return None
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
# 통합 식별 (OCR admin + Visual sheet 하이브리드)
# ============================================================

def identify_scan(scan_jpg, sheet_cache, valid_codes, shp_index=None):
    """하이브리드 식별:
      - admin: OCR (헤더 0.25x 다운스케일 + SHP fuzzy/한글명 + valid_codes 2-sub)
      - sheet: Visual SIFT (스캔 ↔ admin 내 split PDF, Hungarian은 main_loop에서)

    반환 dict에 'sheet_scores' 포함 — admin별 Hungarian 할당에 재사용.
    sheet_id는 여기서 argmax로 잠정 결정, 최종은 main_loop이 확정.
    """
    img = _imread(scan_jpg)
    if img is None:
        return {'status': 'ERROR', 'admin_code': None, 'sheet_id': None,
                'method': 'LOAD_FAIL', 'message': 'cv2.imread 실패'}

    # admin_code OCR
    code, conf = ocr_admin_code(img, valid_codes=valid_codes,
                                shp_index=shp_index)
    if code is None:
        cmd, err = check_tesseract()
        if not cmd:
            msg = f'OCR 환경 문제: {err}'
        elif err:
            msg = f'OCR 부분 동작 (한국어 언어팩 없음): {err[:200]}'
        else:
            hdr = crop_header(img)
            raw = _tesseract(hdr, '--psm 6', 'kor+eng')
            msg = (f'OCR은 동작하나 8자리 행정코드 미검출. '
                   f'헤더 raw text(일부): {raw[:150].strip()!r}')
        return {'status': 'FAIL', 'admin_code': None, 'sheet_id': None,
                'method': 'OCR_FAIL', 'message': msg}

    # scan SIFT
    kp_s, des_s = _scan_sift(img)
    if des_s is None or len(des_s) < 100:
        return {'status': 'FAIL', 'admin_code': code, 'sheet_id': None,
                'method': 'VISUAL', 'confidence': conf,
                'message': f'스캔 SIFT 피처 부족 ({0 if des_s is None else len(des_s)})'}

    # visual sheet match
    scores = visual_sheet_match(kp_s, des_s, code, sheet_cache)
    if not scores:
        return {'status': 'FAIL', 'admin_code': code, 'sheet_id': None,
                'method': 'VISUAL', 'confidence': conf,
                'message': f'admin {code}의 split PDF 없음 or 매칭 전부 실패'}

    best_sid = max(scores, key=scores.get)
    best_inl = scores[best_sid]
    if best_inl < VISUAL_INLIER_MIN:
        return {'status': 'FAIL', 'admin_code': code, 'sheet_id': None,
                'method': 'VISUAL', 'confidence': conf,
                'sheet_scores': scores,
                'message': f'visual inlier 부족 (best={best_inl})'}

    # sheet_id는 잠정. 최종 확정은 main_loop의 admin별 Hungarian이 담당.
    # bbox/sheets_geo 출력도 Hungarian 이후에 수행.
    return {'status': 'OK', 'admin_code': code, 'sheet_id': best_sid,
            'method': 'VISUAL', 'confidence': best_inl,
            'sheet_scores': scores, 'message': ''}


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
                       bbox_cache_path=bbox_path,
                       shp_path=args.shp)
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
    rows = []  # in-memory 누적 (CSV/파일작업은 Hungarian 이후 일괄)

    # === Pass 0: split PDF SIFT 일괄 선처리 (Pass 1 cold-cache 분산 방지) ===
    all_sheets = [(a, s) for a in cache.admins_with_sheets()
                  for s, _ in cache.get_sheets(a)]
    from concurrent.futures import ThreadPoolExecutor, as_completed
    n_workers = min(8, (os.cpu_count() or 4))
    print(f'[Pass 0] split PDF SIFT 캐시 준비 ({len(all_sheets)}개, {n_workers} threads)...')
    t_pre = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futs = [ex.submit(cache.get_sheet_sift, a, s) for a, s in all_sheets]
        for _ in as_completed(futs):
            done += 1
            if done % 10 == 0 or done == len(all_sheets):
                print(f'  [{done}/{len(all_sheets)}] ({(time.time()-t_pre)/done:.1f}s/장 평균)')
    print(f'  완료: {time.time()-t_pre:.1f}s')

    # === Pass 1: OCR admin + Visual sheet match (admin별 score matrix 수집) ===
    t0 = time.time()
    for i, scan in enumerate(scans, 1):
        ti = time.time()
        r = identify_scan(scan, cache, valid_codes, shp_index=shp_index)
        dt = time.time() - ti
        rows.append({
            'scan_path': scan,
            'status': r['status'],
            'admin_code': r.get('admin_code') or '',
            'sheet_id': r.get('sheet_id') or '',
            'confidence': r.get('confidence', 0),
            'sheet_scores': r.get('sheet_scores'),
            'method': r['method'],
            'message': r['message'],
            'renamed_path': '',
            'elapsed_s': dt,
        })
        if i % 5 == 0 or i == len(scans):
            ok_c = sum(1 for x in rows if x['status'] == 'OK')
            fail_c = sum(1 for x in rows if x['status'] == 'FAIL')
            err_c = sum(1 for x in rows if x['status'] == 'ERROR')
            print(f'  [1-pass {i}/{len(scans)}] OK={ok_c} FAIL={fail_c} '
                  f'ERR={err_c} ({(time.time()-t0)/i:.1f}s/장)')

    # === Pass 2: admin별 Hungarian — 중복 수렴 제거, 전역 최적 1:1 할당 ===
    from scipy.optimize import linear_sum_assignment
    from collections import defaultdict
    admin_groups = defaultdict(list)  # admin → [(row_idx, scores dict)]
    for idx, row in enumerate(rows):
        if row['status'] == 'OK' and row['sheet_scores']:
            admin_groups[row['admin_code']].append((idx, row['sheet_scores']))

    for admin, group in admin_groups.items():
        if len(group) < 2:
            continue  # 1장이면 argmax 결과 그대로 OK
        sheets = sorted({s for _, d in group for s in d})
        M = np.array([[g_scores.get(s, 0) for s in sheets]
                      for _, g_scores in group], dtype=float)
        row_ind, col_ind = linear_sum_assignment(-M)
        for ri, cj in zip(row_ind, col_ind):
            row_idx = group[ri][0]
            assigned_sheet = sheets[cj]
            inl = int(M[ri, cj])
            prev_sheet = rows[row_idx]['sheet_id']
            if inl < VISUAL_INLIER_MIN:
                rows[row_idx]['status'] = 'FAIL'
                rows[row_idx]['sheet_id'] = ''
                rows[row_idx]['message'] = (
                    f'Hungarian 할당 inlier 부족 ({inl}) — 다른 스캔이 이 sheet 선점')
                rows[row_idx]['confidence'] = inl
            else:
                if assigned_sheet != prev_sheet:
                    rows[row_idx]['message'] = (
                        f'Hungarian 재배정: {prev_sheet}→{assigned_sheet} '
                        f'(inlier {int(rows[row_idx]["confidence"])}→{inl})')
                rows[row_idx]['sheet_id'] = assigned_sheet
                rows[row_idx]['confidence'] = inl

    # === Pass 3: 파일 작업 (identified/ 복사, _unmatched/ 복사, bbox, sheets_geo) ===
    def _move_to_unmatched(scan_path):
        if args.no_unmatched:
            return
        os.makedirs(unmatched_dir, exist_ok=True)
        dst = os.path.join(unmatched_dir, os.path.basename(scan_path))
        if not os.path.exists(dst):
            shutil.copy2(scan_path, dst)

    for row in rows:
        scan = row['scan_path']
        if row['status'] == 'OK':
            code, sid = row['admin_code'], row['sheet_id']
            # bbox + sheets_geo
            bbox = cache.compute_sheet_world_bbox(code, sid)
            if bbox is None:
                row['status'] = 'FAIL'
                row['message'] = 'sheet bbox 계산 실패'
                _move_to_unmatched(scan)
                continue
            cache.export_sheet_geo(code, sid, cache.sheets_geo_dir)
            # identified/ 복사
            if not args.no_rename:
                sub_dir = os.path.join(identified_dir, code[:2], code[:5])
                os.makedirs(sub_dir, exist_ok=True)
                ext = os.path.splitext(scan)[1]
                renamed = os.path.join(sub_dir, f'{code}_{sid}{ext}')
                if os.path.exists(renamed):
                    base = os.path.splitext(renamed)[0]
                    k = 2
                    while os.path.exists(f'{base}_{k}{ext}'):
                        k += 1
                    renamed = f'{base}_{k}{ext}'
                shutil.copy2(scan, renamed)
                row['renamed_path'] = renamed
        elif row['status'] == 'FAIL':
            _move_to_unmatched(scan)

    # === CSV 저장 ===
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scan_path', 'status', 'admin_code', 'sheet_id',
                    'confidence', 'method', 'message',
                    'renamed_path', 'elapsed_s'])
        for row in rows:
            w.writerow([row['scan_path'], row['status'], row['admin_code'],
                        row['sheet_id'], f"{row['confidence']:.1f}",
                        row['method'], row['message'], row['renamed_path'],
                        f"{row['elapsed_s']:.2f}"])

    bbox_path = os.path.join(args.out_dir, 'sheet_bboxes.json')
    with open(bbox_path, 'w') as f:
        json.dump(cache._sheet_world_bbox, f, indent=2)

    n_ok = sum(1 for r in rows if r['status'] == 'OK')
    n_fail = sum(1 for r in rows if r['status'] == 'FAIL')
    n_err = sum(1 for r in rows if r['status'] == 'ERROR')
    print(f'\n[Stage 2] 완료: OK={n_ok}, FAIL={n_fail}, ERROR={n_err}')
    print(f'  CSV: {csv_path}')
    print(f'  bbox: {bbox_path}')


if __name__ == '__main__':
    main()
