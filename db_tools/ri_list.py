"""특정 admin_code의 RI(행정리) 목록 로드 — ri_status 테이블 기반.

Phase 3 엑셀 탑재로 populated된 ri_status에서 admin_cd별 RI를 가져옴.
Split UX C: 사용자가 RI 선택 → 맵 편집 → 새 피처 속성 자동 부여.
"""


def load_ri_for_admin(profile, admin_code, schema='public', table='ri_status'):
    """Returns [{ri_cd, ri_nm, li_nm, remark}, ...] — ri_cd 오름차순.

    ri_status 테이블이 없으면 빈 리스트 반환 (에러 아님 — 엑셀 미탑재).
    """
    import psycopg2
    conn = psycopg2.connect(
        host=profile.host, port=profile.port,
        dbname=profile.database, user=profile.username,
        password=profile.password or None)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s
            """, (schema, table))
            if cur.fetchone() is None:
                return []
            cur.execute(f"""
                SELECT ri_cd, ri_nm, COALESCE(li_nm, ''), COALESCE(remark, '')
                FROM "{schema}"."{table}"
                WHERE adm_cd = %s
                ORDER BY ri_cd
            """, (admin_code,))
            return [dict(ri_cd=r[0], ri_nm=r[1], li_nm=r[2], remark=r[3])
                    for r in cur.fetchall()]
    finally:
        conn.close()
