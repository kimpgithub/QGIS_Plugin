-- 완료여부 저장 키 일반화 — 부호(ri_cd) 없는 행정리도 체크 가능하게.
--  · 부호 있는 행: 키 = ri_cd (기존 그대로, 재업로드 후에도 유지)
--  · 부호 없는 행: 키 = 'gid:<gid>' (일련번호 기반 — 로그인 간 유지, 재업로드 시 초기화 허용)
-- ri_cd 컬럼에 'gid:12345' 형태 문자열도 담아야 하므로 길이만 확장(데이터 보존).
ALTER TABLE boundary_confirm ALTER COLUMN ri_cd TYPE VARCHAR(40);
