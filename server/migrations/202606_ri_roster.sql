-- 행정리 전체 명부(ri_roster) — (2025농총) 행정리현황 엑셀 38,274행.
-- 폴리곤 부여 현황 비교의 기준 마스터. boundary 와 (emd_cd, li_cd) = (adm_cd, ri_cd) 로 LEFT JOIN.
-- 명부는 발주처 배포본이므로 갱신 = 전체 교체(seed 재적재). 작업 진행상태는 boundary 가 진실 소스.
CREATE TABLE IF NOT EXISTS ri_roster (
    ctpv_cd   VARCHAR(2)   NOT NULL,            -- 시도코드
    ctpv_nm   VARCHAR(100),                     -- 시도명
    sgg_cd    VARCHAR(5)   NOT NULL,            -- 시군구코드
    sgg_nm    VARCHAR(100),                     -- 시군구명
    emd_cd    VARCHAR(8)   NOT NULL,            -- 읍면동코드 (= boundary.adm_cd)
    emd_nm    VARCHAR(100),                     -- 읍면동명
    ri_nm     VARCHAR(100),                     -- 법정리명
    li_nm     VARCHAR(100),                     -- 행정리명
    li_cd     VARCHAR(3)   NOT NULL,            -- 행정리부호 (= boundary.ri_cd)
    work_yn   CHAR(1),                          -- 엑셀 원본 작업여부(참고용; 진실은 boundary)
    remark    VARCHAR(200),                     -- 비고
    PRIMARY KEY (emd_cd, li_cd)
);

CREATE INDEX IF NOT EXISTS ri_roster_ctpv_idx ON ri_roster (ctpv_cd);
CREATE INDEX IF NOT EXISTS ri_roster_emd_idx  ON ri_roster (emd_cd);
