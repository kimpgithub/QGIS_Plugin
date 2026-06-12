#!/usr/bin/env python3
"""관리자(master) 계정 5개 시드 — admin001~admin005 / 비번 1234.

  admin001~003 : 국가데이터처
  admin004~005 : 작업관리자
모두 role=master (전국 검수·반영 권한). admin_cd 는 CHAR(8) — 'admin001' 8자 그대로.
역할 구분(국가데이터처/작업관리자)은 권한 차이가 없어 DB 에 저장하지 않는다(메모용).

ON CONFLICT DO UPDATE — 재실행 시 비번을 1234 로 재설정. 다른 계정은 안 건드림.

실행:
  cd /srv/gis/compose
  docker compose run --rm -v /srv/gis/scripts:/scripts backend \
      python /scripts/seed_admin_accounts.py
"""
import os

import psycopg
from passlib.context import CryptContext

pwd_ctx = CryptContext(schemes=["bcrypt"])
PASSWORD = os.environ.get("ADMIN_PASSWORD", "1234")
ACCOUNTS = [
    ("admin001", "국가데이터처"),
    ("admin002", "국가데이터처"),
    ("admin003", "국가데이터처"),
    ("admin004", "작업관리자"),
    ("admin005", "작업관리자"),
    ("admin006", "발주처"),
    ("admin007", "발주처"),
    ("admin008", "대전"),
    ("admin009", "대전"),
    ("admin010", "대전"),
]


def main():
    conninfo = (
        f"host={os.environ.get('DB_HOST', 'db')} port={os.environ.get('DB_PORT', '5432')} "
        f"user={os.environ['POSTGRES_USER']} password={os.environ['POSTGRES_PASSWORD']} "
        f"dbname={os.environ['POSTGRES_DB']}"
    )
    ph = pwd_ctx.hash(PASSWORD)
    with psycopg.connect(conninfo) as conn:
        with conn.cursor() as cur:
            for admin_cd, _label in ACCOUNTS:
                cur.execute(
                    """
                    INSERT INTO auth (admin_cd, password_hash, role)
                    VALUES (%s, %s, 'master')
                    ON CONFLICT (admin_cd) DO UPDATE
                      SET password_hash = EXCLUDED.password_hash,
                          role = EXCLUDED.role
                    """,
                    (admin_cd, ph),
                )
        conn.commit()
    for admin_cd, label in ACCOUNTS:
        print(f"  {admin_cd}  master  {label}  (pw={PASSWORD})")
    print(f"seeded {len(ACCOUNTS)} master 계정")


if __name__ == "__main__":
    main()
