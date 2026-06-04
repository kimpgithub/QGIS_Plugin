-- 행정리경계 확인 완료여부 — 작업자가 임의로 작업한 경계의 검토 완료 체크.
-- (adm_cd, ri_cd) 키 — 플러그인 경계 재제출(DELETE+INSERT, gid 변경) 후에도 유지.
-- 행 존재 = 완료. 체크 해제 = 행 삭제.
CREATE TABLE IF NOT EXISTS boundary_confirm (
    adm_cd       CHAR(8)     NOT NULL,
    ri_cd        VARCHAR(10) NOT NULL,
    confirmed_by CHAR(8),
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (adm_cd, ri_cd)
);
