"""Stage 2: 스캔 식별 (행정코드 + 시트번호)

각 스캔의 (admin_code, sheet_id)를 자동 식별.

admin_code : 헤더 OCR (한글명 + 8자리 fuzzy) — 기존 로직
sheet_id   : 좌상단 큰 'N-i' 라벨 OCR — 다운스케일 + threshold fallback chain
             admin의 valid 시트 집합(분할 PDF 파일명 인덱스)으로 결과 필터
sheet bbox : PDF 메타데이터만 사용 (메인 PDF 'N-i' 라벨 좌표 + 1:N 축척)
             ICP / SIFT 폴백 없음 — Stage 4 병합/Stage 3 워핑에 ±수m 충분

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
    from .common import (
        parse_jgw, extract_map_region, find_main_image,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
    )
except ImportError:
    from gis_scan_tools.tools.common import (
        parse_jgw, extract_map_region, find_main_image,
        imread_unicode as _imread, imwrite_unicode as _imwrite,
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
            [cmd, tmp.name, '-', '-l', lang, '--oem', '1'] + config.split(),
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
        args = [cmd, tmp.name, '-', '-l', lang, '--oem', '1', '--psm', psm]
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

# sheet_id OCR 설정 — 스캔 좌상단 'N-i' 대형 라벨 추출
SHEET_OCR_CROP_H = 0.22       # 스캔 상단 비율
SHEET_OCR_CROP_W = 0.30       # 스캔 좌측 비율
SHEET_OCR_MIN_HEIGHT_PX = 60  # OCR 토큰 최소 높이 (다운스케일 후 입력 기준)
# OCR 문자 오인식 보정 — morphology 후 7의 위쪽 가로획이 얇아져 /로 읽히는 등
SHEET_OCR_CHAR_FIX = {'/': '7', '[': '7', ']': '7', 'T': '7',
                       '|': '1', 'l': '1', 'I': '1', 'i': '1',
                       'O': '0', 'o': '0', 'L': '1'}
# OCR whitelist — char fix 매핑 char 도 포함해야 7→/ 등 confusion 회수 가능
SHEET_OCR_WHITELIST = '0123456789-/[]T|lIiOoL'

# 검은 잉크 필터 (HSV) — 라벨은 검정, 행정명·경계선은 빨강 → 채도로 분리
SHEET_OCR_BLACK_V_MAX = 100   # 밝기 임계 (V ≤)
SHEET_OCR_BLACK_S_MAX = 60    # 채도 임계 (S ≤) — 무채색만
# 두께 필터 — erosion-dilation. 라벨 stroke ~30-50px, 잡문자·경계 ~5-10px
SHEET_OCR_EROSION_PX = 8
# OCR 입력 다운스케일 (Tesseract 훈련 분포 정합)
SHEET_OCR_INPUT_SCALE = 0.35


def _downscale_to_width(img, target_w):
    h, w = img.shape[:2]
    if w <= target_w:
        return img
    s = target_w / w
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)


def _tesseract_tsv(img, psm='11', whitelist='', lang='eng'):
    """tesseract TSV 출력 → (text, left, top, width, height) 리스트.

    whitelist 지정 시 tessedit_char_whitelist 로 문자 제한.
    """
    cmd, _ = check_tesseract()
    if not cmd:
        return []
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    tmp.close()
    try:
        _imwrite(tmp.name, img)
        args = [cmd, tmp.name, '-', '-l', lang, '--oem', '1', '--psm', psm]
        if whitelist:
            args += ['-c', f'tessedit_char_whitelist={whitelist}']
        args.append('tsv')
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=20, **_SUBPROCESS_KW)
        out = []
        for ln in r.stdout.strip().split('\n')[1:]:
            parts = ln.split('\t')
            if len(parts) < 12:
                continue
            text = parts[11].strip()
            if not text:
                continue
            try:
                left, top, width, height = (int(parts[6]), int(parts[7]),
                                             int(parts[8]), int(parts[9]))
            except ValueError:
                continue
            out.append((text, left, top, width, height))
        return out
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _add_sheet_candidate(candidates, text, ht, valid_sheets,
                          forced_prefix=None):
    """OCR 토큰 → digit 추출 후 valid_sheets 매치되는 sid 후보 dict 누적.

    forced_prefix: valid_sheets 가 동일 prefix 공유 시 (예: {7-1, 7-2, ..., 7-7})
        OCR 첫자리 무시하고 강제. morphology 후 7→1/5→9 류 오인식 회수.
    """
    fixed = ''.join(SHEET_OCR_CHAR_FIX.get(c, c) for c in text)
    digits = re.sub(r'\D', '', fixed)
    if len(digits) < 2:
        return
    prefix = forced_prefix if forced_prefix else digits[0]
    sid = f'{prefix}-{digits[-1]}'
    if valid_sheets and sid not in valid_sheets:
        return
    cnt, mh = candidates.get(sid, (0, 0))
    candidates[sid] = (cnt + 1, max(mh, ht))


def _isolate_sheet_label(scan_img, erosion_px=None):
    """좌상단 22%×30% 크롭 → HSV 검은 잉크 마스크 → 두께 필터 → OCR 입력.

    라벨은 검정(어둡고 무채색), 행정명·경계선은 빨강(채도 높음) → 채도로 분리.
    erosion-dilation으로 라벨처럼 두꺼운 stroke만 남김.

    Args:
        erosion_px: None → SHEET_OCR_EROSION_PX (기본 8). 적응 호출 시 다른값 지정

    Returns:
        OCR-ready (black on white, 다운스케일된) 이미지. None if scan_img is None.
    """
    if scan_img is None:
        return None
    if erosion_px is None:
        erosion_px = SHEET_OCR_EROSION_PX
    h, w = scan_img.shape[:2]
    crop = scan_img[:int(h * SHEET_OCR_CROP_H), :int(w * SHEET_OCR_CROP_W)]
    if crop.ndim == 2:
        v = crop
        s = np.zeros_like(crop)
    else:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2]
        s = hsv[:, :, 1]
    black = ((v <= SHEET_OCR_BLACK_V_MAX) &
             (s <= SHEET_OCR_BLACK_S_MAX)).astype(np.uint8) * 255
    ksz = erosion_px * 2 + 1
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksz, ksz))
    eroded = cv2.erode(black, k)
    thick = cv2.dilate(eroded, k)
    feed = cv2.bitwise_not(thick)
    sc = SHEET_OCR_INPUT_SCALE
    return cv2.resize(feed, None, fx=sc, fy=sc,
                      interpolation=cv2.INTER_AREA)


def ocr_sheet_id(scan_img, valid_sheets=None):
    """좌상단 큰 'N-i' 라벨 OCR — 검은 잉크 필터 + 두께 필터 + 다중 PSM.

    파이프라인:
      1. 22%×30% 크롭 → HSV 검은 잉크 마스크 (V≤100, S≤60)
         라벨(검정)만 남기고 빨간 행정명·경계선·맵라인 제거.
      2. erosion-dilation 두께 필터 → 굵은 라벨 stroke 만 잔존.
      3. 흑백 반전 후 0.35x 다운스케일 → PSM 11/6/7 OCR.
      4. 토큰의 hyphen은 무시, 숫자 첫·끝자리로 (prefix, idx) 구성 →
         valid_sheets 매치 후보만 등록.
      5. (vote count desc, max height desc) 정렬해 최상위 채택.

    Returns:
        sheet_id ('N-i') or None
    """
    if scan_img is None:
        return None

    # valid_sheets 가 동일 prefix 공유하면 강제 (e.g., 7-tile → {7-1...7-7}, prefix '7')
    forced_prefix = None
    if valid_sheets:
        prefixes = {s.split('-')[0] for s in valid_sheets if '-' in s}
        if len(prefixes) == 1:
            forced_prefix = next(iter(prefixes))

    # 적응 erosion — 기본 8 로 시도 → 실패 시 6, 12 추가 (morphology 후 글자
    # 깎임 정도가 케이스마다 달라 단일 erosion 으로는 일부 라벨 누락. 다른
    # erosion 으로 7→1/5→9 류 회수)
    candidates = {}  # sid -> (vote_count, max_ht)
    for erosion in (SHEET_OCR_EROSION_PX, 6, 12):
        feed = _isolate_sheet_label(scan_img, erosion_px=erosion)
        if feed is None:
            continue
        for psm in ('11', '6', '7'):
            tokens = _tesseract_tsv(feed, psm=psm,
                                     whitelist=SHEET_OCR_WHITELIST)
            for text, _l, _t, _wd, ht in tokens:
                if ht < SHEET_OCR_MIN_HEIGHT_PX:
                    continue
                _add_sheet_candidate(candidates, text, ht, valid_sheets,
                                      forced_prefix=forced_prefix)
        if candidates:
            # 첫 erosion 에서 후보 얻었으면 추가 erosion 생략 (성능)
            break

    if not candidates:
        return None
    sid, _ = max(candidates.items(), key=lambda kv: (kv[1][0], kv[1][1]))
    return sid


def dump_sheet_ocr_debug(scan_img, scan_name, out_dir, valid_sheets=None):
    """sheet_id OCR 디버그 덤프 — FAIL/CONFLICT 케이스 분석용.

    out_dir/{scan_name}/ 에 다음 산출:
      crop.jpg     원본 좌상단 컬러 크롭
      black.png    검은 잉크 마스크 (HSV 필터 결과)
      thick.png    두께 필터 후 (라벨 글리프만)
      feed.png     OCR 입력 (반전 + 다운스케일)
      tokens.csv   psm × token × h × sid_cand × valid
    """
    if scan_img is None:
        return
    d = os.path.join(out_dir, scan_name)
    os.makedirs(d, exist_ok=True)
    h, w = scan_img.shape[:2]
    crop = scan_img[:int(h * SHEET_OCR_CROP_H), :int(w * SHEET_OCR_CROP_W)]
    _imwrite(os.path.join(d, 'crop.jpg'), crop,
             [cv2.IMWRITE_JPEG_QUALITY, 85])

    if crop.ndim == 2:
        v = crop; s = np.zeros_like(crop)
    else:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2]; s = hsv[:, :, 1]
    black = ((v <= SHEET_OCR_BLACK_V_MAX) &
             (s <= SHEET_OCR_BLACK_S_MAX)).astype(np.uint8) * 255
    _imwrite(os.path.join(d, 'black.png'), black)

    ksz = SHEET_OCR_EROSION_PX * 2 + 1
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksz, ksz))
    thick = cv2.dilate(cv2.erode(black, k), k)
    _imwrite(os.path.join(d, 'thick.png'), thick)

    feed = cv2.bitwise_not(thick)
    sc = SHEET_OCR_INPUT_SCALE
    feed_s = cv2.resize(feed, None, fx=sc, fy=sc,
                        interpolation=cv2.INTER_AREA)
    _imwrite(os.path.join(d, 'feed.png'), feed_s)

    tokens_csv = os.path.join(d, 'tokens.csv')
    with open(tokens_csv, 'w', newline='', encoding='utf-8') as f:
        cw = csv.writer(f)
        cw.writerow(['psm', 'text', 'h', 'sid_cand', 'valid'])
        for psm in ('11', '6', '7'):
            tokens = _tesseract_tsv(feed_s, psm=psm,
                                    whitelist='0123456789-')
            for text, _l, _t, _wd, ht in tokens:
                fixed = ''.join(SHEET_OCR_CHAR_FIX.get(c, c) for c in text)
                digits = re.sub(r'\D', '', fixed)
                sid_cand = (f'{digits[0]}-{digits[-1]}'
                            if len(digits) >= 2 else '')
                vmark = ('y' if (sid_cand and
                                 (not valid_sheets or sid_cand in valid_sheets)
                                 and ht >= SHEET_OCR_MIN_HEIGHT_PX)
                         else 'n')
                cw.writerow([psm, text, ht, sid_cand, vmark])


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
    1. 괄호 안 정확 8자리 + valid_codes 매칭 + 한글명 cross-check
       (paren 결과가 한글명과 불일치 시 reject → 다음 tier 폴백)
    2. SHP 한글명 + 자릿수 fuzzy 교차검증 (둘 다 같은 코드 가리키면 강한 신호)
    3. SHP 한글명 단독 매칭 (코드 OCR 실패해도 한글명 인식되면 회수)
    4. SHP fuzzy 7~9자리 단독 매칭
    5. 폴백: 모든 8자리 + 공백 복원 (기존)

    valid_codes (PDF 보유 admin) 주어지면 최종 필터링.
    """
    # 한글명 cross-check 사전 계산 (Tier 1 검증 + Tier 2-3 재사용)
    name_cands_set = set()
    if shp_index is not None:
        name_cands_set = set(_extract_korean_admin_names(
            text, shp_index['by_name']))

    # Tier 1: 괄호 안 정확 8자리 + 한글명 cross-check
    paren_candidates = []
    for m in re.finditer(r'\(\s*([\d\s]+?)\s*\)', text):
        digits = re.sub(r'\s', '', m.group(1))
        if len(digits) == 8 and digits.isdigit():
            paren_candidates.append(digits)
    if valid_codes is not None:
        paren_valid = [c for c in paren_candidates if c in valid_codes]
        if paren_valid:
            # 한글명 추출됐으면 cross-check 강제 (OCR 끝자리 오인 차단)
            if name_cands_set:
                confirmed = [c for c in paren_valid if c in name_cands_set]
                if confirmed:
                    return confirmed
                # paren-name 불일치 → 다음 tier 폴백 (return 하지 않음)
            else:
                return paren_valid
    elif paren_candidates:
        if name_cands_set:
            confirmed = [c for c in paren_candidates if c in name_cands_set]
            if confirmed:
                return confirmed
        else:
            return paren_candidates

    # Tier 2~4: SHP 활용 회수 (name_cands_set 은 Tier 1 사전 계산 재사용)
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
        name_cands = name_cands_set

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
# LABEL_OFFSET_X/Y_PT: common.py에서 import (Stage 1과 공유)


class SheetCache:
    """분할/메인 PDF 인덱스 + 메타 기반 sheet world bbox 산출."""

    def __init__(self, pdf_input_dir, pdf_main_dir,
                 cache_dir=None, bbox_cache_path=None,
                 render_dpi=200):
        self.pdf_input_dir = pdf_input_dir or None
        self.pdf_main_dir = pdf_main_dir or None
        self.cache_dir = cache_dir or '/tmp/_sheet_cache'
        self.render_dpi = render_dpi        # split PDF 렌더 DPI
        os.makedirs(self.cache_dir, exist_ok=True)
        self._sheet_meta = {}      # admin_code → {sheet_id: pdf_path}
        self._main_pdfs = {}       # admin_code → main pdf path (PDF 라벨 추출용)
        self._sheet_world_bbox = {}
        self._label_bboxes = {}    # admin_code → {sheet_id: bbox} 계산 캐시
        self._label_logged = set() # 로그 1회/admin
        self._main_body_world = None  # admin → (minx, miny, maxx, maxy) — 메모리+디스크 캐시
        if self.pdf_input_dir and os.path.isdir(self.pdf_input_dir):
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

    def get_valid_sheet_ids(self, admin_code):
        """분할 PDF 파일명에서 뽑은 유효 sheet_id 집합 — OCR 필터용."""
        return set(self._sheet_meta.get(admin_code, {}).keys())

    def _render_pdf(self, pdf_path, dpi=None):
        dpi = dpi or self.render_dpi
        cache_jpg = os.path.join(self.cache_dir,
                                 os.path.basename(pdf_path) + '.jpg')
        if os.path.exists(cache_jpg):
            return _imread(cache_jpg)
        doc = fitz.open(pdf_path)
        pix = doc[0].get_pixmap(dpi=dpi)
        pix.save(cache_jpg)
        doc.close()
        return _imread(cache_jpg)

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

    def _get_main_body_world_bbox(self, admin_code, main_jgw, main_jpg):
        """메인 body world bbox.

        조회 순서:
        1. 메모리 캐시
        2. Stage 1 사이드카 `{pdf_main_dir}/{admin}.body.json`
        3. 폴백: TIF 읽어 재계산 + `_sheet_cache/main_body_bbox.json` 에 캐시
        """
        if self._main_body_world is None:
            self._main_body_world = {}
            # 폴백 캐시 로드 (구 Stage 1 산출물 대응)
            cache_path = os.path.join(self.cache_dir, 'main_body_bbox.json')
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, 'r') as f:
                        loaded = json.load(f)
                    for k, v in loaded.items():
                        if isinstance(v, list) and len(v) == 4:
                            self._main_body_world[k] = tuple(v)
                except Exception:
                    pass

        if admin_code in self._main_body_world:
            return self._main_body_world[admin_code]

        # Stage 1 사이드카 우선
        sidecar = os.path.join(self.pdf_main_dir, f'{admin_code}.body.json')
        if os.path.exists(sidecar):
            try:
                with open(sidecar, 'r') as f:
                    data = json.load(f)
                bbox = data.get('body_world_bbox')
                if bbox and len(bbox) == 4:
                    bbox = tuple(bbox)
                    self._main_body_world[admin_code] = bbox
                    return bbox
            except Exception:
                pass

        # 사이드카 없음 → TIF 읽어 재계산 (구 Stage 1 산출물 폴백)
        try:
            main_img = _imread(main_jpg)
            _, body_bbox_pix = extract_map_region(main_img, margin=0)
        except Exception:
            return None
        bx, by, bw, bh = body_bbox_pix
        minx = main_jgw.top_left_x + bx * main_jgw.pixel_size_x
        maxx = main_jgw.top_left_x + (bx + bw) * main_jgw.pixel_size_x
        maxy = main_jgw.top_left_y + by * main_jgw.pixel_size_y
        miny = main_jgw.top_left_y + (by + bh) * main_jgw.pixel_size_y
        if minx > maxx: minx, maxx = maxx, minx
        if miny > maxy: miny, maxy = maxy, miny
        bbox = (minx, miny, maxx, maxy)
        self._main_body_world[admin_code] = bbox

        # 폴백 캐시 저장 (재실행 가속)
        cache_path = os.path.join(self.cache_dir, 'main_body_bbox.json')
        try:
            with open(cache_path, 'w') as f:
                json.dump({k: list(v) for k, v in self._main_body_world.items()},
                          f, indent=2)
        except Exception:
            pass
        return bbox

    def _bbox_from_body_grid(self, admin_code):
        """PDF 메타데이터만으로 sheet world bbox 산출 (rough).

        1. 메인 PDF의 'N-i' 라벨 좌표로 그리드 토폴로지 (rows, cols) 파악
        2. Stage 1 main_jgw + extract_map_region 으로 main body world bbox
        3. cell 중심 = body 격자 분할
        4. sheet 크기 = split PDF '1:N' 메타 축척 × 렌더 픽셀 (없으면 cell_size 폴백)

        ICP 정합 없이 메타만 사용 → 정확도 ±수m. Stage 4 병합/Stage 3 워핑에는 충분.

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

        # 1) 메인 PDF 'N-i' 라벨 추출
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

        # 2) 라벨 좌표 → 그리드 토폴로지 (rows × cols)
        xs_s = sorted({round(v[0] / 10) * 10 for v in labels.values()})
        ys_s = sorted({round(v[1] / 10) * 10 for v in labels.values()})
        cols, rows = len(xs_s), len(ys_s)

        def _cell(lx, ly):
            col = min(range(cols), key=lambda i: abs(xs_s[i] - round(lx / 10) * 10))
            row = min(range(rows), key=lambda i: abs(ys_s[i] - round(ly / 10) * 10))
            return row, col

        # 3) 메인 body world bbox (JSON 캐시로 TIF 재읽기 회피)
        body_world = self._get_main_body_world_bbox(admin_code, main_jgw, main_jpg)
        if body_world is None:
            return None
        body_minx, body_miny, body_maxx, body_maxy = body_world
        cell_w_m = (body_maxx - body_minx) / cols
        cell_h_m = (body_maxy - body_miny) / rows

        out = {}
        for sid, (lx, ly) in labels.items():
            row, col = _cell(lx, ly)
            cx = body_minx + (col + 0.5) * cell_w_m
            cy = body_maxy - (row + 0.5) * cell_h_m

            # split 메타 축척 → 정확한 pixel_size (없으면 cell_size 폴백)
            split_pdf = sheets.get(sid)
            scale = self._parse_split_scale(split_pdf) if split_pdf else None
            if scale:
                true_ps = (25.4 / self.render_dpi) * scale / 1000  # m/px
                sheet_map, _ = self._split_body(split_pdf)
                if sheet_map is not None:
                    sh_px, sw_px = sheet_map.shape[:2]
                    w_world = sw_px * true_ps
                    h_world = sh_px * true_ps
                    out[sid] = (cx - w_world / 2, cy - h_world / 2,
                                cx + w_world / 2, cy + h_world / 2)
                    continue
            # 폴백: 단순 cell 분할
            out[sid] = (body_minx + col * cell_w_m,
                        body_maxy - (row + 1) * cell_h_m,
                        body_minx + (col + 1) * cell_w_m,
                        body_maxy - row * cell_h_m)

        self._label_bboxes[admin_code] = out
        return out

    def compute_sheet_world_bbox(self, admin_code, sheet_id):
        """sheet world bbox — PDF 메타데이터만 사용 (rough, ICP 없음)."""
        cached = self._sheet_world_bbox.get(admin_code, {}).get(sheet_id)
        if cached:
            return cached
        try:
            label_bboxes = self._bbox_from_body_grid(admin_code)
        except Exception as e:
            print(f'  [메타 bbox 오류] {admin_code}: {e}')
            return None
        if not label_bboxes or sheet_id not in label_bboxes:
            print(f'  [메타 bbox 실패] {admin_code} {sheet_id} '
                  f'(라벨 없음 or main_jgw 누락)')
            return None
        bbox = label_bboxes[sheet_id]
        self._sheet_world_bbox.setdefault(admin_code, {})[sheet_id] = bbox
        if admin_code not in self._label_logged:
            print(f'  [메타 bbox] {admin_code}: {len(label_bboxes)}개 시트')
            self._label_logged.add(admin_code)
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

        from .common import write_jgw, JGWParams, PRJ_5179
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

        # S7 ORB 매칭용 body 템플릿 (800px 폭) — 시트당 1회 캐시
        self._save_body_template(admin_code, sheet_id, sheet_map)
        return jpg_path

    def _save_body_template(self, admin_code, sheet_id, body_img,
                             target_w=800):
        """ORB 매칭용 다운스케일 body 캐시 저장.

        S7 (stage_extract_map) 가 scan↔PDF body 매칭으로 정확한 본문 영역을
        검출할 때 참조 템플릿으로 사용. 시트당 ~1MB.
        """
        out = os.path.join(self.cache_dir,
                           f'{admin_code}_{sheet_id}.body.jpg')
        if os.path.exists(out):
            return out
        h, w = body_img.shape[:2]
        if w > target_w:
            sc = target_w / w
            small = cv2.resize(body_img, None, fx=sc, fy=sc,
                                interpolation=cv2.INTER_AREA)
        else:
            small = body_img
        ok, buf = cv2.imencode('.jpg', small,
                                [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            buf.tofile(out)
        return out


# ============================================================
# 통합 식별 (OCR admin + Visual sheet 하이브리드)
# ============================================================

def identify_scan(scan_jpg, sheet_cache, valid_codes, shp_index=None,
                  allow_no_pdf=False):
    """OCR 기반 식별:
      - admin_code: 헤더 OCR (SHP fuzzy/한글명 + valid_codes 2-sub 회수)
      - sheet_id  : 좌상단 대형 'N-i' 라벨 OCR (valid_sheets 필터)

    allow_no_pdf=True 면 admin에 분할 PDF 없어도 무필터 OCR로 통과
    (status='OK_NO_PDF'). Stage 3 가 SHPGeoreferencer 폴백으로 처리.
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
                'method': 'OCR_ADMIN_FAIL', 'message': msg}

    # sheet_id OCR — admin의 valid 시트 집합으로 필터
    valid_sheets = sheet_cache.get_valid_sheet_ids(code)
    if not valid_sheets:
        if allow_no_pdf:
            # PDF 없음 → 무필터 OCR. None 이어도 통과 시키되 표시.
            sheet = ocr_sheet_id(img, valid_sheets=None)
            if sheet is None:
                return {'status': 'FAIL', 'admin_code': code, 'sheet_id': None,
                        'method': 'OCR_SHEET_NO_PDF', 'confidence': conf,
                        'message': '분할 PDF 없음 — sheet_id OCR 실패 (CSV 수동 입력 후 재실행)'}
            return {'status': 'OK_NO_PDF', 'admin_code': code, 'sheet_id': sheet,
                    'method': 'OCR_NO_PDF', 'confidence': conf,
                    'message': '분할 PDF 없음 — OCR 통과'}
        return {'status': 'FAIL', 'admin_code': code, 'sheet_id': None,
                'method': 'OCR_SHEET', 'confidence': conf,
                'message': f'admin {code}의 분할 PDF 없음'}

    sheet = ocr_sheet_id(img, valid_sheets=valid_sheets)
    if sheet is None:
        return {'status': 'FAIL', 'admin_code': code, 'sheet_id': None,
                'method': 'OCR_SHEET', 'confidence': conf,
                'message': (f'sheet 라벨 OCR 실패 '
                            f'(valid={sorted(valid_sheets)})')}

    return {'status': 'OK', 'admin_code': code, 'sheet_id': sheet,
            'method': 'OCR', 'confidence': conf, 'message': ''}


# ============================================================
# CLI
# ============================================================

def main():
    ap = argparse.ArgumentParser(description='Stage 2: 스캔 식별 (admin+sheet)')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='스캔 폴더 (재귀)')
    ap.add_argument('--pdf-input', default='',
                    help='원본 PDF 폴더 (메인+분할). 미지정 시 PDF-less 통과 모드 (--shp 필수)')
    ap.add_argument('--pdf-main', default='',
                    help='Stage 1 산출 폴더 (pdf_main_geo). 미지정 시 PDF-less 통과 모드')
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

    # PDF-less 폴백 — SHP 만 있으면 항상 ON (mixed 입력에서 admin 별 PDF 유무 자동 분기)
    # PDF 있는 admin → 정규 OCR + valid_sheets 필터
    # PDF 없는 admin → 무필터 OCR + status=OK_NO_PDF
    has_pdf_input = bool(args.pdf_input) and os.path.isdir(args.pdf_input)
    has_pdf_main = bool(args.pdf_main) and os.path.isdir(args.pdf_main)
    allow_no_pdf = bool(args.shp)
    if allow_no_pdf:
        if has_pdf_input and has_pdf_main:
            print('[Stage 2] mixed 모드 — admin 별로 PDF 있으면 정규 OCR, 없으면 OK_NO_PDF')
        else:
            print('[Stage 2] PDF-less 모드 — 모든 admin 무필터 OCR (OK_NO_PDF)')
    elif not (has_pdf_input and has_pdf_main):
        print('ERROR: --pdf-input/--pdf-main 누락 시 --shp 필수 (no-pdf 폴백용)')
        sys.exit(1)

    print(f'[Stage 2] 시트 캐시 초기화')
    bbox_path = os.path.join(args.out_dir, 'sheet_bboxes.json')
    cache = SheetCache(args.pdf_input or None, args.pdf_main or None,
                       cache_dir=os.path.join(args.out_dir, '_sheet_cache'),
                       bbox_cache_path=bbox_path)
    # Stage 3 매칭 템플릿용 sheet geo 출력 경로
    cache.sheets_geo_dir = os.path.join(args.out_dir, 'sheets_geo')
    os.makedirs(cache.sheets_geo_dir, exist_ok=True)
    valid_codes = set(cache.admins_with_sheets())
    print(f'  → {len(valid_codes)}개 admin 코드 (분할 PDF 보유)')

    shp_index = None
    if args.shp:
        try:
            shp_index = load_shp_index(args.shp)
        except Exception as e:
            print(f'  [SHP 로드 실패 → 폴백] {e}')
            shp_index = None
    else:
        print('  [SHP 미지정 → 기존 OCR 동작]')

    # PDF-less 모드: SHP 전체 코드를 valid_codes 로 합산 (Tier 4.5 fuzzy 는 ≤100 가드로 자동 비활성)
    if allow_no_pdf:
        if shp_index:
            n_before = len(valid_codes)
            valid_codes |= set(shp_index['codes'])
            print(f'  [PDF-less] SHP 코드 합산: {n_before} → {len(valid_codes)}개')
        elif not valid_codes:
            print('ERROR: PDF 도 SHP 도 없음 — valid_codes 공급원 없음')
            sys.exit(1)
    elif not valid_codes:
        print('ERROR: 분할 PDF가 없음 (filename: {8자리}_{N}-{i}.pdf)')
        sys.exit(1)

    scans = sorted(set(
        glob.glob(os.path.join(args.in_dir, '**/*.jpg'), recursive=True)
        + glob.glob(os.path.join(args.in_dir, '**/*.JPG'), recursive=True)
    ))
    scans = [s for s in scans if 'checkpoint' not in s]
    print(f'[Stage 2] 스캔 {len(scans)}장 식별 시작')

    csv_path = os.path.join(args.out_dir, '_identification.csv')
    unmatched_dir = os.path.join(args.out_dir, '_unmatched')
    identified_dir = os.path.join(args.out_dir, 'identified')
    rows = []

    # === Pass 1: OCR 식별 (admin_code + sheet_id 모두 OCR) ===
    t0 = time.time()
    for i, scan in enumerate(scans, 1):
        ti = time.time()
        r = identify_scan(scan, cache, valid_codes, shp_index=shp_index,
                          allow_no_pdf=allow_no_pdf)
        dt = time.time() - ti
        rows.append({
            'scan_path': scan,
            'status': r['status'],
            'admin_code': r.get('admin_code') or '',
            'sheet_id': r.get('sheet_id') or '',
            'confidence': r.get('confidence', 0),
            'method': r['method'],
            'message': r['message'],
            'renamed_path': '',
            'elapsed_s': dt,
        })
        if i % 5 == 0 or i == len(scans):
            ok_c = sum(1 for x in rows if x['status'] == 'OK')
            fail_c = sum(1 for x in rows if x['status'] == 'FAIL')
            err_c = sum(1 for x in rows if x['status'] == 'ERROR')
            print(f'  [{i}/{len(scans)}] OK={ok_c} FAIL={fail_c} '
                  f'ERR={err_c} ({(time.time()-t0)/i:.1f}s/장)')

    # === Pass 1.5: CONFLICT 격리 — 동일 (admin, sheet_id) 다중 OK는 양쪽 모두 FAIL ===
    key_counts = Counter((r['admin_code'], r['sheet_id']) for r in rows
                          if r['status'] in ('OK', 'OK_NO_PDF'))
    n_conflict = 0
    for r in rows:
        if r['status'] not in ('OK', 'OK_NO_PDF'):
            continue
        key = (r['admin_code'], r['sheet_id'])
        if key_counts[key] > 1:
            r['status'] = 'FAIL'
            r['method'] = 'CONFLICT'
            r['message'] = (f'CONFLICT: 동일 (admin={key[0]}, sheet={key[1]}) '
                            f'{key_counts[key]}건')
            r['sheet_id'] = ''
            n_conflict += 1
    if n_conflict:
        print(f'  [CONFLICT] {n_conflict}건 격리 (동일 (admin, sheet) 충돌)')

    # === Pass 1.6: sheet_id OCR 실패/CONFLICT 디버그 덤프 (환경 비교용) ===
    debug_dir = os.path.join(args.out_dir, '_debug_sheet_ocr')
    n_dump = 0
    for r in rows:
        if r['status'] == 'FAIL' and r['method'] in ('OCR_SHEET', 'CONFLICT'):
            img = _imread(r['scan_path'])
            if img is None:
                continue
            valid = (cache.get_valid_sheet_ids(r['admin_code'])
                     if r['admin_code'] else None)
            scan_name = os.path.splitext(os.path.basename(r['scan_path']))[0]
            dump_sheet_ocr_debug(img, scan_name, debug_dir, valid_sheets=valid)
            n_dump += 1
    if n_dump:
        print(f'  [DEBUG] sheet OCR 덤프 {n_dump}건 → {debug_dir}')

    # === Pass 2: 파일 작업 (identified/ 복사, _unmatched/ 복사, bbox, sheets_geo) ===
    def _move_to_unmatched(scan_path):
        if args.no_unmatched:
            return
        os.makedirs(unmatched_dir, exist_ok=True)
        dst = os.path.join(unmatched_dir, os.path.basename(scan_path))
        if not os.path.exists(dst):
            shutil.copy2(scan_path, dst)

    # OK_NO_PDF 는 PDF 가 없어 valid_sheets 교차검증 불가 → OCR 오류 가능성 있음
    # → identified/ 가 아닌 _unmatched/ 로 보내 2a.미식별보강 탭에서 검수
    def _copy_unmatched_renamed(scan_path, code, sid):
        """OCR 결과 admin/sheet 로 rename 해 _unmatched/ 에 복사."""
        if args.no_unmatched:
            return None
        os.makedirs(unmatched_dir, exist_ok=True)
        ext = os.path.splitext(scan_path)[1]
        dst = os.path.join(unmatched_dir, f'{code}_{sid}{ext}')
        if os.path.exists(dst):
            base = os.path.splitext(dst)[0]
            k = 2
            while os.path.exists(f'{base}_{k}{ext}'):
                k += 1
            dst = f'{base}_{k}{ext}'
        shutil.copy2(scan_path, dst)
        return dst

    for row in rows:
        scan = row['scan_path']
        if row['status'] == 'OK':
            code, sid = row['admin_code'], row['sheet_id']
            bbox = cache.compute_sheet_world_bbox(code, sid)
            if bbox is None:
                row['status'] = 'FAIL'
                row['message'] = 'sheet bbox 계산 실패'
                _move_to_unmatched(scan)
                continue
            cache.export_sheet_geo(code, sid, cache.sheets_geo_dir)
            # identified/ 복사 (정규 OK 만)
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
        elif row['status'] == 'OK_NO_PDF':
            # _unmatched/ 로 복사 (OCR 결과 admin/sheet 로 rename) — 2a 검수 대상
            renamed = _copy_unmatched_renamed(scan, row['admin_code'],
                                                row['sheet_id'])
            if renamed:
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
    n_nopdf = sum(1 for r in rows if r['status'] == 'OK_NO_PDF')
    n_fail = sum(1 for r in rows if r['status'] == 'FAIL')
    n_err = sum(1 for r in rows if r['status'] == 'ERROR')
    print(f'\n[Stage 2] 완료: OK={n_ok}, OK_NO_PDF={n_nopdf}, FAIL={n_fail}, ERROR={n_err}')
    print(f'  CSV: {csv_path}')
    print(f'  bbox: {bbox_path}')
    if n_nopdf:
        print(f'  [검수 필요] OK_NO_PDF {n_nopdf}장 → {unmatched_dir}')
        print(f'             2a.미식별보강 탭에서 OCR 결과 확인 후 정정/이동')


if __name__ == '__main__':
    main()
