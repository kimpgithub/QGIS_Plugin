-- 담당자 업무연락처(내선번호) 컬럼. 첫 로그인 시 담당자 본인이 등록.
-- 내선번호는 개인정보 아님(휴대전화번호 등록 불가 — 입력은 숫자만).
ALTER TABLE auth ADD COLUMN IF NOT EXISTS contact VARCHAR(20);
