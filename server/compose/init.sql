-- GIS 검수 인프라 DB 스키마 — 좌표계 전부 EPSG:5179 (Korea 2000 / Unified CS)
-- 적용: docker exec -i gis-db-1 psql -U gis -d gis < init.sql
-- 멱등성: 재적용해도 안전하도록 IF NOT EXISTS 사용.

CREATE EXTENSION IF NOT EXISTS postgis;

-- EPSG:5179 가 spatial_ref_sys 에 존재하는지 확인 (PostGIS 기본 포함)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM spatial_ref_sys WHERE srid = 5179) THEN
    RAISE EXCEPTION 'EPSG:5179 not found in spatial_ref_sys';
  END IF;
END $$;

-- 행정리 경계: 대전 QGIS가 read/write 하는 편집 대상
CREATE TABLE IF NOT EXISTS boundary (
  gid        SERIAL PRIMARY KEY,
  geom       geometry(MultiPolygon, 5179),
  adm_cd     VARCHAR(8)  NOT NULL,
  adm_nm     VARCHAR(100),
  ri_cd      VARCHAR(10),
  ri_nm      VARCHAR(100),
  status     VARCHAR(20) DEFAULT 'draft',
  updated_at TIMESTAMPTZ DEFAULT now(),
  updated_by VARCHAR(50)
);
CREATE INDEX IF NOT EXISTS boundary_geom_idx ON boundary USING GIST (geom);
CREATE INDEX IF NOT EXISTS boundary_adm_idx  ON boundary (adm_cd);

-- merged COG 등록부: 대전 플러그인이 업로드 후 insert, 검수 웹이 read
CREATE TABLE IF NOT EXISTS cog_catalog (
  adm_cd       VARCHAR(8) PRIMARY KEY,
  s3_key       TEXT NOT NULL,
  bounds       geometry(Polygon, 5179),
  width        INT,
  height       INT,
  published_at TIMESTAMPTZ DEFAULT now()
);

-- 발주자 마크업: 검수 웹이 write, 대전 QGIS가 read
CREATE TABLE IF NOT EXISTS review_markup (
  id            SERIAL PRIMARY KEY,
  geom          geometry(Geometry, 5179),
  kind          VARCHAR(10) CHECK (kind IN ('pin','arrow','area')),
  comment       TEXT,
  target_adm_cd VARCHAR(8),
  status        VARCHAR(10) DEFAULT 'open' CHECK (status IN ('open','resolved')),
  created_by    VARCHAR(50),
  created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS review_markup_geom_idx ON review_markup USING GIST (geom);
CREATE INDEX IF NOT EXISTS review_markup_adm_idx  ON review_markup (target_adm_cd);
