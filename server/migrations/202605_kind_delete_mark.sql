-- 2026-05-19 — review_markup.kind 에 'delete_mark'(삭제표기, Point) 추가.
-- kostat_front 의 ToolBar 4종(add/delete/attr/delete_mark) 호환.

ALTER TABLE review_markup DROP CONSTRAINT IF EXISTS review_markup_kind_check;
ALTER TABLE review_markup ADD CONSTRAINT review_markup_kind_check
    CHECK (kind IN ('add', 'delete', 'attr', 'delete_mark'));
