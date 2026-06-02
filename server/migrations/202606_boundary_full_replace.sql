-- 2026-06-02 — 경계 저장을 읍면 단위 전체 교체로 변경 + 빈 부호(ri_cd) 허용.
--
-- 배경: 기존 upsert 키 (adm_cd, ri_cd) 때문에 부호가 빈 폴리곤이 여러 개면
-- 충돌 → 플러그인이 가짜 일련번호('001'...)를 자동 부여해 제출했고, 웹에서
-- 빈 부호가 채워진 것처럼 보이는 문제 발생.
--
-- 변경: PUT /api/boundary = 읍면 단위 DELETE+INSERT (전체 교체). 키 매칭이
-- 없으므로 빈 부호 폴리곤도 그대로 저장. 유니크 인덱스는 "실제 부호" 에만
-- 적용하는 부분 인덱스로 완화 (빈 부호는 여러 개 허용, 실제 부호 중복은 차단).
--
-- 멱등 — 재적용 안전.

BEGIN;

DROP INDEX IF EXISTS boundary_adm_ri_uniq;

CREATE UNIQUE INDEX IF NOT EXISTS boundary_adm_ri_uniq
    ON boundary (adm_cd, ri_cd)
    WHERE ri_cd IS NOT NULL AND btrim(ri_cd) <> '';

COMMIT;
