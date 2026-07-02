-- GIS 검수 인프라 DB 스키마 — 좌표계 전부 EPSG:5179 (Korea 2000 / Unified CS)
-- 적용: docker exec -i gis-db-1 psql -U gis -d gis < init.sql
-- 멱등성: 재적용해도 안전하도록 IF NOT EXISTS 사용.
--
-- 이 파일은 "신규 DB 를 한 번에 최신 스키마로" 만드는 파일이다.
-- 기존 DB 는 server/migrations/ 를 날짜순으로 적용할 것 (둘의 결과는 항상 동일해야 함).
-- 스키마를 바꿀 때는 마이그레이션 추가 + 이 파일 동기화를 한 커밋에서 한다.

CREATE EXTENSION IF NOT EXISTS postgis;

-- EPSG:5179 가 spatial_ref_sys 에 존재하는지 확인 (PostGIS 기본 포함)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM spatial_ref_sys WHERE srid = 5179) THEN
    RAISE EXCEPTION 'EPSG:5179 not found in spatial_ref_sys';
  END IF;
END $$;

-- ---------------------------------------------------------------- boundary
-- 행정리 경계: 대전 QGIS가 read/write 하는 편집 대상. 웹은 read.
CREATE TABLE IF NOT EXISTS boundary (
  gid        SERIAL PRIMARY KEY,
  geom       geometry(MultiPolygon, 5179),
  adm_cd     VARCHAR(8)  NOT NULL,
  adm_nm     VARCHAR(100),
  ri_cd      VARCHAR(10),
  ri_nm      VARCHAR(100),
  status     VARCHAR(20) DEFAULT 'draft',
  remark     TEXT,                       -- QGIS 비고(지속 메모, 상태머신 비대상)
  updated_at TIMESTAMPTZ DEFAULT now(),
  updated_by VARCHAR(50)
);
CREATE INDEX IF NOT EXISTS boundary_geom_idx ON boundary USING GIST (geom);
CREATE INDEX IF NOT EXISTS boundary_adm_idx  ON boundary (adm_cd);
-- 실제 부호(ri_cd)는 읍면 안에서 유일 강제. 빈 부호(NULL/'')는 여러 개 허용
-- (저장은 읍면 단위 전체 교체 방식이라 upsert 키가 필요 없음).
CREATE UNIQUE INDEX IF NOT EXISTS boundary_adm_ri_uniq
    ON boundary (adm_cd, ri_cd)
    WHERE ri_cd IS NOT NULL AND btrim(ri_cd) <> '';

-- ---------------------------------------------------------------- boundary_confirm
-- 행정리경계 확인 완료여부 — 작업자가 임의로 작업한 경계의 검토 완료 체크.
-- (adm_cd, ri_cd) 키 — 플러그인 재제출로 gid 가 바뀌어도 유지. 행 존재 = 완료.
-- ri_cd 컬럼은 "완료여부 저장 키" — 부호 있으면 ri_cd, 없으면 'gid:<gid>' 를 담는다
-- (부호 없는 행도 일련번호로 체크 저장 가능. 재업로드 시 gid 키는 초기화됨).
CREATE TABLE IF NOT EXISTS boundary_confirm (
    adm_cd       CHAR(8)     NOT NULL,
    ri_cd        VARCHAR(40) NOT NULL,
    confirmed_by CHAR(8),
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (adm_cd, ri_cd)
);

-- ---------------------------------------------------------------- cog_catalog
-- merged COG 등록부: 대전 플러그인이 업로드 후 insert, 검수 웹이 read
CREATE TABLE IF NOT EXISTS cog_catalog (
  adm_cd       VARCHAR(8) PRIMARY KEY,
  s3_key       TEXT NOT NULL,
  bounds       geometry(Polygon, 5179),
  width        INT,
  height       INT,
  published_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------- auth / login_log
CREATE TABLE IF NOT EXISTS auth (
    admin_cd      CHAR(8) PRIMARY KEY,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'normal'
                          CHECK (role IN ('normal', 'master')),
    contact       VARCHAR(20),               -- 담당자 업무연락처(내선번호) — 첫 로그인 시 등록
    -- 권한 레벨(마스터가 지역 계정 제어): 1=정상, 2=편집회수(열람전용), 3=접근회수(로그인불가).
    -- master 계정에는 적용되지 않음(항상 전권).
    perm_level    SMALLINT NOT NULL DEFAULT 1 CHECK (perm_level BETWEEN 1 AND 3),
    -- 권한 마지막 변경 시각. 이 시각 이전 발급된 토큰은 무효(재로그인 강제).
    perm_updated_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS login_log (
    id         BIGSERIAL PRIMARY KEY,
    admin_cd   CHAR(8),
    ip         INET,
    user_agent TEXT,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS login_log_admin_ts ON login_log(admin_cd, ts DESC);

-- ---------------------------------------------------------------- admin_node (행정읍면 마스터)
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

-- ---------------------------------------------------------------- admin_outline (행정읍면 외곽 SHP 적재본)
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

-- ---------------------------------------------------------------- review_markup (수정요청)
-- 검수 웹이 write/처리. QGIS 와는 동기화하지 않음 (작업자는 웹 화면을 보고 QGIS 로 수정).
-- lifecycle: pending →(웹 작업자 반영) applied (끝)
--            pending →(웹 작업자 반려·사유) rejected (끝)
CREATE TABLE IF NOT EXISTS review_markup (
    id            SERIAL PRIMARY KEY,
    adm_cd        CHAR(8) NOT NULL,                -- 8자리 행정읍면 코드
    kind          TEXT    NOT NULL
                          CHECK (kind IN ('add', 'delete', 'attr', 'delete_mark')),
    geom          GEOMETRY(Geometry, 5179) NOT NULL,
    attrs         JSONB,
    status        TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'applied', 'rejected')),
    version       INT  NOT NULL DEFAULT 1,         -- 낙관적 잠금
    reject_reason TEXT,
    created_by    TEXT,                            -- admin_cd 또는 master admin_cd
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by    CHAR(8),
    applied_at    TIMESTAMPTZ,
    rejected_by   CHAR(8),
    rejected_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS review_markup_adm_status ON review_markup(adm_cd, status);
CREATE INDEX IF NOT EXISTS review_markup_geom_idx   ON review_markup USING GIST(geom);

-- ---------------------------------------------------------------- markup_event (append-only 이력)
-- status 컬럼은 "현재 상태 캐시", 진실 이력은 여기. 반려→보완→재처리 루프 전 이력 보존.
CREATE TABLE IF NOT EXISTS markup_event (
    id          BIGSERIAL PRIMARY KEY,
    markup_id   INT NOT NULL REFERENCES review_markup(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    actor       TEXT,                 -- admin_cd 또는 'plugin'
    reason      TEXT,
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS markup_event_markup ON markup_event(markup_id, at);
