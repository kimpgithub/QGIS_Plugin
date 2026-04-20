"""bnd_adm_pg에서 읍면동 리스트 + bbox 로드.

행정리 작업 탭의 리스트 표시 + 더블클릭 맵 줌에 사용.
"""


def load_admin_list(profile, schema='census_23p', table='bnd_adm_pg'):
    """Returns list of dicts:
        {adm_cd, adm_nm, sigungu_nm, sido_nm, xmin, ymin, xmax, ymax}
    """
    import psycopg2
    conn = psycopg2.connect(
        host=profile.host, port=profile.port,
        dbname=profile.database, user=profile.username,
        password=profile.password or None)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"""
                SELECT adm_cd, adm_nm, sigungu_nm, sido_nm,
                       ST_XMin(geom), ST_YMin(geom),
                       ST_XMax(geom), ST_YMax(geom)
                FROM "{schema}"."{table}"
                ORDER BY adm_cd
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        dict(adm_cd=str(r[0]), adm_nm=str(r[1] or ''),
             sigungu_nm=str(r[2] or ''), sido_nm=str(r[3] or ''),
             xmin=float(r[4]), ymin=float(r[5]),
             xmax=float(r[6]), ymax=float(r[7]))
        for r in rows
    ]
