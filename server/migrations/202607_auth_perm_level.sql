-- 계정 권한 레벨: 마스터가 행정읍면(지역) 계정의 이용 권한을 제어.
--   1 = 정상(기본)      : 로그인·편집·조회 모두 가능
--   2 = 편집회수(열람전용): 로그인·조회 O, 라인등록/삭제표기/속성등록/완료체크/요청취소 차단
--   3 = 접근회수         : 로그인 불가
-- master 계정에는 적용되지 않음(항상 전권).
ALTER TABLE auth ADD COLUMN IF NOT EXISTS perm_level SMALLINT NOT NULL DEFAULT 1;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'auth_perm_level_check') THEN
    ALTER TABLE auth ADD CONSTRAINT auth_perm_level_check CHECK (perm_level BETWEEN 1 AND 3);
  END IF;
END $$;

COMMENT ON COLUMN auth.perm_level IS
  '권한: 1=정상, 2=편집회수(열람전용), 3=접근회수(로그인불가). master는 무시.';
