"""
검수 웹 초기 계정 시드.
실행: docker compose -f /srv/gis/compose/docker-compose.yml run --rm \
        -v /srv/gis/scripts:/scripts -v /srv/gis/compose:/out \
        backend python /scripts/seed_web_accounts.py

부산광역시(21) / 기장군(21510) / 5개 읍면 (boundary 보유 범위)
master 계정: admin_cd='00000000'
비밀번호는 16자 base32 랜덤. 평문은 /out/.web-credentials.txt 에 저장 (chmod 600).
기존 계정이 있으면 패스워드만 재발급(ON CONFLICT UPDATE).
"""
import os
import secrets
import string
from datetime import datetime, timezone

import psycopg
from passlib.context import CryptContext

pwd_ctx = CryptContext(schemes=["bcrypt"])

SIDO = ("21", "부산광역시")
SGG = ("21510", "기장군")
ADMINS = [
    ("21510110", "기장읍"),
    ("21510111", "일광읍"),
    ("21510120", "장안읍"),
    ("21510130", "정관읍"),
    ("21510330", "철마면"),
]
MASTER_CD = "00000000"


def gen_password(n: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def main():
    conninfo = (
        f"host={os.environ['DB_HOST']} port={os.environ['DB_PORT']} "
        f"user={os.environ['POSTGRES_USER']} password={os.environ['POSTGRES_PASSWORD']} "
        f"dbname={os.environ['POSTGRES_DB']}"
    )
    creds: list[tuple[str, str, str, str]] = []  # (admin_cd, adm_nm, role, password)

    with psycopg.connect(conninfo) as conn:
        with conn.cursor() as cur:
            # admin_node 시드 (5건, 멱등)
            for adm_cd, adm_nm in ADMINS:
                cur.execute(
                    """
                    INSERT INTO admin_node (adm_cd, adm_nm, sgg_cd, sgg_nm, sido_cd, sido_nm)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (adm_cd) DO UPDATE
                      SET adm_nm = EXCLUDED.adm_nm,
                          sgg_cd = EXCLUDED.sgg_cd, sgg_nm = EXCLUDED.sgg_nm,
                          sido_cd = EXCLUDED.sido_cd, sido_nm = EXCLUDED.sido_nm
                    """,
                    (adm_cd, adm_nm, SGG[0], SGG[1], SIDO[0], SIDO[1]),
                )

            # 계정 시드: master + 5 admin (비번 재발급)
            all_accounts = [(MASTER_CD, "MASTER", "master")] + [
                (cd, nm, "normal") for cd, nm in ADMINS
            ]
            for admin_cd, label, role in all_accounts:
                pw = gen_password(16)
                ph = pwd_ctx.hash(pw)
                cur.execute(
                    """
                    INSERT INTO auth (admin_cd, password_hash, role)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (admin_cd) DO UPDATE
                      SET password_hash = EXCLUDED.password_hash,
                          role = EXCLUDED.role
                    """,
                    (admin_cd, ph, role),
                )
                creds.append((admin_cd, label, role, pw))
        conn.commit()

    # 평문 자격증명 파일 출력
    out_path = "/out/.web-credentials.txt"
    with open(out_path, "w") as f:
        f.write(f"# 검수 웹 자격증명 — 생성 {datetime.now(timezone.utc).isoformat()}\n")
        f.write("# 경로: /srv/gis/compose/.web-credentials.txt (chmod 600)\n")
        f.write("# URL : https://gis-hq.tail3b9b19.ts.net/  (/web/login)\n\n")
        f.write(f"{'admin_cd':<10}{'label':<14}{'role':<8}password\n")
        f.write("-" * 56 + "\n")
        for admin_cd, label, role, pw in creds:
            f.write(f"{admin_cd:<10}{label:<14}{role:<8}{pw}\n")
    os.chmod(out_path, 0o600)
    print(f"WROTE {out_path}")
    print()
    for admin_cd, label, role, pw in creds:
        print(f"  {admin_cd}  {label:<10}  {role:<7}  {pw}")


if __name__ == "__main__":
    main()
