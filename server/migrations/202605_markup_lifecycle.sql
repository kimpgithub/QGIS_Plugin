-- 2026-05-29 — 마크업 lifecycle 정식화 + 비고 서버화 + 이력 테이블.
--
-- 왕복(웹 요청 → QGIS 반영 → 웹 확인) 을 명시적 상태머신으로 강제한다.
--   상태: pending → applied → closed  (+ rejected, reopen 으로 pending 복귀)
--   전이는 서버가 from-status 가드 + version(낙관적 잠금) 으로만 허용.
-- 가산적 마이그레이션 — 기존 데이터/스키마 손상 없음. 멱등.

BEGIN;

-- ---------------------------------------------------------------- boundary.remark
-- QGIS 비고를 엑셀 로컬에서 서버로 승격. 경계 제출 시 함께 업서트, 웹은 read.
ALTER TABLE boundary ADD COLUMN IF NOT EXISTS remark TEXT;

-- ---------------------------------------------------------------- boundary upsert 키 무결성
-- 서버 upsert 키 (adm_cd, ri_cd) 를 DB 가 직접 보장(앱 코드 사전검사의 안전망).
-- 사전 조건: 기존 데이터에 (adm_cd, ri_cd) 중복이 없어야 함. 중복 시 정리 후 적용:
--   SELECT adm_cd, COALESCE(ri_cd,''), count(*) FROM boundary
--    GROUP BY 1, 2 HAVING count(*) > 1;
CREATE UNIQUE INDEX IF NOT EXISTS boundary_adm_ri_uniq
    ON boundary (adm_cd, COALESCE(ri_cd, ''));

-- ---------------------------------------------------------------- review_markup lifecycle
ALTER TABLE review_markup
  ADD COLUMN IF NOT EXISTS version               INT NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS closed_by             CHAR(8),
  ADD COLUMN IF NOT EXISTS closed_at             TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS reopened_at           TIMESTAMPTZ;

-- (이전 초안의 resolved_boundary_gid 는 미사용으로 제외 — 적용된 환경에선 잔존해도 무해)
ALTER TABLE review_markup DROP COLUMN IF EXISTS resolved_boundary_gid;

-- status: 기존 pending/applied/rejected + closed(웹 확인 종료)
ALTER TABLE review_markup DROP CONSTRAINT IF EXISTS review_markup_status_check;
ALTER TABLE review_markup ADD CONSTRAINT review_markup_status_check
    CHECK (status IN ('pending', 'applied', 'rejected', 'closed'));

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

COMMIT;
