-- 2026-05-19 — review_markup 에 처리자 admin_cd 기록.
-- '반영' 또는 '반려' 를 누른 마스터(관리자) 의 admin_cd 를 보존해
-- 수정 이력 카드에 "작업자 / 처리자" 양쪽 표시.

ALTER TABLE review_markup
  ADD COLUMN IF NOT EXISTS applied_by  CHAR(8),
  ADD COLUMN IF NOT EXISTS rejected_by CHAR(8);
