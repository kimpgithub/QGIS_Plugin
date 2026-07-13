"""
GIS 검수 API — FastAPI.

인증: 단일 Bearer 헤더.
  - 값이 PLUGIN_TOKEN 과 일치 → plugin (대전 QGIS): 전국 접근, write 권한
  - 그 외: JWT 디코드 시도 (HS256). payload.sub=admin_cd, payload.role=normal|master
    normal 은 본인 adm_cd 만, master 는 전국 접근

좌표계: DB=EPSG:5179, API=EPSG:4326. 빈 DB 에서도 에러 없이 동작.
프론트엔드 contract: web/src/api/*.ts, web/src/types/index.ts.
"""
import hmac as _hmac
import io
import json
import os
import time
import zipfile
from collections import Counter
from contextlib import asynccontextmanager
from urllib.parse import quote

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

# ---------------------------------------------------------------- config
DB_HOST = os.environ.get("DB_HOST", "db")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_USER = os.environ["POSTGRES_USER"]
DB_PASS = os.environ["POSTGRES_PASSWORD"]
DB_NAME = os.environ["POSTGRES_DB"]
PLUGIN_TOKEN = os.environ["PLUGIN_TOKEN"]
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_EXPIRES_MIN = int(os.environ.get("JWT_EXPIRES_MIN", "480"))
JWT_ALGO = "HS256"
S3_BUCKET = os.environ.get("S3_BUCKET", "gis-scan")
TITILER_PREFIX = os.environ.get("TITILER_PREFIX", "/tiles")

CONNINFO = f"host={DB_HOST} port={DB_PORT} user={DB_USER} password={DB_PASS} dbname={DB_NAME}"
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)
pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = ConnectionPool(CONNINFO, min_size=1, max_size=8, kwargs={"row_factory": dict_row})
    pool.wait(timeout=30)
    yield
    pool.close()


app = FastAPI(title="GIS 검수 API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- db
def fetchone(sql: str, params=()):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetchall(sql: str, params=()):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def execute(sql: str, params=()):
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone() if cur.description else None
        conn.commit()
        return row


# ---------------------------------------------------------------- auth
def get_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    """Bearer 토큰 검사. plugin/normal/master 중 하나로 식별."""
    if not creds or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer 토큰이 필요합니다")
    tok = creds.credentials
    if _hmac.compare_digest(tok, PLUGIN_TOKEN):
        return {"role": "plugin", "admin_cd": None}
    try:
        payload = jwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="토큰이 만료되었습니다") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않습니다") from None
    role = payload.get("role")
    if role not in ("normal", "master"):
        raise HTTPException(status_code=401, detail="토큰 페이로드가 유효하지 않습니다")
    admin_cd = payload.get("sub")
    user = {"role": role, "admin_cd": admin_cd}
    if role == "normal":
        # 권한 변경 시 기존 세션 무효화 — 변경시각(perm_updated_at) 이후 발급 토큰만 유효.
        # 마스터가 권한을 바꾸면 대상 계정의 다음 요청에서 401 → 프론트 자동 로그아웃.
        row = fetchone(
            "SELECT perm_level, extract(epoch FROM perm_updated_at) AS upd "
            "FROM auth WHERE admin_cd = %s",
            (admin_cd,),
        )
        if row:
            upd = row.get("upd")
            iat = payload.get("iat") or 0
            # iat 는 초 단위로 내림되므로 upd 도 초 단위(int)로 맞춰 비교 —
            # 변경과 같은 초에 재로그인해도 방금 받은 토큰이 무효가 되지 않도록.
            if upd is not None and iat < int(upd):
                raise HTTPException(
                    status_code=401,
                    detail="권한이 변경되어 다시 로그인해야 합니다.",
                )
            user["perm_level"] = row.get("perm_level") or 1
    return user


def require_plugin(user: dict = Depends(get_user)) -> dict:
    if user["role"] != "plugin":
        raise HTTPException(status_code=403, detail="플러그인 권한이 필요합니다")
    return user


def check_admin_access(user: dict, adm_cd: str):
    """plugin/master 는 전국 허용, normal 은 본인 adm_cd 만."""
    if user["role"] in ("plugin", "master"):
        return
    if user["admin_cd"] != adm_cd:
        raise HTTPException(status_code=403, detail="본인 adm_cd 외 접근 불가")


def assert_can_edit(user: dict):
    """편집(수정요청 생성/완료체크/요청취소) 권한 확인 — normal 계정의 perm_level(get_user
    에서 부착)이 2 이상(편집회수)이면 차단. master/plugin 은 항상 허용."""
    if user["role"] != "normal":
        return
    if (user.get("perm_level") or 1) >= 2:
        raise HTTPException(
            status_code=403, detail="편집 권한이 회수된 계정입니다 (열람 전용)."
        )


# 슈퍼관리자(발주처 총괄) 전용 — master 중에서도 00000000 한 계정만.
# 관리 현황 페이지(지역별 수정요청 현황·데이터 업로드 이력)는 이 계정만 열람.
SUPERADMIN_CD = "00000000"


def require_superadmin(user: dict = Depends(get_user)) -> dict:
    if user["role"] != "master" or (user.get("admin_cd") or "").strip() != SUPERADMIN_CD:
        raise HTTPException(status_code=403, detail="관리 현황은 00000000 계정만 열람 가능")
    return user


def require_master(user: dict = Depends(get_user)) -> dict:
    """master 계정 전용(발주처/작업자 총괄). 전국 공간정보 내보내기 등에 사용."""
    if user["role"] != "master":
        raise HTTPException(status_code=403, detail="관리자(master) 전용 기능입니다")
    return user


# ---------------------------------------------------------------- models
class LoginBody(BaseModel):
    id: str
    password: str


class MarkupCreate(BaseModel):
    adm_cd: str
    kind: str
    geometry: dict
    attrs: dict | None = None


class RejectBody(BaseModel):
    reason: str
    version: int | None = None        # 낙관적 잠금 — 불일치 시 409


class ApplyBody(BaseModel):
    version: int | None = None


class CogRegister(BaseModel):
    adm_cd: str
    s3_key: str
    bounds: dict | list           # GeoJSON Polygon 또는 [minx,miny,maxx,maxy]
    width: int | None = None
    height: int | None = None
    srid: int = 5179


# ---------------------------------------------------------------- health
@app.get("/api/health")
def health():
    try:
        fetchone("SELECT 1 AS ok")
        return {"status": "ok", "db": "ok"}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "degraded", "db": str(e)})


# ---------------------------------------------------------------- login
@app.post("/api/login")
def login(body: LoginBody, request: Request):
    row = fetchone(
        "SELECT admin_cd, password_hash, role, perm_level FROM auth WHERE admin_cd = %s",
        (body.id,),
    )
    ok = False
    if row:
        try:
            ok = pwd_ctx.verify(body.password, row["password_hash"])
        except Exception:  # noqa: BLE001
            ok = False
    else:
        pwd_ctx.dummy_verify()
    fwd = request.headers.get("x-forwarded-for")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None)
    ua = request.headers.get("user-agent")
    try:
        execute(
            "INSERT INTO login_log (admin_cd, ip, user_agent) VALUES (%s, %s, %s)",
            (body.id[:8], ip, ua),
        )
    except Exception:  # noqa: BLE001
        pass
    if not ok:
        raise HTTPException(status_code=401, detail="ID 또는 비밀번호가 올바르지 않습니다")
    admin_cd = row["admin_cd"].strip()
    db_role = row["role"]                                    # normal | master
    perm_level = row.get("perm_level") or 1
    # 접근권한 회수(레벨3) — master 는 예외. 로그인 차단.
    if db_role != "master" and perm_level >= 3:
        raise HTTPException(
            status_code=403, detail="관리자에 의해 접근 권한이 회수된 계정입니다."
        )
    fe_role = "master" if db_role == "master" else "user"    # 프론트 UserRole
    now = int(time.time())
    token = jwt.encode(
        {"sub": admin_cd, "role": db_role, "iat": now, "exp": now + JWT_EXPIRES_MIN * 60},
        JWT_SECRET, algorithm=JWT_ALGO,
    )
    user_obj: dict = {"id": admin_cd, "role": fe_role}
    if db_role != "master":
        node = fetchone("SELECT adm_nm FROM admin_node WHERE adm_cd = %s", (admin_cd,))
        user_obj["adm_cd"] = admin_cd
        user_obj["adm_nm"] = node["adm_nm"] if node else None
        user_obj["perm_level"] = perm_level    # 프론트 편집 UI 게이팅용(레벨2=열람전용)
    return {"token": token, "user": user_obj}


# ---------------------------------------------------------------- token refresh (세션 연장)
@app.post("/api/auth/refresh")
def refresh_token(user: dict = Depends(get_user)):
    """활성 사용자 세션 연장 — 유효한 토큰을 받아 동일 사용자로 만료시각만 갱신한
    새 토큰을 발급. (미사용 타임아웃 경고의 [연장] 및 작업 중 자동 갱신에 사용)
    plugin 토큰은 갱신 대상이 아니다."""
    if user["role"] not in ("normal", "master"):
        raise HTTPException(status_code=403, detail="세션 연장 대상이 아닙니다")
    admin_cd = (user.get("admin_cd") or "").strip()
    now = int(time.time())
    token = jwt.encode(
        {"sub": admin_cd, "role": user["role"], "iat": now, "exp": now + JWT_EXPIRES_MIN * 60},
        JWT_SECRET, algorithm=JWT_ALGO,
    )
    return {"token": token}


# ---------------------------------------------------------------- admins
@app.get("/api/admins")
def list_admins(_: dict = Depends(get_user)):
    """검수 대상 admin 목록 (AdminUnit[]).

    노출 조건 = COG(스캔 이미지) **또는** 경계(boundary) 가 있는 읍면.
    이미지가 아직 없어도 경계 SHP 만 올라온 읍면(예: 정선군)은 경계 검수가
    가능하므로 목록에 나와야 한다 — 과거 cog_catalog INNER JOIN 이라 이런
    읍면이 통째로 가려졌다. (지도는 경계 기준으로 맞춰지고, 이미지 없으면
    COG 레이어만 꺼진 채 정상 동작.)
    플러그인의 /api/admins 연결 테스트는 list length 만 보므로 호환."""
    rows = fetchall(
        """
        SELECT n.adm_cd, n.adm_nm,
               n.sgg_cd AS sigungu_cd, n.sgg_nm AS sigungu_nm,
               n.sido_cd, n.sido_nm,
               COALESCE(a.perm_level, 1) AS perm_level,
               EXISTS (SELECT 1 FROM cog_catalog c WHERE c.adm_cd = n.adm_cd)
                 AS has_cog
        FROM admin_node n
        LEFT JOIN auth a ON a.admin_cd = n.adm_cd
        WHERE EXISTS (SELECT 1 FROM cog_catalog c WHERE c.adm_cd = n.adm_cd)
           OR EXISTS (SELECT 1 FROM boundary  b WHERE b.adm_cd = n.adm_cd)
        ORDER BY n.sido_cd, n.sgg_cd, n.adm_cd
        """
    )
    return [{**r, "adm_cd": r["adm_cd"].strip()} for r in rows]


class PermBody(BaseModel):
    level: int   # 1=정상, 2=편집회수(열람전용), 3=접근회수(로그인불가)


@app.patch("/api/admin/account/{adm_cd}/perm")
def set_account_perm(adm_cd: str, body: PermBody, _: dict = Depends(require_master)):
    """행정읍면 계정 권한 레벨 변경 — master 전용.
    1=정상, 2=편집회수(열람전용), 3=접근회수(로그인불가). master 계정은 변경 불가."""
    if body.level not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="level 은 1/2/3")
    target = fetchone(
        "SELECT role, perm_level FROM auth WHERE admin_cd = %s", (adm_cd,)
    )
    if not target:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다")
    if target["role"] == "master":
        raise HTTPException(status_code=400, detail="master 계정은 권한을 변경할 수 없습니다")
    # 실제 변경이 있을 때만 perm_updated_at 갱신 → 그 계정의 기존 토큰(로그인 세션)을
    # 무효화해 재로그인 강제(get_user 에서 iat < perm_updated_at 이면 401).
    if (target.get("perm_level") or 1) != body.level:
        execute(
            "UPDATE auth SET perm_level = %s, perm_updated_at = now() WHERE admin_cd = %s",
            (body.level, adm_cd),
        )
    return {"adm_cd": adm_cd.strip(), "perm_level": body.level}


# ---------------------------------------------------------------- admin outline (행정읍면 외곽)
@app.get("/api/admin_outline")
def admin_outline(
    adm_cd: str | None = None,
    buffer_m: float = 1000.0,
    bbox: str | None = None,
    _: dict = Depends(get_user),
):
    """admin_outline (bnd_adm_pg.shp 적재본) 원본 geom GeoJSON FC.

    - adm_cd 지정: 해당 읍면 + buffer_m(기본 1km, EPSG:5179) 거리 내 이웃 폴리곤 반환.
    - bbox 지정 (minx,miny,maxx,maxy EPSG:4326): bbox 와 교차하는 폴리곤 반환.
    - 둘 다 미지정: 400 (전국 일괄 로딩 차단).
    """
    if adm_cd:
        if len(adm_cd) != 8:
            raise HTTPException(status_code=400, detail="adm_cd는 8자")
        if buffer_m < 0 or buffer_m > 50000:
            raise HTTPException(status_code=400, detail="buffer_m 은 0~50000m")
        where = """
            WHERE geom && (
                SELECT ST_Expand(geom, %s) FROM admin_outline WHERE adm_cd = %s
            )
        """
        params: tuple = (buffer_m, adm_cd)
    elif bbox:
        try:
            x1, y1, x2, y2 = [float(v) for v in bbox.split(",")]
        except ValueError as e:
            raise HTTPException(status_code=400,
                detail="bbox 형식: minx,miny,maxx,maxy (EPSG:4326)") from e
        where = ("WHERE geom && "
                 "ST_Transform(ST_MakeEnvelope(%s,%s,%s,%s,4326), 5179)")
        params = (x1, y1, x2, y2)
    else:
        raise HTTPException(status_code=400,
            detail="adm_cd 또는 bbox 중 하나가 필요합니다")
    row = fetchone(
        f"""
        SELECT json_build_object(
          'type', 'FeatureCollection',
          'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature', 'id', adm_cd,
            'geometry', ST_AsGeoJSON(ST_Transform(geom, 4326))::json,
            'properties', json_build_object(
              'adm_cd', adm_cd, 'adm_nm', adm_nm,
              'sgg_cd', sgg_cd, 'sgg_nm', sgg_nm,
              'sido_cd', sido_cd, 'sido_nm', sido_nm,
              'is_target', adm_cd = %s)
          )) FILTER (WHERE adm_cd IS NOT NULL), '[]'::json)
        ) AS fc
        FROM admin_outline {where}
        """,
        (adm_cd if adm_cd else None, *params),
    )
    return row["fc"]


# ---------------------------------------------------------------- boundary
@app.get("/api/boundary")
def get_boundary(adm_cd: str, user: dict = Depends(get_user)):
    """해당 admin 행정리 경계 → GeoJSON FC (EPSG:4326). 없으면 빈 FC."""
    check_admin_access(user, adm_cd)
    # confirmed = boundary_confirm 에 (adm_cd, ri_cd) 행이 있으면 true(완료).
    row = fetchone(
        """
        SELECT json_build_object(
          'type', 'FeatureCollection',
          'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature', 'id', b.gid,
            'geometry', ST_AsGeoJSON(ST_Transform(b.geom, 4326))::json,
            'properties', json_build_object(
              'gid', b.gid, 'adm_cd', b.adm_cd, 'adm_nm', b.adm_nm,
              'ri_cd', b.ri_cd, 'ri_nm', b.ri_nm, 'status', b.status,
              'remark', b.remark, 'confirmed', (c.ri_cd IS NOT NULL),
              'updated_at', b.updated_at, 'updated_by', b.updated_by)
          )) FILTER (WHERE b.gid IS NOT NULL), '[]'::json)
        ) AS fc
        FROM boundary b
        LEFT JOIN boundary_confirm c
          ON c.adm_cd = b.adm_cd
         AND c.ri_cd = COALESCE(NULLIF(btrim(b.ri_cd), ''), 'gid:' || b.gid)
        WHERE b.adm_cd = %s
        """,
        (adm_cd,),
    )
    return row["fc"]


class ConfirmBody(BaseModel):
    adm_cd: str
    ri_cd: str = ""
    gid: int | None = None
    confirmed: bool


@app.put("/api/boundary/confirm")
def set_boundary_confirm(body: ConfirmBody, user: dict = Depends(get_user)):
    """행정리경계 확인 완료여부 토글. 행 존재=완료.

    저장 키(ri_cd 컬럼): 부호가 있으면 부호, 없으면 'gid:<gid>'.
      · 부호 키: 재업로드(경계 재제출) 후에도 유지.
      · gid 키: 로그인 간 유지되나 해당 읍면 재업로드 시 초기화(허용).
    부호도 gid 도 없으면 키를 못 만들어 거부."""
    check_admin_access(user, body.adm_cd)
    assert_can_edit(user)
    ri_cd = (body.ri_cd or "").strip()
    key = ri_cd if ri_cd else (f"gid:{body.gid}" if body.gid is not None else "")
    if not key:
        raise HTTPException(status_code=400, detail="부호 또는 gid 가 있어야 완료 체크 가능")
    if body.confirmed:
        execute(
            """
            INSERT INTO boundary_confirm (adm_cd, ri_cd, confirmed_by, confirmed_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (adm_cd, ri_cd) DO UPDATE
              SET confirmed_by = EXCLUDED.confirmed_by, confirmed_at = now()
            """,
            (body.adm_cd, key, user["admin_cd"]),
        )
    else:
        execute(
            "DELETE FROM boundary_confirm WHERE adm_cd = %s AND ri_cd = %s",
            (body.adm_cd, key),
        )
    return {"adm_cd": body.adm_cd, "key": key, "confirmed": body.confirmed}


@app.put("/api/boundary")
def replace_boundary(body: dict, srid: int = 4326, user: dict = Depends(require_plugin)):
    """플러그인 전용 — 제출된 읍면(adm_cd)의 경계를 **전체 교체**(DELETE 후 INSERT).

    QGIS 제출 = 해당 읍면의 최신 스냅샷. 키 매칭(upsert)이 없으므로 ri_cd(부호)가
    빈 폴리곤도 여러 개 그대로 저장된다 — 빈 부호는 웹에서 발주자가 속성등록
    요청으로 채운다(가짜 일련번호 자동부여 제거).

    같은 읍면 안에서 *같은 실제 부호*를 가진 폴리곤 여러 개(비연속 행정리 — 섬,
    분리 구역)는 ST_Union 으로 병합해 1개 MultiPolygon 행으로 저장한다.

    주의: payload 에 포함된 adm_cd 의 기존 경계는 전부 지워진다. 일부 폴리곤만
    보내면 나머지는 삭제됨 — 플러그인은 항상 읍면 전체를 제출한다.
    수정요청(마크업) 처리는 웹에서 별도 진행(QGIS 와 동기화하지 않음)."""
    if srid not in (4326, 5179):
        raise HTTPException(status_code=400, detail="srid는 4326 또는 5179만 허용")
    if body.get("type") != "FeatureCollection" or not isinstance(body.get("features"), list):
        raise HTTPException(status_code=400, detail="GeoJSON FeatureCollection이 필요합니다")

    def _norm_ri(props: dict):
        """빈 문자열/공백 ri_cd 를 None 으로 정규화."""
        v = props.get("ri_cd")
        return (str(v).strip() or None) if v is not None else None

    # 부호 중복 집계 — 같은 (adm_cd, ri_cd) 폴리곤 여러 개는 병합 대상으로 보고.
    key_counts: Counter = Counter()
    rows = []
    for feat in body["features"]:
        geom = feat.get("geometry")   # None=경계 미매핑 행정리(명부만 제출)
        props = feat.get("properties") or {}
        adm_cd = props.get("adm_cd")
        if not adm_cd:
            raise HTTPException(status_code=400, detail="각 feature는 properties.adm_cd 필요")
        ri_cd = _norm_ri(props)
        if ri_cd is not None:
            key_counts[(adm_cd, ri_cd)] += 1
        rows.append({
            "geometry": geom,
            "adm_cd": adm_cd,
            "adm_nm": props.get("adm_nm"),
            "ri_cd": ri_cd,
            "ri_nm": props.get("ri_nm"),
            "status": props.get("status"),
            "remark": props.get("remark"),
            "updated_by": props.get("updated_by") or "daejeon",
        })
    merged = sorted(f"{a}/{r}" for (a, r), c in key_counts.items() if c > 1)

    admins = sorted({r["adm_cd"] for r in rows})
    deleted = inserted = carried = 0
    with pool.connection() as conn:
        with conn.cursor() as cur:
            if rows:
                # 0) 완료체크 승계 준비 — 부호 없는(gid 키) 완료체크만 대상.
                #    부호 있는 행은 키가 부호라 재업로드에도 자동 유지되므로 제외.
                #    삭제 전에 옛 도형을 스냅샷 → 삽입 후 공간 매칭으로 새 gid 로 이전.
                cur.execute(
                    """
                    CREATE TEMP TABLE _confirm_carry ON COMMIT DROP AS
                    SELECT b.adm_cd, b.gid AS old_gid,
                           ST_PointOnSurface(ST_MakeValid(b.geom)) AS pt,
                           c.confirmed_by, c.confirmed_at
                    FROM boundary b
                    JOIN boundary_confirm c
                      ON c.adm_cd = b.adm_cd AND c.ri_cd = 'gid:' || b.gid
                    WHERE b.adm_cd = ANY(%s)
                      AND (b.ri_cd IS NULL OR btrim(b.ri_cd) = '')
                      AND b.geom IS NOT NULL
                    """,
                    (admins,),
                )
                # 1) 제출된 읍면의 기존 경계 전부 삭제
                cur.execute(
                    "DELETE FROM boundary WHERE adm_cd = ANY(%s)", (admins,))
                deleted = cur.rowcount
                # 2) 새 경계 일괄 삽입.
                #    - 같은 실제 부호(ri_cd) 폴리곤들 → ST_Union 으로 1행 병합
                #    - 빈 부호(NULL) 폴리곤들 → 병합하지 않고 각각 1행 (ordinality 로 그룹 분리)
                cur.execute(
                    """
                    INSERT INTO boundary
                      (geom, adm_cd, adm_nm, ri_cd, ri_nm, status, remark, updated_by)
                    SELECT ST_Multi(ST_Union(s.geom)),
                           s.adm_cd, max(s.adm_nm), s.ri_cd, max(s.ri_nm),
                           COALESCE(max(s.status), 'draft'),
                           string_agg(DISTINCT s.remark, ' / '),
                           max(s.updated_by)
                    FROM (
                      SELECT CASE
                               WHEN f->'geometry' IS NULL
                                 OR jsonb_typeof(f->'geometry') = 'null'
                               THEN NULL
                               ELSE ST_Transform(ST_SetSRID(
                                 ST_GeomFromGeoJSON((f->'geometry')::text), %s), 5179)
                             END AS geom,
                             f->>'adm_cd' AS adm_cd, f->>'adm_nm' AS adm_nm,
                             f->>'ri_cd'  AS ri_cd,  f->>'ri_nm'  AS ri_nm,
                             f->>'status' AS status, f->>'remark' AS remark,
                             f->>'updated_by' AS updated_by,
                             ord
                      FROM jsonb_array_elements(%s::jsonb) WITH ORDINALITY AS t(f, ord)
                    ) s
                    GROUP BY s.adm_cd, s.ri_cd,
                             CASE WHEN s.ri_cd IS NULL THEN s.ord ELSE 0 END
                    """,
                    (srid, json.dumps(rows)),
                )
                inserted = cur.rowcount
                # 3) 완료체크 공간 매칭 승계 — 옛 폴리곤의 내부점(대표점)을 포함하는
                #    새 폴리곤이 '같은 위치'. 그 새 gid 로 완료체크를 이전한다.
                #    ST_MakeValid 로 위상오류(self-intersection) 방어. 점-폴리곤
                #    포함이라 폴리곤 교차보다 견고하고 자연히 1:1. 여럿에 포함되면
                #    가장 작은(가장 안쪽) 폴리곤을 채택.
                cur.execute(
                    """
                    INSERT INTO boundary_confirm
                      (adm_cd, ri_cd, confirmed_by, confirmed_at)
                    SELECT o.adm_cd, 'gid:' || nm.new_gid,
                           o.confirmed_by, o.confirmed_at
                    FROM _confirm_carry o
                    CROSS JOIN LATERAL (
                      SELECT n.gid AS new_gid
                      FROM boundary n
                      WHERE n.adm_cd = o.adm_cd
                        AND (n.ri_cd IS NULL OR btrim(n.ri_cd) = '')
                        AND n.geom IS NOT NULL
                        AND ST_Intersects(ST_MakeValid(n.geom), o.pt)
                      ORDER BY ST_Area(ST_MakeValid(n.geom)) ASC
                      LIMIT 1
                    ) nm
                    ON CONFLICT (adm_cd, ri_cd) DO NOTHING
                    """,
                )
                carried = cur.rowcount
                # 4) 승계 못한 옛 gid 체크 정리 — 대응 경계가 사라진 gid 키 제거.
                cur.execute(
                    """
                    DELETE FROM boundary_confirm c
                    WHERE c.adm_cd = ANY(%s)
                      AND c.ri_cd LIKE 'gid:%%'
                      AND NOT EXISTS (
                        SELECT 1 FROM boundary b
                        WHERE b.adm_cd = c.adm_cd
                          AND 'gid:' || b.gid = c.ri_cd
                      )
                    """,
                    (admins,),
                )
        conn.commit()
    return {"affected": inserted, "inserted": inserted, "deleted": deleted,
            "confirmed_carried": carried,
            "admins": admins, "features": len(body["features"]),
            "merged": merged}


# ---------------------------------------------------------------- cog
@app.get("/api/cog/{adm_cd}")
def get_cog(adm_cd: str, user: dict = Depends(get_user)):
    """cog_catalog 의 s3_key + titiler 타일 URL 템플릿."""
    if len(adm_cd) != 8:
        raise HTTPException(status_code=400, detail="adm_cd는 8자")
    check_admin_access(user, adm_cd)
    row = fetchone(
        """
        SELECT adm_cd, s3_key, width, height, published_at,
               ST_AsGeoJSON(ST_Transform(bounds, 4326))::json AS bounds_geojson,
               Box2D(ST_Transform(bounds, 4326))::text AS bbox_text
        FROM cog_catalog WHERE adm_cd = %s
        """,
        (adm_cd,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="해당 admin의 COG가 아직 없습니다")
    s3_url = f"s3://{S3_BUCKET}/{row['s3_key']}"
    enc = quote(s3_url, safe="")
    bbox = None
    bt = row.get("bbox_text")
    if bt and bt.startswith("BOX("):
        bbox = [float(x) for x in bt[4:-1].replace(",", " ").split()]
    return {
        "adm_cd": row["adm_cd"].strip(), "s3_key": row["s3_key"], "s3_url": s3_url,
        "width": row["width"], "height": row["height"],
        "published_at": row["published_at"], "bounds_geojson": row["bounds_geojson"],
        "bbox": bbox,
        "tile_url": f"{TITILER_PREFIX}/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png?url={enc}",
        "tilejson_url": f"{TITILER_PREFIX}/cog/WebMercatorQuad/tilejson.json?url={enc}",
    }


@app.post("/api/cog")
def register_cog(body: CogRegister, _: dict = Depends(require_plugin)):
    """플러그인 전용 — COG S3 업로드 후 cog_catalog 등록 (adm_cd PK upsert)."""
    if body.srid not in (4326, 5179):
        raise HTTPException(status_code=400, detail="srid는 4326 또는 5179만 허용")
    if isinstance(body.bounds, list):
        if len(body.bounds) != 4:
            raise HTTPException(status_code=400, detail="bounds 배열은 [minx,miny,maxx,maxy]")
        bounds_sql = "ST_MakeEnvelope(%s,%s,%s,%s,%s)"
        bparams = (*[float(x) for x in body.bounds], body.srid)
    else:
        bounds_sql = "ST_SetSRID(ST_GeomFromGeoJSON(%s), %s)"
        bparams = (json.dumps(body.bounds), body.srid)
    row = execute(
        f"""
        INSERT INTO cog_catalog (adm_cd, s3_key, bounds, width, height)
        VALUES (%s, %s, ST_Transform({bounds_sql}, 5179), %s, %s)
        ON CONFLICT (adm_cd) DO UPDATE
          SET s3_key = EXCLUDED.s3_key, bounds = EXCLUDED.bounds,
              width = EXCLUDED.width, height = EXCLUDED.height,
              published_at = now()
        RETURNING adm_cd, s3_key, width, height, published_at
        """,
        (body.adm_cd, body.s3_key, *bparams, body.width, body.height),
    )
    return {"ok": True, **row}


# ---------------------------------------------------------------- markup
_KIND_GEOM = {
    "add":         ("LineString",),
    "delete":      ("LineString",),
    "attr":        ("Point",),
    # 삭제표기: 경계선에 스냅한 구간(LineString) 으로 전환. Point 는 구버전 호환.
    "delete_mark": ("LineString", "Point"),
}


@app.get("/api/markup")
def list_markup(
    adm_cd: str | None = None,
    status: str | None = None,
    user: dict = Depends(get_user),
):
    """수정요청 GeoJSON FC. adm_cd 지정 시 role 검증. 최신 등록(id 큰 것)이 먼저."""
    if adm_cd:
        check_admin_access(user, adm_cd)
    conds: list[str] = []
    params: list = []
    if adm_cd:
        conds.append("adm_cd = %s")
        params.append(adm_cd)
    if status:
        if status not in ("pending", "applied", "rejected"):
            raise HTTPException(status_code=400, detail="status는 pending/applied/rejected 중 하나")
        conds.append("status = %s")
        params.append(status)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    row = fetchone(
        f"""
        SELECT json_build_object(
          'type', 'FeatureCollection',
          'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature', 'id', id,
            'geometry', ST_AsGeoJSON(ST_Transform(geom, 4326))::json,
            'properties', json_build_object(
              'id', id, 'kind', kind, 'attrs', attrs,
              'adm_cd', adm_cd, 'status', status, 'version', version,
              'reject_reason', reject_reason,
              'created_by', created_by, 'created_at', created_at,
              'applied_by', applied_by, 'applied_at', applied_at,
              'rejected_by', rejected_by, 'rejected_at', rejected_at)
          ) ORDER BY id DESC) FILTER (WHERE id IS NOT NULL), '[]'::json)
        ) AS fc
        FROM review_markup {where}
        """,
        tuple(params),
    )
    return row["fc"]


@app.post("/api/markup", status_code=201)
def create_markup(body: MarkupCreate, user: dict = Depends(get_user)):
    if len(body.adm_cd) != 8:
        raise HTTPException(status_code=400, detail="adm_cd는 8자")
    check_admin_access(user, body.adm_cd)
    assert_can_edit(user)
    if body.kind not in _KIND_GEOM:
        raise HTTPException(status_code=400, detail="kind는 add/delete/attr/delete_mark 중 하나")
    gtype = body.geometry.get("type")
    if gtype not in _KIND_GEOM[body.kind]:
        raise HTTPException(
            status_code=400,
            detail=f"kind={body.kind} 는 geometry type {_KIND_GEOM[body.kind]} 만 허용",
        )
    creator = user["admin_cd"] or "plugin"
    row = execute(
        """
        INSERT INTO review_markup (adm_cd, kind, geom, attrs, created_by)
        VALUES (%s, %s,
                ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 5179),
                %s::jsonb, %s)
        RETURNING id
        """,
        (body.adm_cd, body.kind, json.dumps(body.geometry),
         json.dumps(body.attrs) if body.attrs is not None else None,
         creator),
    )
    return {"id": row["id"]}


# 상태머신 — 전이는 전부 _transition 경유(직접 status UPDATE 금지).
# 마크업 처리는 전부 웹에서 — QGIS 와 동기화하지 않는다.
#   pending → applied(웹 작업자 반영, 끝)
#   pending → rejected(웹 작업자 반려·사유, 끝)
# 반려됐거나 결과가 다르면 → 새 요청을 등록한다.
_ALLOWED_FROM = {
    "applied":  {"pending"},
    "rejected": {"pending"},
}


def _transition(cur, markup_id: int, to_status: str, user: dict, *,
                reason: str | None = None,
                expected_version: int | None = None) -> str:
    """review_markup 상태 전이 — from-status 가드 + 낙관적 잠금 + 이력 기록.

    주어진 커서(cur) 안에서만 동작하고 커밋은 호출자 책임 → 경계 제출과 한
    트랜잭션에 묶을 수 있다. 반환 'changed' | 'noop'(이미 목표 상태, 멱등).
    위반 시 HTTPException(404/403/409).
    """
    cur.execute(
        "SELECT adm_cd, status, version FROM review_markup WHERE id = %s FOR UPDATE",
        (markup_id,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"마크업 {markup_id} 없음")
    check_admin_access(user, row["adm_cd"].strip())
    cur_status = row["status"]
    if cur_status == to_status:
        return "noop"                                      # 재호출 안전(멱등)
    allowed = _ALLOWED_FROM.get(to_status, set())
    if cur_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"{cur_status}→{to_status} 전이 불가 (허용 from={sorted(allowed)})",
        )
    if expected_version is not None and row["version"] != expected_version:
        raise HTTPException(
            status_code=409,
            detail=(f"version 충돌 — 서버 {row['version']} ≠ 요청 {expected_version}. "
                    f"새로고침 후 재시도"),
        )
    actor_cd = user["admin_cd"]                            # CHAR(8) 컬럼용(plugin=NULL)
    actor_label = actor_cd or "plugin"                     # 이벤트 로그용
    sets = ["status = %s", "version = version + 1"]
    params: list = [to_status]
    if to_status == "applied":
        sets += ["applied_at = now()", "applied_by = %s",
                 "reject_reason = NULL", "rejected_by = NULL", "rejected_at = NULL"]
        params.append(actor_cd)
    elif to_status == "rejected":
        sets += ["rejected_at = now()", "rejected_by = %s", "reject_reason = %s",
                 "applied_by = NULL", "applied_at = NULL"]
        params += [actor_cd, reason]
    params.append(markup_id)
    cur.execute(f"UPDATE review_markup SET {', '.join(sets)} WHERE id = %s", params)
    cur.execute(
        """INSERT INTO markup_event (markup_id, from_status, to_status, actor, reason)
           VALUES (%s, %s, %s, %s, %s)""",
        (markup_id, cur_status, to_status, actor_label, reason),
    )
    return "changed"


def require_processor(user: dict = Depends(get_user)) -> dict:
    """마크업 처리(반영/반려) 권한 — 웹 작업자(master). plugin 도 허용(운영 보조)."""
    if user["role"] not in ("master", "plugin"):
        raise HTTPException(status_code=403, detail="반영/반려는 master 만 가능")
    return user


@app.patch("/api/markup/{markup_id}/apply", status_code=204)
def apply_markup(markup_id: int, body: ApplyBody | None = None,
                 user: dict = Depends(require_processor)):
    """pending → applied. 웹 작업자가 QGIS 로 경계를 고친 뒤 요청 카드에서 '반영' 처리.
    body.version(선택) 불일치 시 409."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            _transition(cur, markup_id, "applied", user,
                        expected_version=body.version if body else None)
        conn.commit()
    return Response(status_code=204)


@app.patch("/api/markup/{markup_id}/reject", status_code=204)
def reject_markup(markup_id: int, body: RejectBody,
                  user: dict = Depends(require_processor)):
    """pending → rejected. 웹 작업자가 수행 불가/오요청을 사유와 함께 반려(종결).
    발주자의 자기 요청 취소는 DELETE(회수)로."""
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=400, detail="reason 필수")
    with pool.connection() as conn:
        with conn.cursor() as cur:
            _transition(cur, markup_id, "rejected", user, reason=body.reason.strip(),
                        expected_version=body.version)
        conn.commit()
    return Response(status_code=204)


@app.delete("/api/markup/{markup_id}")
def delete_markup(markup_id: int, user: dict = Depends(get_user)):
    """수정요청 회수 — 작성자가 잘못 올린 요청을 지움.

    대기(pending)·반려(rejected) 요청은 삭제 가능(실제 경계를 바꾼 적 없음).
    이미 반영(applied)된 요청만 이력 보존을 위해 거부(409).
    권한은 본인 adm_cd 만(마스터/플러그인은 전체).
    """
    cur = fetchone(
        "SELECT adm_cd, status FROM review_markup WHERE id = %s", (markup_id,)
    )
    if not cur:
        raise HTTPException(status_code=404, detail="마크업을 찾을 수 없습니다")
    check_admin_access(user, cur["adm_cd"].strip())
    assert_can_edit(user)
    if cur["status"] == "applied":
        raise HTTPException(
            status_code=409, detail="반영된 요청은 이력 보존을 위해 삭제할 수 없습니다"
        )
    execute("DELETE FROM review_markup WHERE id = %s", (markup_id,))
    return Response(status_code=204)


# ---------------------------------------------------------------- 관리 현황 (00000000 전용)
@app.get("/api/admin/markup-stats")
def admin_markup_stats(_: dict = Depends(require_superadmin)):
    """지역별 수정요청 현황 — 읍면(adm_cd) 단위로 상태별 건수 집계.

    수정요청이 한 건이라도 있는 읍면만 반환. 대기 많은 순 → 총건수 순.
    """
    rows = fetchall(
        """
        SELECT n.adm_cd, n.adm_nm, n.sgg_nm, n.sido_nm,
               COUNT(*)                                        AS total,
               COUNT(*) FILTER (WHERE m.status = 'pending')    AS pending,
               COUNT(*) FILTER (WHERE m.status = 'applied')    AS applied,
               COUNT(*) FILTER (WHERE m.status = 'rejected')   AS rejected,
               MAX(m.created_at)                               AS last_request_at
        FROM review_markup m
        JOIN admin_node n ON n.adm_cd = m.adm_cd
        GROUP BY n.adm_cd, n.adm_nm, n.sgg_nm, n.sido_nm
        ORDER BY pending DESC, total DESC, n.adm_cd
        """
    )
    return [{**r, "adm_cd": r["adm_cd"].strip()} for r in rows]


@app.get("/api/admin/upload-history")
def admin_upload_history(_: dict = Depends(require_superadmin)):
    """데이터 업로드 이력 — 읍면 단위로 항공사진(COG)·경계(boundary) 적재 현황.

    cog_catalog.published_at = 항공사진 업로드 시각,
    boundary 의 행 수/최종 updated_at = 경계 적재 건수/최종 업로드 시각.
    둘 중 하나라도 있는 읍면만, 최근 활동 순으로 반환.
    """
    rows = fetchall(
        """
        SELECT n.adm_cd, n.adm_nm, n.sgg_nm, n.sido_nm,
               c.published_at        AS cog_published_at,
               b.feature_count       AS boundary_count,
               b.boundary_updated_at AS boundary_updated_at,
               b.last_updated_by     AS boundary_updated_by
        FROM admin_node n
        LEFT JOIN cog_catalog c ON c.adm_cd = n.adm_cd
        LEFT JOIN (
            SELECT adm_cd,
                   COUNT(*)            AS feature_count,
                   MAX(updated_at)     AS boundary_updated_at,
                   (ARRAY_AGG(updated_by ORDER BY updated_at DESC NULLS LAST))[1]
                                       AS last_updated_by
            FROM boundary
            GROUP BY adm_cd
        ) b ON b.adm_cd = n.adm_cd
        WHERE c.adm_cd IS NOT NULL OR b.adm_cd IS NOT NULL
        ORDER BY GREATEST(
                   COALESCE(c.published_at, 'epoch'::timestamptz),
                   COALESCE(b.boundary_updated_at, 'epoch'::timestamptz)
                 ) DESC, n.adm_cd
        """
    )
    return [{**r, "adm_cd": r["adm_cd"].strip()} for r in rows]


@app.get("/api/admin/markup-list")
def admin_markup_list(_: dict = Depends(require_superadmin)):
    """개별 수정요청 전체 목록(전국·모든 상태) — 총괄(00000000) 관리용.
    읍면별로 모아 보고 개별 삭제하기 위함. 최신(id 큰 것)이 먼저."""
    rows = fetchall(
        """
        SELECT m.id, m.adm_cd, n.adm_nm, m.kind, m.status,
               m.attrs->>'note' AS note,
               m.created_by, m.created_at
        FROM review_markup m
        LEFT JOIN admin_node n ON n.adm_cd = m.adm_cd
        ORDER BY m.adm_cd, m.id DESC
        """
    )
    return [{**r, "adm_cd": r["adm_cd"].strip()} for r in rows]


@app.delete("/api/admin/markup/{markup_id}")
def admin_delete_markup(markup_id: int, _: dict = Depends(require_superadmin)):
    """개별 수정요청 삭제 — 총괄(00000000) 전용. 상태 무관(반영 이력 포함) 삭제.
    일반 DELETE /api/markup/{id} 의 '반영 보존' 규칙을 우회한다. 복구 불가.
    markup_event 는 FK ON DELETE CASCADE 로 함께 삭제."""
    row = execute(
        "DELETE FROM review_markup WHERE id = %s RETURNING id", (markup_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="마크업을 찾을 수 없습니다")
    return {"deleted": row["id"]}


@app.delete("/api/admin/markup")
def admin_delete_all_markup(_: dict = Depends(require_superadmin)):
    """전국 모든 수정요청 일괄 삭제 — 총괄(00000000) 전용. 상태 무관. 복구 불가.
    삭제 건수를 반환(프론트 확인 표시용)."""
    row = execute(
        "WITH d AS (DELETE FROM review_markup RETURNING 1) SELECT count(*) AS n FROM d"
    )
    return {"deleted": row["n"] if row else 0}


# ---------------------------------------------------------------- 공간정보 내보내기 (전국/시도)
# kind → 파일명 라벨. (라인등록/삭제표기/속성등록만 대상)
_EXPORT_KINDS = [("add", "라인등록"), ("delete_mark", "삭제표기"), ("attr", "속성등록")]
_EXPORT_KIND_SET = {k for k, _ in _EXPORT_KINDS}


def _parse_status(status: str) -> list[str]:
    """status 쿼리(콤마구분)를 화이트리스트로 검증. 기본 pending."""
    allowed = {"pending", "applied", "rejected"}
    vals = [s.strip() for s in (status or "").split(",") if s.strip()]
    picked = [s for s in vals if s in allowed]
    return picked or ["pending"]


@app.get("/api/admin/markup-sido-summary")
def markup_sido_summary(status: str = "pending", _: dict = Depends(require_master)):
    """시도별 수정요청 건수 요약 — 체크리스트 UI 구성용. 데이터 있는 시도만 반환.
    status(콤마구분, 기본 pending)로 상태 필터. '전국' 합계도 함께 준다."""
    statuses = _parse_status(status)
    rows = fetchall(
        """
        SELECT n.sido_cd, n.sido_nm, m.kind, count(*) AS c
        FROM review_markup m
        JOIN admin_node n ON n.adm_cd = m.adm_cd
        WHERE m.status = ANY(%s) AND m.kind = ANY(%s)
        GROUP BY n.sido_cd, n.sido_nm, m.kind
        ORDER BY n.sido_cd
        """,
        (statuses, list(_EXPORT_KIND_SET)),
    )
    by_sido: dict[str, dict] = {}
    nation = {"add": 0, "delete_mark": 0, "attr": 0, "total": 0}
    for r in rows:
        s = by_sido.setdefault(
            r["sido_cd"],
            {"sido_cd": r["sido_cd"], "sido_nm": r["sido_nm"],
             "add": 0, "delete_mark": 0, "attr": 0, "total": 0},
        )
        s[r["kind"]] += r["c"]
        s["total"] += r["c"]
        nation[r["kind"]] += r["c"]
        nation["total"] += r["c"]
    return {"nation": nation, "sido": list(by_sido.values())}


@app.get("/api/admin/markup-export")
def markup_export(
    scopes: str,
    status: str = "pending",
    _: dict = Depends(require_master),
):
    """선택한 범위(scopes)의 수정요청 공간정보를 kind별 GeoJSON 으로 만들어 ZIP 반환.
    scopes: 콤마구분. 'all'=전국(전체 집계), 그 외 값=시도코드(2자리).
    파일명: 전국=전국_수정요청_{kind라벨}_{날짜}.geojson,
           시도=시도명_수정요청_{kind라벨}_{시도코드2자리}.geojson (데이터 있는 kind만)."""
    statuses = _parse_status(status)
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    if not scope_list:
        raise HTTPException(status_code=400, detail="scopes 가 비어 있습니다")

    rows = fetchall(
        """
        SELECT m.id, m.adm_cd, n.adm_nm, n.sido_cd, n.sido_nm,
               m.kind, m.status, m.attrs, m.created_by, m.created_at,
               ST_AsGeoJSON(ST_Transform(m.geom, 4326))::json AS geometry
        FROM review_markup m
        JOIN admin_node n ON n.adm_cd = m.adm_cd
        WHERE m.status = ANY(%s) AND m.kind = ANY(%s)
        ORDER BY m.adm_cd, m.id
        """,
        (statuses, list(_EXPORT_KIND_SET)),
    )

    def to_feature(r: dict) -> dict:
        attrs = r.get("attrs") or {}
        return {
            "type": "Feature",
            "geometry": r["geometry"],
            "properties": {
                "id": r["id"],
                "adm_cd": r["adm_cd"].strip(),
                "adm_nm": r["adm_nm"],
                "kind": r["kind"],
                "status": r["status"],
                "ri_nm": attrs.get("ri_nm"),
                "ri_cd": attrs.get("ri_cd"),
                "note": attrs.get("note"),
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            },
        }

    # scope 라벨 결정 + 해당 scope 에 포함되는 행 선별
    def scope_label(sc: str) -> str | None:
        if sc == "all":
            return "전국"
        for r in rows:
            if r["sido_cd"] == sc:
                return r["sido_nm"]
        return None  # 데이터 없는 시도코드

    buf = io.BytesIO()
    file_count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for sc in scope_list:
            label = scope_label(sc)
            if label is None:
                continue
            subset = rows if sc == "all" else [r for r in rows if r["sido_cd"] == sc]
            # 전국은 지역코드 생략, 시도는 시도코드(2자리)를 접미로.
            code_part = "" if sc == "all" else f"_{sc}"
            for kind, klabel in _EXPORT_KINDS:
                feats = [to_feature(r) for r in subset if r["kind"] == kind]
                if not feats:
                    continue
                fc = {"type": "FeatureCollection", "features": feats}
                fname = f"{label}_수정요청_{klabel}{code_part}.geojson"
                z.writestr(fname, json.dumps(fc, ensure_ascii=False))
                file_count += 1

    if file_count == 0:
        raise HTTPException(status_code=404, detail="선택한 범위에 내보낼 수정요청이 없습니다")

    buf.seek(0)
    zipname = "수정요청_공간정보.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(zipname)}"
        },
    )
