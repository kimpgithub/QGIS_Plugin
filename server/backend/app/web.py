"""
검수 웹 (/web/*) — admin_cd 계정 로그인 + JWT.
플러그인용 /api/* 와 격리. JWT_SECRET 누락 시 import 단계에서 즉시 실패.

role:
  - normal : 자기 admin_cd 데이터만 접근 가능
  - master : 전국 접근 가능
"""
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_EXPIRES_MIN = int(os.environ.get("JWT_EXPIRES_MIN", "480"))
JWT_ALGO = "HS256"
S3_BUCKET = os.environ.get("S3_BUCKET", "gis-scan")
TITILER_PREFIX = os.environ.get("TITILER_PREFIX", "/tiles")

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/web", tags=["web"])

# 순환 import 회피용 — main 에서 set 한다.
_pool = None


def init(pool):
    """main.py 가 ConnectionPool 을 주입."""
    global _pool
    _pool = pool


# ---------------------------------------------------------------- db helpers
def _fetchone(sql: str, params=()):
    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _fetchall(sql: str, params=()):
    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _execute(sql: str, params=()):
    with _pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone() if cur.description else None
        conn.commit()
        return row


# ---------------------------------------------------------------- auth
class LoginBody(BaseModel):
    admin_cd: str
    password: str


def _issue_token(admin_cd: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": admin_cd,
        "role": role,
        "iat": now,
        "exp": now + JWT_EXPIRES_MIN * 60,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    if not creds or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer 토큰이 필요합니다")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="토큰이 만료되었습니다") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않습니다") from None
    return {"admin_cd": payload["sub"], "role": payload["role"]}


def require_master(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "master":
        raise HTTPException(status_code=403, detail="master 권한이 필요합니다")
    return user


def _check_admin_access(user: dict, adm_cd: str):
    """normal 은 본인 admin_cd 만, master 는 전국 허용."""
    if user["role"] == "master":
        return
    if user["admin_cd"] != adm_cd:
        raise HTTPException(
            status_code=403,
            detail="본인 admin_cd 외 접근 불가",
        )


# ---------------------------------------------------------------- /web/login
@router.post("/login")
def login(body: LoginBody, request: Request):
    row = _fetchone(
        "SELECT admin_cd, password_hash, role FROM auth WHERE admin_cd = %s",
        (body.admin_cd,),
    )
    # 타이밍 공격 완화 — 계정 없어도 해시 비교 수행
    ok = False
    if row:
        try:
            ok = pwd_ctx.verify(body.password, row["password_hash"])
        except Exception:  # noqa: BLE001
            ok = False
    else:
        pwd_ctx.dummy_verify()
    # login_log 기록 (성공/실패 모두). admin_cd 는 입력값 그대로(없는 계정도 흔적 남김).
    fwd = request.headers.get("x-forwarded-for")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None)
    ua = request.headers.get("user-agent")
    try:
        _execute(
            "INSERT INTO login_log (admin_cd, ip, user_agent) VALUES (%s, %s, %s)",
            (body.admin_cd[:8], ip, ua),
        )
    except Exception:  # noqa: BLE001
        pass  # 로그 실패가 로그인을 막지 않도록
    if not ok:
        raise HTTPException(status_code=401, detail="admin_cd 또는 비밀번호가 올바르지 않습니다")
    token = _issue_token(row["admin_cd"].strip(), row["role"])
    return {"access_token": token, "role": row["role"], "admin_cd": row["admin_cd"].strip()}


# ---------------------------------------------------------------- /web/admin_tree (master)
@router.get("/admin_tree")
def admin_tree(_: dict = Depends(require_master)):
    """sido → sgg → adm 3-단 트리. admin_node 비어있으면 sido=[]."""
    rows = _fetchall(
        """
        SELECT adm_cd, adm_nm, sgg_cd, sgg_nm, sido_cd, sido_nm
        FROM admin_node
        ORDER BY sido_cd, sgg_cd, adm_cd
        """
    )
    sido_map: dict = {}
    for r in rows:
        s = sido_map.setdefault(
            r["sido_cd"],
            {"sido_cd": r["sido_cd"], "sido_nm": r["sido_nm"], "_sgg": {}},
        )
        g = s["_sgg"].setdefault(
            r["sgg_cd"],
            {"sgg_cd": r["sgg_cd"], "sgg_nm": r["sgg_nm"], "adm": []},
        )
        g["adm"].append({"adm_cd": r["adm_cd"].strip(), "adm_nm": r["adm_nm"]})
    sido_list = []
    for s in sido_map.values():
        sido_list.append({
            "sido_cd": s["sido_cd"], "sido_nm": s["sido_nm"],
            "sgg": list(s["_sgg"].values()),
        })
    return {"sido": sido_list}


# ---------------------------------------------------------------- /web/admin_line (stub)
@router.get("/admin_line")
def admin_line(adm_cd: str = Query(..., min_length=8, max_length=8),
               user: dict = Depends(get_current_user)):
    """읍면 외곽 폴리곤. admin_outline 테이블에서 조회 (SHP 적재 후 활성)."""
    _check_admin_access(user, adm_cd)
    row = _fetchone(
        """
        SELECT json_build_object(
          'type', 'FeatureCollection',
          'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature', 'id', adm_cd,
            'geometry', ST_AsGeoJSON(ST_Transform(geom, 4326))::json,
            'properties', json_build_object(
              'adm_cd', adm_cd, 'adm_nm', adm_nm,
              'sgg_cd', sgg_cd, 'sgg_nm', sgg_nm,
              'sido_cd', sido_cd, 'sido_nm', sido_nm)
          )) FILTER (WHERE adm_cd IS NOT NULL), '[]'::json)
        ) AS fc
        FROM admin_outline WHERE adm_cd = %s
        """,
        (adm_cd,),
    )
    fc = row["fc"]
    if not fc["features"]:
        # admin_outline 미적재 상태 — 운영팀이 SHP 반입할 때까지 501 신호.
        raise HTTPException(
            status_code=501,
            detail="admin_outline 데이터가 아직 적재되지 않았습니다",
        )
    return fc


# ---------------------------------------------------------------- /web/boundary
@router.get("/boundary")
def get_boundary(
    adm_cd: str = Query(..., min_length=8, max_length=8),
    user: dict = Depends(get_current_user),
):
    """해당 읍면 행정리 폴리곤들 (EPSG:4326)."""
    _check_admin_access(user, adm_cd)
    row = _fetchone(
        """
        SELECT json_build_object(
          'type', 'FeatureCollection',
          'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature', 'id', gid,
            'geometry', ST_AsGeoJSON(ST_Transform(geom, 4326))::json,
            'properties', json_build_object(
              'gid', gid, 'adm_cd', adm_cd, 'adm_nm', adm_nm,
              'ri_cd', ri_cd, 'ri_nm', ri_nm,
              'status', status, 'updated_at', updated_at)
          )) FILTER (WHERE gid IS NOT NULL), '[]'::json)
        ) AS fc
        FROM boundary WHERE adm_cd = %s
        """,
        (adm_cd,),
    )
    headers = {"Cache-Control": "max-age=5"}
    from fastapi.responses import JSONResponse
    return JSONResponse(content=row["fc"], headers=headers)


# ---------------------------------------------------------------- /web/cog/{adm_cd}
@router.get("/cog/{adm_cd}")
def get_cog(adm_cd: str, user: dict = Depends(get_current_user)):
    """cog_catalog 의 s3_key + titiler 타일 URL 템플릿. /api/cog 와 동일 shape."""
    if len(adm_cd) != 8:
        raise HTTPException(status_code=400, detail="adm_cd는 8자")
    _check_admin_access(user, adm_cd)
    row = _fetchone(
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


# ---------------------------------------------------------------- /web/markup
def _markup_row_to_dict(r: dict) -> dict:
    return {
        "id": r["id"],
        "adm_cd": r["adm_cd"].strip() if r.get("adm_cd") else None,
        "kind": r["kind"],
        "geometry": r["geometry"],
        "attrs": r.get("attrs"),
        "status": r["status"],
        "reject_reason": r.get("reject_reason"),
        "created_by": r.get("created_by"),
        "created_at": r["created_at"],
        "applied_at": r.get("applied_at"),
        "rejected_at": r.get("rejected_at"),
    }


_MARKUP_SELECT = """
    SELECT id, adm_cd, kind,
           ST_AsGeoJSON(ST_Transform(geom, 4326))::json AS geometry,
           attrs, status, reject_reason, created_by,
           created_at, applied_at, rejected_at
    FROM review_markup
"""


@router.get("/markup")
def list_markup(
    adm_cd: str = Query(..., min_length=8, max_length=8),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=500),
    user: dict = Depends(get_current_user),
):
    _check_admin_access(user, adm_cd)
    where = ["adm_cd = %s"]
    params: list = [adm_cd]
    if status:
        if status not in ("pending", "applied", "rejected"):
            raise HTTPException(status_code=400, detail="status는 pending/applied/rejected 중 하나")
        where.append("status = %s")
        params.append(status)
    where_sql = "WHERE " + " AND ".join(where)
    total_row = _fetchone(f"SELECT COUNT(*) AS c FROM review_markup {where_sql}", tuple(params))
    total = total_row["c"]
    offset = (page - 1) * size
    rows = _fetchall(
        f"""
        {_MARKUP_SELECT}
        {where_sql}
        ORDER BY (status = 'pending') DESC, created_at DESC
        LIMIT %s OFFSET %s
        """,
        (*params, size, offset),
    )
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [_markup_row_to_dict(r) for r in rows],
    }


_GEOM_TYPE_FOR_KIND = {
    "add": ("LineString",),
    "delete": ("LineString",),
    "attr": ("Point",),
}


class MarkupCreate(BaseModel):
    adm_cd: str
    kind: str
    geometry: dict
    attrs: dict | None = None


@router.post("/markup", status_code=201)
def create_markup(body: MarkupCreate, user: dict = Depends(get_current_user)):
    if len(body.adm_cd) != 8:
        raise HTTPException(status_code=400, detail="adm_cd는 8자")
    _check_admin_access(user, body.adm_cd)
    if body.kind not in _GEOM_TYPE_FOR_KIND:
        raise HTTPException(status_code=400, detail="kind는 add/delete/attr 중 하나")
    gtype = body.geometry.get("type")
    if gtype not in _GEOM_TYPE_FOR_KIND[body.kind]:
        raise HTTPException(
            status_code=400,
            detail=f"kind={body.kind} 는 geometry type {_GEOM_TYPE_FOR_KIND[body.kind]} 만 허용",
        )
    row = _execute(
        f"""
        WITH ins AS (
          INSERT INTO review_markup (adm_cd, kind, geom, attrs, created_by)
          VALUES (%s, %s,
                  ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 5179),
                  %s::jsonb, %s)
          RETURNING *
        )
        SELECT id, adm_cd, kind,
               ST_AsGeoJSON(ST_Transform(geom, 4326))::json AS geometry,
               attrs, status, reject_reason, created_by,
               created_at, applied_at, rejected_at
        FROM ins
        """,
        (body.adm_cd, body.kind, json.dumps(body.geometry),
         json.dumps(body.attrs) if body.attrs is not None else None,
         user["admin_cd"]),
    )
    return _markup_row_to_dict(row)


class MarkupPatch(BaseModel):
    status: str
    reject_reason: str | None = None


@router.patch("/markup/{markup_id}")
def patch_markup(markup_id: int, body: MarkupPatch, user: dict = Depends(get_current_user)):
    if body.status not in ("applied", "rejected"):
        raise HTTPException(status_code=400, detail="status는 applied/rejected 만 허용")
    if body.status == "rejected" and not (body.reject_reason and body.reject_reason.strip()):
        raise HTTPException(status_code=400, detail="rejected 시 reject_reason 필수")
    # 소유 확인 — normal 은 본인 adm_cd 마크업만
    cur = _fetchone(
        "SELECT adm_cd FROM review_markup WHERE id = %s",
        (markup_id,),
    )
    if not cur:
        raise HTTPException(status_code=404, detail="마크업을 찾을 수 없습니다")
    _check_admin_access(user, cur["adm_cd"].strip())

    if body.status == "applied":
        row = _execute(
            f"""
            WITH upd AS (
              UPDATE review_markup
                 SET status = 'applied', applied_at = now(), reject_reason = NULL
               WHERE id = %s
               RETURNING *
            )
            SELECT id, adm_cd, kind,
                   ST_AsGeoJSON(ST_Transform(geom, 4326))::json AS geometry,
                   attrs, status, reject_reason, created_by,
                   created_at, applied_at, rejected_at
            FROM upd
            """,
            (markup_id,),
        )
    else:
        row = _execute(
            f"""
            WITH upd AS (
              UPDATE review_markup
                 SET status = 'rejected', rejected_at = now(),
                     reject_reason = %s
               WHERE id = %s
               RETURNING *
            )
            SELECT id, adm_cd, kind,
                   ST_AsGeoJSON(ST_Transform(geom, 4326))::json AS geometry,
                   attrs, status, reject_reason, created_by,
                   created_at, applied_at, rejected_at
            FROM upd
            """,
            (body.reject_reason.strip(), markup_id),
        )
    return _markup_row_to_dict(row)
