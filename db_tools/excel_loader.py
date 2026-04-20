"""행정리현황 엑셀 → PostGIS 업서트.

화면정의서 S12 "행정리현황 엑셀 파일을 post에 넣어 놓고" 대응.

엑셀 스키마 (image5, 10컬럼):
    SIDO_CD, SIDO_NM, SIGUNGU_CD, SIGUNGU_NM, ADM_CD, ADM_NM,
    LI_NM, RI_NM, RI_CD, REMARK

컬럼명 변형 관용 (대소문자/공백/한글 별칭):
    "SIDO_CD" == "sido_cd" == "시도코드" == "SIDO CD"

업서트 키: (adm_cd, ri_cd) — 같은 행정리는 덮어씀.
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


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {schema}.{table} (
    id          SERIAL PRIMARY KEY,
    sido_cd     VARCHAR(2),
    sido_nm     VARCHAR(100),
    sigungu_cd  VARCHAR(5),
    sigungu_nm  VARCHAR(100),
    adm_cd      VARCHAR(8)  NOT NULL,
    adm_nm      VARCHAR(100),
    li_nm       VARCHAR(100),
    ri_nm       VARCHAR(100) NOT NULL,
    ri_cd       VARCHAR(10)  NOT NULL,
    remark      TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (adm_cd, ri_cd)
);
CREATE INDEX IF NOT EXISTS idx_{table}_adm_cd ON {schema}.{table} (adm_cd);
CREATE INDEX IF NOT EXISTS idx_{table}_sigungu_cd ON {schema}.{table} (sigungu_cd);
"""

UPSERT_SQL = """
INSERT INTO {schema}.{table}
    (sido_cd, sido_nm, sigungu_cd, sigungu_nm, adm_cd, adm_nm,
     li_nm, ri_nm, ri_cd, remark)
VALUES %s
ON CONFLICT (adm_cd, ri_cd) DO UPDATE SET
    sido_cd    = EXCLUDED.sido_cd,
    sido_nm    = EXCLUDED.sido_nm,
    sigungu_cd = EXCLUDED.sigungu_cd,
    sigungu_nm = EXCLUDED.sigungu_nm,
    adm_nm     = EXCLUDED.adm_nm,
    li_nm      = EXCLUDED.li_nm,
    ri_nm      = EXCLUDED.ri_nm,
    remark     = EXCLUDED.remark,
    updated_at = now();
"""


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


def upsert(profile, rows, schema='public', table='ri_status'):
    """행 리스트를 PostGIS에 업서트.

    Args:
        profile: PGProfile
        rows: [{sido_cd, sido_nm, ..., ri_cd, remark}, ...]
        schema, table: 대상

    Returns:
        {'inserted': n, 'updated': n, 'errors': []}  — 실제로 PG가
        INSERT/UPDATE 구분 안 알려줘서 합산(affected)만 반환. 여기선
        총 처리 건수 'affected' 로 보고.
    """
    import psycopg2
    from psycopg2.extras import execute_values
    if not rows:
        return {'affected': 0, 'errors': ['탑재 행 0건']}
    values = [
        (r['sido_cd'], r['sido_nm'], r['sigungu_cd'], r['sigungu_nm'],
         r['adm_cd'], r['adm_nm'], r['li_nm'], r['ri_nm'], r['ri_cd'],
         r['remark'])
        for r in rows
    ]
    conn = psycopg2.connect(
        host=profile.host, port=profile.port,
        dbname=profile.database, user=profile.username,
        password=profile.password or None)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL.format(schema=schema, table=table))
            execute_values(
                cur,
                UPSERT_SQL.format(schema=schema, table=table),
                values, page_size=500)
            # row count — UPDATE도 포함
            affected = cur.rowcount
        return {'affected': affected, 'errors': []}
    finally:
        conn.close()
