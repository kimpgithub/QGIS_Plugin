"""
검수 웹 API — FastAPI
인증 2종:
  - require_review : 발주자 웹 (공유 비밀번호 → HMAC 서명 쿠키)
  - require_plugin : 대전 QGIS 플러그인 (Bearer 토큰 1개)
  - require_any    : 둘 중 하나 (데이터 GET 엔드포인트)
데이터 흐름: 대전↔서버는 전부 Funnel HTTPS 경유. 좌표계 DB=EPSG:5179.
빈 DB에서도 에러 없이 동작.
"""
import base64
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from . import web as web_router

# ---------------------------------------------------------------- config
DB_HOST = os.environ.get("DB_HOST", "db")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_USER = os.environ["POSTGRES_USER"]
DB_PASS = os.environ["POSTGRES_PASSWORD"]
DB_NAME = os.environ["POSTGRES_DB"]
REVIEW_PASSWORD = os.environ["REVIEW_PASSWORD"]
SESSION_SECRET = os.environ["SESSION_SECRET"].encode()
PLUGIN_TOKEN = os.environ["PLUGIN_TOKEN"]
S3_BUCKET = os.environ.get("S3_BUCKET", "gis-scan")
TITILER_PREFIX = os.environ.get("TITILER_PREFIX", "/tiles")
SESSION_MAX_AGE = 7 * 24 * 3600  # 7일

CONNINFO = f"host={DB_HOST} port={DB_PORT} user={DB_USER} password={DB_PASS} dbname={DB_NAME}"

pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = ConnectionPool(CONNINFO, min_size=1, max_size=8, kwargs={"row_factory": dict_row})
    pool.wait(timeout=30)
    web_router.init(pool)
    yield
    pool.close()


app = FastAPI(title="GIS 검수 API", lifespan=lifespan)
app.include_router(web_router.router)

# CORS — OpenLayers 프론트 로컬 개발 지원.
# allow_credentials=True 와 allow_origins=["*"] 는 양립 불가하므로
# localhost / 127.0.0.1 의 모든 포트를 정규식으로 허용. 운영 단계 축소는 추후.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- auth
def _sign(msg: str) -> str:
    return hmac.new(SESSION_SECRET, msg.encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    ts = str(int(time.time()))
    return f"{ts}.{_sign(ts)}"


def verify_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    ts, sig = token.split(".", 1)
    if not hmac.compare_digest(sig, _sign(ts)):
        return False
    try:
        return (time.time() - int(ts)) < SESSION_MAX_AGE
    except ValueError:
        return False


def is_plugin(authorization: str | None) -> bool:
    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(authorization[7:].strip(), PLUGIN_TOKEN)


def require_review(gis_session: str | None = Cookie(default=None)):
    """발주자 웹 전용."""
    if not verify_token(gis_session):
        raise HTTPException(status_code=401, detail="인증이 필요합니다")
    return "reviewer"


def require_plugin(authorization: str | None = Header(default=None)):
    """대전 플러그인 전용 (Bearer 토큰)."""
    if not is_plugin(authorization):
        raise HTTPException(status_code=401, detail="플러그인 토큰이 필요합니다")
    return "plugin"


def require_any(
    gis_session: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
):
    """발주자 쿠키 또는 플러그인 Bearer 둘 중 하나."""
    if verify_token(gis_session):
        return "reviewer"
    if is_plugin(authorization):
        return "plugin"
    raise HTTPException(status_code=401, detail="인증이 필요합니다")


# ---------------------------------------------------------------- models
class LoginBody(BaseModel):
    password: str


class CogRegister(BaseModel):
    adm_cd: str
    s3_key: str
    bounds: dict | list           # GeoJSON Polygon 또는 [minx,miny,maxx,maxy]
    width: int | None = None
    height: int | None = None
    srid: int = 5179              # bounds 좌표계 (COG는 보통 5179)


# ---------------------------------------------------------------- db helpers
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


# ---------------------------------------------------------------- routes: auth
@app.get("/api/health")
def health():
    try:
        fetchone("SELECT 1 AS ok")
        return {"status": "ok", "db": "ok"}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "degraded", "db": str(e)})


@app.post("/api/login")
def login(body: LoginBody, response: Response):
    if not hmac.compare_digest(body.password, REVIEW_PASSWORD):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다")
    response.set_cookie(
        "gis_session", make_token(),
        max_age=SESSION_MAX_AGE, httponly=True, samesite="lax",
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("gis_session")
    return {"ok": True}


@app.get("/api/me")
def me(gis_session: str | None = Cookie(default=None)):
    return {"authenticated": verify_token(gis_session)}


# ---------------------------------------------------------------- routes: read (web + plugin)
@app.get("/api/admins")
def list_admins(_: str = Depends(require_any)):
    """cog_catalog 기반 검수 대상 admin 목록. 빈 카탈로그면 []."""
    rows = fetchall(
        """
        SELECT c.adm_cd, b.adm_nm, c.s3_key, c.width, c.height, c.published_at,
               ST_AsGeoJSON(ST_Transform(c.bounds, 4326))::json AS bounds_geojson,
               (SELECT count(*) FROM review_markup m
                  WHERE m.adm_cd = c.adm_cd AND m.status = 'pending') AS open_markups
        FROM cog_catalog c
        LEFT JOIN LATERAL (
            SELECT adm_nm FROM boundary WHERE adm_cd = c.adm_cd LIMIT 1
        ) b ON true
        ORDER BY c.adm_cd
        """
    )
    return {"admins": rows}


@app.get("/api/boundary")
def get_boundary(adm_cd: str, _: str = Depends(require_any)):
    """해당 admin 경계 → GeoJSON FeatureCollection (EPSG:4326). 없으면 빈 FC."""
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
              'updated_at', updated_at, 'updated_by', updated_by)
          )) FILTER (WHERE gid IS NOT NULL), '[]'::json)
        ) AS fc
        FROM boundary WHERE adm_cd = %s
        """,
        (adm_cd,),
    )
    return row["fc"]


@app.get("/api/cog/{adm_cd}")
def get_cog(adm_cd: str, _: str = Depends(require_any)):
    """cog_catalog의 s3_key + titiler 타일 URL 템플릿."""
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
        "adm_cd": row["adm_cd"], "s3_key": row["s3_key"], "s3_url": s3_url,
        "width": row["width"], "height": row["height"],
        "published_at": row["published_at"], "bounds_geojson": row["bounds_geojson"],
        "bbox": bbox,
        "tile_url": f"{TITILER_PREFIX}/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png?url={enc}",
        "tilejson_url": f"{TITILER_PREFIX}/cog/WebMercatorQuad/tilejson.json?url={enc}",
    }


@app.get("/api/markup")
def get_markup(adm_cd: str | None = None, _: str = Depends(require_any)):
    """마크업 GeoJSON FeatureCollection (EPSG:4326). 대전 플러그인의 마크업 회수용.
    신규 스키마: kind add/delete/attr, status pending/applied/rejected, attrs JSONB.
    플러그인은 보통 status='applied' 만 회수해 PostGIS 본체에 반영."""
    where, params = "", ()
    if adm_cd:
        where, params = "WHERE adm_cd = %s", (adm_cd,)
    row = fetchone(
        f"""
        SELECT json_build_object(
          'type', 'FeatureCollection',
          'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature', 'id', id,
            'geometry', ST_AsGeoJSON(ST_Transform(geom, 4326))::json,
            'properties', json_build_object(
              'id', id, 'kind', kind, 'attrs', attrs,
              'adm_cd', adm_cd, 'status', status,
              'reject_reason', reject_reason,
              'created_by', created_by, 'created_at', created_at,
              'applied_at', applied_at, 'rejected_at', rejected_at)
          )) FILTER (WHERE id IS NOT NULL), '[]'::json)
        ) AS fc
        FROM review_markup {where}
        """,
        params,
    )
    return row["fc"]


# 구 POST /api/markup, PATCH /api/markup/{id} 제거 (2026-05-18).
# 검수자 측 마크업 CRUD 는 /web/markup 으로 이관. 플러그인측 신규 PATCH 가 필요해지면
# /api/markup/{id} (Bearer) 로 신규 스키마 기반 재신설 예정.


# ---------------------------------------------------------------- routes: plugin write
@app.put("/api/boundary")
def upsert_boundary(body: dict, srid: int = 4326, _: str = Depends(require_plugin)):
    """대전 플러그인이 보낸 GeoJSON FeatureCollection을 boundary 테이블에 upsert.
    키: adm_cd + ri_cd. 입력 좌표계는 ?srid= 로 지정 (4326 기본, 5179 허용).
    geometry는 Polygon/MultiPolygon 모두 허용 → MultiPolygon으로 강제."""
    if srid not in (4326, 5179):
        raise HTTPException(status_code=400, detail="srid는 4326 또는 5179만 허용")
    if body.get("type") != "FeatureCollection" or not isinstance(body.get("features"), list):
        raise HTTPException(status_code=400, detail="GeoJSON FeatureCollection이 필요합니다")

    inserted = updated = 0
    with pool.connection() as conn:
        with conn.cursor() as cur:
            for feat in body["features"]:
                geom = feat.get("geometry")
                props = feat.get("properties") or {}
                if not geom:
                    continue
                adm_cd = props.get("adm_cd")
                if not adm_cd:
                    raise HTTPException(status_code=400, detail="각 feature는 properties.adm_cd 필요")
                ri_cd = props.get("ri_cd")
                params_geom = (json.dumps(geom), srid)
                geom_sql = "ST_Multi(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), %s), 5179))"
                # UPDATE 시도 (ri_cd NULL 도 매칭)
                cur.execute(
                    f"""
                    UPDATE boundary
                       SET geom = {geom_sql}, adm_nm = %s, ri_nm = %s,
                           status = COALESCE(%s, status),
                           updated_at = now(), updated_by = %s
                     WHERE adm_cd = %s AND ri_cd IS NOT DISTINCT FROM %s
                    """,
                    (*params_geom, props.get("adm_nm"), props.get("ri_nm"),
                     props.get("status"), props.get("updated_by") or "daejeon",
                     adm_cd, ri_cd),
                )
                if cur.rowcount > 0:
                    updated += cur.rowcount
                else:
                    cur.execute(
                        f"""
                        INSERT INTO boundary
                          (geom, adm_cd, adm_nm, ri_cd, ri_nm, status, updated_by)
                        VALUES ({geom_sql}, %s, %s, %s, %s,
                                COALESCE(%s, 'draft'), %s)
                        """,
                        (*params_geom, adm_cd, props.get("adm_nm"), ri_cd,
                         props.get("ri_nm"), props.get("status"),
                         props.get("updated_by") or "daejeon"),
                    )
                    inserted += 1
        conn.commit()
    return {"ok": True, "inserted": inserted, "updated": updated,
            "features": len(body["features"])}


@app.post("/api/cog")
def register_cog(body: CogRegister, _: str = Depends(require_plugin)):
    """플러그인이 COG 업로드(S3) 후 호출 → cog_catalog 등록/갱신 (adm_cd PK upsert)."""
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
