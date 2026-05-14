"""명부(행정리현황) 엑셀 파서.

대전 작업자의 로컬 작업목록. 발주처가 엑셀로 제공 — 어떤 행정리를 그려야
하는지의 마스터 리스트. 행정리 작업 탭에서 read_excel 로 읽어 표시한다.

엑셀 스키마 (10컬럼):
    SIDO_CD, SIDO_NM, SIGUNGU_CD, SIGUNGU_NM, ADM_CD, ADM_NM,
    LI_NM, RI_NM, RI_CD, REMARK

컬럼명 변형 관용 (대소문자/공백/한글 별칭):
    "SIDO_CD" == "sido_cd" == "시도코드" == "SIDO CD"
"""
import re


# 엑셀 컬럼명 → 정식 컬럼명 매핑 (alias 관용)
COLUMN_ALIASES = {
    'sido_cd':    ['sido_cd', 'sidocd', '시도코드', 'sd_cd'],
    'sido_nm':    ['sido_nm', 'sidonm', '시도명', '시도명칭', 'sd_nm'],
    'sigungu_cd': ['sigungu_cd', 'sgg_cd', '시군구코드', 'sigungucd'],
    'sigungu_nm': ['sigungu_nm', 'sgg_nm', '시군구명', '시군구명칭'],
    'adm_cd':     ['adm_cd', 'admcd', '읍면동코드', '행정동코드'],
    'adm_nm':     ['adm_nm', 'admnm', '읍면동명', '행정동명', '행정동명칭'],
    'li_nm':      ['li_nm', 'linm', '법정리명', '리명'],
    'ri_nm':      ['ri_nm', 'rinm', '행정리명', '행정리명칭'],
    'ri_cd':      ['ri_cd', 'ricd', '행정리코드'],
    'remark':     ['remark', 'rmk', '비고', '특이사항'],
}

REQUIRED_COLS = ['adm_cd', 'adm_nm', 'ri_cd', 'ri_nm']


def _normalize(s):
    """컬럼명 정규화 — 공백 제거 + 소문자 + 특수문자 제거."""
    if s is None:
        return ''
    return re.sub(r'[\s_\-]+', '', str(s).strip().lower())


def detect_columns(header_row):
    """엑셀 헤더 → 표준 컬럼명 매핑. 매핑 안 된 컬럼은 무시.

    Returns:
        {엑셀_헤더: 표준_컬럼} dict, 누락된 required 컬럼 list
    """
    mapping = {}
    for h in header_row:
        hn = _normalize(h)
        if not hn:
            continue
        for canonical, aliases in COLUMN_ALIASES.items():
            if hn in {_normalize(a) for a in aliases}:
                mapping[str(h)] = canonical
                break
    found = set(mapping.values())
    missing = [c for c in REQUIRED_COLS if c not in found]
    return mapping, missing


def read_excel(path, sheet=None, limit=None):
    """엑셀 파일 → (headers, rows, mapping, missing).

    - headers: 원본 엑셀 헤더 list
    - rows: 데이터 행 (원본 순서 유지) — dict with canonical keys
    - mapping: 엑셀 헤더 → 표준 컬럼
    - missing: 누락된 required 컬럼
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        wb.close()
        return [], [], {}, REQUIRED_COLS.copy()
    headers = [str(h) if h is not None else '' for h in header_row]
    mapping, missing = detect_columns(headers)

    # 엑셀 헤더 → canonical 이름으로 변환
    col_to_canon = {i: mapping[headers[i]] for i in range(len(headers))
                    if headers[i] in mapping}
    rows = []
    for r in rows_iter:
        if all(c is None for c in r):
            continue
        rec = {}
        for i, v in enumerate(r):
            if i in col_to_canon:
                # 공백 trim, None→''
                if v is None:
                    rec[col_to_canon[i]] = ''
                else:
                    rec[col_to_canon[i]] = str(v).strip()
        # 빈 키 보정
        for c in ['sido_cd', 'sido_nm', 'sigungu_cd', 'sigungu_nm',
                  'adm_cd', 'adm_nm', 'li_nm', 'ri_nm', 'ri_cd', 'remark']:
            rec.setdefault(c, '')
        # 필수 값 체크
        if not rec['adm_cd'] or not rec['ri_cd'] or not rec['ri_nm']:
            continue
        rows.append(rec)
        if limit and len(rows) >= limit:
            break
    wb.close()
    return headers, rows, mapping, missing
