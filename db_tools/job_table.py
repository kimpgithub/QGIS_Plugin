"""bnd_job_pg (행정리 작업 DB) 스키마 생성/확인.

화면정의서 S13 "bnd_job_pg 만 수정이 되고" — 이 테이블이 편집 대상.

스키마:
  gid         SERIAL PK
  geom        geometry(MultiPolygon, 5179)   -- 행정리 경계 폴리곤
  adm_cd      VARCHAR(8)  NOT NULL           -- 상위 읍면동 코드
  adm_nm      VARCHAR(100)                   -- 상위 읍면동명
  ri_cd       VARCHAR(10) NOT NULL           -- 행정리 코드 (image5 기준)
  ri_nm       VARCHAR(100)                   -- 행정리명
  status      VARCHAR(20) DEFAULT 'draft'    -- draft/done 등 작업 상태
  created_at, updated_at

인덱스:
  GIST (geom)
  btree (adm_cd)
  UNIQUE (adm_cd, ri_cd)   -- 동일 행정리 중복 방지
"""


DEFAULT_SRID = 5179


def _ddl(schema, table, srid=DEFAULT_SRID):
    return f"""
CREATE TABLE IF NOT EXISTS "{schema}"."{table}" (
    gid         SERIAL PRIMARY KEY,
    geom        geometry(MultiPolygon, {srid}),
    adm_cd      VARCHAR(8)   NOT NULL,
    adm_nm      VARCHAR(100),
    ri_cd       VARCHAR(10)  NOT NULL,
    ri_nm       VARCHAR(100),
    status      VARCHAR(20)  DEFAULT 'draft',
    created_at  TIMESTAMPTZ  DEFAULT now(),
    updated_at  TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS "{table}_geom_gist"
    ON "{schema}"."{table}" USING GIST (geom);
CREATE INDEX IF NOT EXISTS "{table}_adm_cd_idx"
    ON "{schema}"."{table}" (adm_cd);
CREATE UNIQUE INDEX IF NOT EXISTS "{table}_adm_ri_uniq"
    ON "{schema}"."{table}" (adm_cd, ri_cd);
"""


def table_exists(profile, schema, table):
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
            return cur.fetchone() is not None
    finally:
        conn.close()


def ensure_table(profile, schema='public', table='bnd_job_pg',
                 srid=DEFAULT_SRID):
    """없으면 CREATE, 있으면 no-op. (columns, row_count) 반환."""
    import psycopg2
    conn = psycopg2.connect(
        host=profile.host, port=profile.port,
        dbname=profile.database, user=profile.username,
        password=profile.password or None)
    try:
        with conn, conn.cursor() as cur:
            # PostGIS 확장 확인
            cur.execute("SELECT 1 FROM pg_extension WHERE extname='postgis'")
            if cur.fetchone() is None:
                raise RuntimeError(
                    'PostGIS 확장이 설치되지 않음. '
                    '관리자에게 "CREATE EXTENSION postgis" 실행 요청')
            cur.execute(_ddl(schema, table, srid))
            cur.execute(f"""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
            """, (schema, table))
            cols = cur.fetchall()
            cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
            n = cur.fetchone()[0]
        return cols, n
    finally:
        conn.close()


def get_stats(profile, schema, table):
    """행 수 + 고유 admin 수 + status별 통계."""
    import psycopg2
    conn = psycopg2.connect(
        host=profile.host, port=profile.port,
        dbname=profile.database, user=profile.username,
        password=profile.password or None)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
            total = cur.fetchone()[0]
            cur.execute(f'SELECT COUNT(DISTINCT adm_cd) '
                        f'FROM "{schema}"."{table}"')
            admins = cur.fetchone()[0]
            cur.execute(f'SELECT status, COUNT(*) '
                        f'FROM "{schema}"."{table}" GROUP BY status')
            by_status = dict(cur.fetchall())
        return {'total': total, 'admins': admins, 'by_status': by_status}
    finally:
        conn.close()
