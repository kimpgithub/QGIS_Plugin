-- 2026-05-19 — admin_outline 에 사전 단순화 컬럼 + GIST 인덱스.
-- 매 요청에서 ST_SimplifyPreserveTopology 를 돌리면 3561 폴리곤 처리에 ~13s 가 걸린다.
-- 30m 톨러런스로 한 번만 계산해 저장, 인덱스 붙여 bbox/근접 필터를 빠르게.

ALTER TABLE admin_outline
  ADD COLUMN IF NOT EXISTS geom_simplified GEOMETRY(MultiPolygon, 5179);

UPDATE admin_outline
   SET geom_simplified = ST_Multi(ST_SimplifyPreserveTopology(geom, 30))
 WHERE geom_simplified IS NULL;

CREATE INDEX IF NOT EXISTS admin_outline_geom_simplified_gix
  ON admin_outline USING GIST (geom_simplified);

ANALYZE admin_outline;
