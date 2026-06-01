-- 2026-06-01 — 마크업 라이프사이클 최종 단순화: 처리는 전부 웹에서, QGIS 와 동기화 없음.
--
--   pending → applied (웹 작업자 반영, 끝)
--   pending → rejected (웹 작업자 반려·사유, 끝)
--
-- closed(확인종료) 단계 제거 — 반영/반려가 곧 종결. 기존 closed 행은 applied 로 변환.
-- 멱등 — 재적용 안전.

BEGIN;

-- 기존 closed → applied 변환 (반영 정보는 이미 applied_* 에 있음)
UPDATE review_markup SET status = 'applied' WHERE status = 'closed';

-- status 제약: 3상태
ALTER TABLE review_markup DROP CONSTRAINT IF EXISTS review_markup_status_check;
ALTER TABLE review_markup ADD CONSTRAINT review_markup_status_check
    CHECK (status IN ('pending', 'applied', 'rejected'));

-- closed/reopen 흔적 컬럼 제거
ALTER TABLE review_markup
  DROP COLUMN IF EXISTS closed_by,
  DROP COLUMN IF EXISTS closed_at,
  DROP COLUMN IF EXISTS reopened_at;

COMMIT;
