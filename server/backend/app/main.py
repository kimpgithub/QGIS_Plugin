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
import json
import os
import time
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
    return {"role": role, "admin_cd": payload.get("sub")}


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
        "SELECT admin_cd, password_hash, role FROM auth WHERE admin_cd = %s",
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
    return {"token": token, "user": user_obj}


# ---------------------------------------------------------------- admins
@app.get("/api/admins")
def list_admins(_: dict = Depends(get_user)):
    """COG 가 업로드된 admin 만 (AdminUnit[]).
    admin_node ⨯ cog_catalog INNER JOIN — picker 에 검수 대상만 노출.
    플러그인의 /api/admins 연결 테스트는 list length 만 보므로 호환."""
    rows = fetchall(
        """
        SELECT n.adm_cd, n.adm_nm,
               n.sgg_cd AS sigungu_cd, n.sgg_nm AS sigungu_nm,
               n.sido_cd, n.sido_nm
        FROM admin_node n
        JOIN cog_catalog c ON c.adm_cd = n.adm_cd
        ORDER BY n.sido_cd, n.sgg_cd, n.adm_cd
        """
    )
    return [{**r, "adm_cd": r["adm_cd"].strip()} for r in rows]


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
    row = fetchone(
        """
        SELECT json_build_object(
          'type', 'FeatureCollection',
          'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature', 'id', gid,
            'geometry', ST_AsGeoJSON(ST_Transform(geom, 4326))::json,
            'properties', json_build_object(
              'gid', gid, 'adm_cd', adm_cd, 'adm_nm', adm_nm,
              'ri_cd', ri_cd, 'ri_nm', ri_nm, 'status', status,
              'remark', remark,
              'updated_at', updated_at, 'updated_by', updated_by)
          )) FILTER (WHERE gid IS NOT NULL), '[]'::json)
        ) AS fc
        FROM boundary WHERE adm_cd = %s
        """,
        (adm_cd,),
    )
    return row["fc"]


@app.put("/api/boundary")
def replace_boundary(body: dict, srid: int = 4326, user: dict = Depends(require_plugin)):
    """플러그인 전용 — 제출된 읍면(adm_cd)의 경계를 **전체 교체**(DELETE 후 INSERT).

    QGIS 제출 = 해당 읍면의 최신 스냅샷. 키 매칭(upsert)이 없으므로 ri_cd(부호)가
    빈 폴리곤도 여러 개 그대로 저장된다 — 빈 부호는 웹에서 발주자가 속성등록
    요청으로 채운다(가짜 일련번호 자동부여 제거).

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

    # 사전 검사 — *실제 부호*가 한 읍면 안에서 중복되면 데이터 오류 (빈 부호는 허용).
    seen: set = set()
    dups: set = set()
    rows = []
    for feat in body["features"]:
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if not geom:
            continue
        adm_cd = props.get("adm_cd")
        if not adm_cd:
            raise HTTPException(status_code=400, detail="각 feature는 properties.adm_cd 필요")
        ri_cd = _norm_ri(props)
        if ri_cd is not None:
            key = (adm_cd, ri_cd)
            if key in seen:
                dups.add(f"{adm_cd}/{ri_cd}")
            seen.add(key)
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
    if dups:
        sample = ", ".join(sorted(dups)[:10])
        raise HTTPException(
            status_code=400,
            detail=(f"같은 읍면 안에 동일한 부호(ri_cd)가 중복됩니다 — "
                    f"부호를 정정하거나 비워서 제출하세요. "
                    f"중복({len(dups)}건): {sample}"),
        )

    admins = sorted({r["adm_cd"] for r in rows})
    deleted = inserted = 0
    with pool.connection() as conn:
        with conn.cursor() as cur:
            if rows:
                # 1) 제출된 읍면의 기존 경계 전부 삭제
                cur.execute(
                    "DELETE FROM boundary WHERE adm_cd = ANY(%s)", (admins,))
                deleted = cur.rowcount
                # 2) 새 경계 일괄 삽입 (빈 ri_cd 는 NULL 그대로)
                cur.execute(
                    """
                    INSERT INTO boundary
                      (geom, adm_cd, adm_nm, ri_cd, ri_nm, status, remark, updated_by)
                    SELECT ST_Multi(ST_Transform(ST_SetSRID(
                             ST_GeomFromGeoJSON((f->'geometry')::text), %s), 5179)),
                           f->>'adm_cd', f->>'adm_nm',
                           f->>'ri_cd',  f->>'ri_nm',
                           COALESCE(f->>'status', 'draft'),
                           f->>'remark', f->>'updated_by'
                    FROM jsonb_array_elements(%s::jsonb) AS f
                    """,
                    (srid, json.dumps(rows)),
                )
                inserted = cur.rowcount
        conn.commit()
    return {"affected": inserted, "inserted": inserted, "deleted": deleted,
            "admins": admins, "features": len(body["features"])}


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
    if cur["status"] == "applied":
        raise HTTPException(
            status_code=409, detail="반영된 요청은 이력 보존을 위해 삭제할 수 없습니다"
        )
    execute("DELETE FROM review_markup WHERE id = %s", (markup_id,))
    return Response(status_code=204)
