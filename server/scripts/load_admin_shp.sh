#!/usr/bin/env bash
# bnd_adm_pg.shp(3561 행정읍면) 을 admin_outline + admin_node 에 적재.
# - SHP 는 저장소 루트 data/bnd_adm_pg.shp (git-lfs, EPSG:5179 가정/없으면 -t_srs 가 변환).
# - dbf 컬럼: sido_cd/sido_nm/sigungu_cd/sigungu_nm/adm_cd/adm_nm.
# - 멱등: ON CONFLICT (adm_cd) DO UPDATE.
#
# 사용:
#   cd /srv/gis/compose && ./../../scripts/load_admin_shp.sh
#   또는: server/scripts/load_admin_shp.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
COMPOSE=$REPO/server/compose
SHP=$REPO/data/bnd_adm_pg.shp

if [ ! -s "$SHP" ] || head -c 64 "$SHP" | grep -q 'git-lfs'; then
  echo "SHP 가 LFS 포인터입니다 — 'git lfs pull' 먼저 실행하세요." >&2
  exit 1
fi

cd "$COMPOSE"
set -a; . ./.env; set +a

NET=$(docker compose ps --format json db 2>/dev/null | python3 -c 'import sys,json; print(list(json.loads(sys.stdin.read()).get("Networks",{}).keys() if False else []))' 2>/dev/null || true)
NET=${NET:-gis_gisnet}

echo "[1/2] ogr2ogr → adm_load (staging)"
docker run --rm --network "$NET" \
  -v "$REPO/data":/data:ro \
  ghcr.io/osgeo/gdal:alpine-small-latest \
  ogr2ogr -f PostgreSQL \
    "PG:host=db port=5432 dbname=$POSTGRES_DB user=$POSTGRES_USER password=$POSTGRES_PASSWORD" \
    /data/bnd_adm_pg.shp \
    -nln adm_load -overwrite \
    -t_srs EPSG:5179 \
    -nlt PROMOTE_TO_MULTI \
    -lco GEOMETRY_NAME=geom -lco FID=fid -lco PRECISION=NO

echo "[2/2] admin_outline + admin_node upsert"
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
BEGIN;
INSERT INTO admin_outline (adm_cd, adm_nm, sgg_cd, sgg_nm, sido_cd, sido_nm, geom)
SELECT adm_cd, adm_nm, sigungu_cd, sigungu_nm, sido_cd, sido_nm, ST_Multi(geom)
FROM adm_load
ON CONFLICT (adm_cd) DO UPDATE SET
  adm_nm=EXCLUDED.adm_nm, sgg_cd=EXCLUDED.sgg_cd, sgg_nm=EXCLUDED.sgg_nm,
  sido_cd=EXCLUDED.sido_cd, sido_nm=EXCLUDED.sido_nm, geom=EXCLUDED.geom;

INSERT INTO admin_node (adm_cd, adm_nm, sgg_cd, sgg_nm, sido_cd, sido_nm)
SELECT adm_cd, adm_nm, sigungu_cd, sigungu_nm, sido_cd, sido_nm
FROM adm_load
ON CONFLICT (adm_cd) DO UPDATE SET
  adm_nm=EXCLUDED.adm_nm, sgg_cd=EXCLUDED.sgg_cd, sgg_nm=EXCLUDED.sgg_nm,
  sido_cd=EXCLUDED.sido_cd, sido_nm=EXCLUDED.sido_nm;

DROP TABLE adm_load;
SELECT 'admin_outline' AS tbl, COUNT(*) FROM admin_outline
UNION ALL SELECT 'admin_node', COUNT(*) FROM admin_node;
COMMIT;
SQL
