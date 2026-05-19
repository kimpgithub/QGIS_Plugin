#!/usr/bin/env python3
"""admin_node 전체 행정읍면(3561건) 에 대해 normal 계정 일괄 생성/확인.

- 기본 비번: env DEFAULT_USER_PASSWORD (없으면 '1234' — dev 전용)
- 단일 bcrypt 해시 재사용 (3561 × 100ms 해싱 회피)
- ON CONFLICT (admin_cd) DO NOTHING — 기존 마스터/유저 안 건드림

실행:
  cd /srv/gis/compose
  docker compose run --rm backend python /srv/gis/scripts/seed_all_users.py
"""
import os
from passlib.context import CryptContext
import psycopg

PASSWORD = os.environ.get("DEFAULT_USER_PASSWORD", "1234")

conn = psycopg.connect(
    host=os.environ.get("DB_HOST", "db"),
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    dbname=os.environ["POSTGRES_DB"],
)
with conn, conn.cursor() as cur:
    h = CryptContext(schemes=["bcrypt"]).hash(PASSWORD)
    cur.execute(
        """
        INSERT INTO auth (admin_cd, password_hash, role)
        SELECT adm_cd, %s, 'normal' FROM admin_node
        ON CONFLICT (admin_cd) DO NOTHING
        """,
        (h,),
    )
    print(f"inserted {cur.rowcount} normal accounts (password='{PASSWORD}')")
    cur.execute("SELECT role, COUNT(*) FROM auth GROUP BY role ORDER BY role")
    for r in cur.fetchall():
        print(" ", r)
