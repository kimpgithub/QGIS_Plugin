#!/usr/bin/env python3
"""읍면별 담당자 계정 시드 — server/scripts/web_accounts.csv 기준.

CSV 출처: 발주처 배포 `읍면별(ID,PW).xlsm` (1403개 읍면, ID=8자리 읍면코드).
  컬럼: adm_cd, adm_nm, sgg_cd, sgg_nm, sido_cd, sido_nm, pw(평문)

동작:
  - admin_node  : ON CONFLICT DO UPDATE (이름/상위코드 동기화)
  - auth        : ON CONFLICT DO UPDATE password_hash/role 만 (contact 는 보존 — 담당자
                  본인이 첫 로그인 시 등록한 내선번호를 시드 재실행이 지우지 않음)
  - master(00000000) : ON CONFLICT DO NOTHING (비번 보존). 없으면 env MASTER_PASSWORD
                  (기본 미생성 — 평문 노출 방지) 로만 생성.

실행:
  cd /srv/gis/compose
  docker compose run --rm -v /srv/gis/scripts:/scripts backend \
      python /scripts/seed_web_users.py
"""
import csv
import os

import psycopg
from passlib.context import CryptContext

pwd_ctx = CryptContext(schemes=["bcrypt"])
CSV_PATH = os.environ.get("WEB_ACCOUNTS_CSV", "/scripts/web_accounts.csv")
MASTER_CD = "00000000"


def main():
    conninfo = (
        f"host={os.environ.get('DB_HOST', 'db')} port={os.environ.get('DB_PORT', '5432')} "
        f"user={os.environ['POSTGRES_USER']} password={os.environ['POSTGRES_PASSWORD']} "
        f"dbname={os.environ['POSTGRES_DB']}"
    )
    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"빈 CSV: {CSV_PATH}")

    # 같은 평문이 많으므로 해시 캐시로 1403 × 100ms 재해싱 회피
    hash_cache: dict[str, str] = {}

    with psycopg.connect(conninfo) as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """
                    INSERT INTO admin_node (adm_cd, adm_nm, sgg_cd, sgg_nm, sido_cd, sido_nm)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (adm_cd) DO UPDATE
                      SET adm_nm = EXCLUDED.adm_nm,
                          sgg_cd = EXCLUDED.sgg_cd, sgg_nm = EXCLUDED.sgg_nm,
                          sido_cd = EXCLUDED.sido_cd, sido_nm = EXCLUDED.sido_nm
                    """,
                    (r["adm_cd"], r["adm_nm"], r["sgg_cd"], r["sgg_nm"],
                     r["sido_cd"], r["sido_nm"]),
                )
                pw = r["pw"]
                ph = hash_cache.get(pw) or hash_cache.setdefault(pw, pwd_ctx.hash(pw))
                cur.execute(
                    """
                    INSERT INTO auth (admin_cd, password_hash, role)
                    VALUES (%s, %s, 'normal')
                    ON CONFLICT (admin_cd) DO UPDATE
                      SET password_hash = EXCLUDED.password_hash,
                          role = EXCLUDED.role
                    """,
                    (r["adm_cd"], ph),
                )

            # master — 있으면 보존, 없고 env 지정 시에만 생성
            mpw = os.environ.get("MASTER_PASSWORD")
            if mpw:
                cur.execute(
                    """
                    INSERT INTO auth (admin_cd, password_hash, role)
                    VALUES (%s, %s, 'master')
                    ON CONFLICT (admin_cd) DO NOTHING
                    """,
                    (MASTER_CD, pwd_ctx.hash(mpw)),
                )
        conn.commit()

    print(f"seeded {len(rows)} 담당자 계정 (admin_node + auth, password 재발급, contact 보존)")
    print("master(00000000) 는 보존" + ("" if not os.environ.get("MASTER_PASSWORD") else " 또는 신규 생성"))


if __name__ == "__main__":
    main()
