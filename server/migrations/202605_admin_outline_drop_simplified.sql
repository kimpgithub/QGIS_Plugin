-- 2026-05-19 — admin_outline.geom_simplified 컬럼 제거.
-- 검수 정밀도 우선 — /api/admin_outline 은 원본 geom 을 그대로 서빙한다.
-- admin_outline_geom_idx (GIST on geom) 는 그대로 유지.

DROP INDEX IF EXISTS admin_outline_geom_simplified_gix;
ALTER TABLE admin_outline DROP COLUMN IF EXISTS geom_simplified;
ANALYZE admin_outline;
