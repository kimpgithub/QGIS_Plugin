-- 2026-05-18 행정리 검수 웹 Phase 1
-- auth/login_log/admin_node 신규, review_markup 스키마 교체.
-- 사전 조건: review_markup 0건 (drop/recreate 안전). boundary/cog_catalog 는 손대지 않음.

BEGIN;

-- ---------------------------------------------------------------- auth
CREATE TABLE IF NOT EXISTS auth (
    admin_cd      CHAR(8) PRIMARY KEY,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'normal'
                          CHECK (role IN ('normal', 'master')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- login_log
CREATE TABLE IF NOT EXISTS login_log (
    id         BIGSERIAL PRIMARY KEY,
    admin_cd   CHAR(8),
    ip         INET,
    user_agent TEXT,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS login_log_admin_ts ON login_log(admin_cd, ts DESC);

-- ---------------------------------------------------------------- admin_node (마스터 트리)
CREATE TABLE IF NOT EXISTS admin_node (
    adm_cd   CHAR(8) PRIMARY KEY,
    adm_nm   TEXT NOT NULL,
    sgg_cd   CHAR(5) NOT NULL,
    sgg_nm   TEXT NOT NULL,
    sido_cd  CHAR(2) NOT NULL,
    sido_nm  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS admin_node_sgg  ON admin_node(sgg_cd);
CREATE INDEX IF NOT EXISTS admin_node_sido ON admin_node(sido_cd);

-- ---------------------------------------------------------------- review_markup 교체
-- 사전 확인: review_markup 0건. 데이터 손실 없음.
-- 기존 스키마(kind pin/arrow/area, status open/resolved, target_adm_cd, comment) 제거 →
-- 신규 스키마(kind add/delete/attr, status pending/applied/rejected, adm_cd, attrs JSONB).
DROP TABLE IF EXISTS review_markup;

CREATE TABLE review_markup (
    id            SERIAL PRIMARY KEY,
    adm_cd        CHAR(8) NOT NULL,                -- 8자리 행정읍면 코드
    kind          TEXT    NOT NULL
                          CHECK (kind IN ('add', 'delete', 'attr')),
    geom          GEOMETRY(Geometry, 5179) NOT NULL,
    attrs         JSONB,
    status        TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'applied', 'rejected')),
    reject_reason TEXT,
    created_by    TEXT,                            -- admin_cd 또는 master admin_cd
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at    TIMESTAMPTZ,
    rejected_at   TIMESTAMPTZ
);
CREATE INDEX review_markup_adm_status ON review_markup(adm_cd, status);
CREATE INDEX review_markup_geom_idx   ON review_markup USING GIST(geom);

-- ---------------------------------------------------------------- admin_outline (행정경계 SHP 적재 대상, Phase 1 후속)
-- 현재는 빈 테이블만. /web/admin_line 은 501 stub. SHP 도착 후 ogr2ogr 적재.
CREATE TABLE IF NOT EXISTS admin_outline (
    adm_cd   CHAR(8) PRIMARY KEY,
    adm_nm   TEXT,
    sgg_cd   CHAR(5),
    sgg_nm   TEXT,
    sido_cd  CHAR(2),
    sido_nm  TEXT,
    geom     GEOMETRY(MultiPolygon, 5179) NOT NULL
);
CREATE INDEX IF NOT EXISTS admin_outline_geom_idx ON admin_outline USING GIST(geom);

COMMIT;
