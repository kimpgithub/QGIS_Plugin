# 검수 웹 API 레퍼런스 (프론트엔드 개발자용)

> **2026-05-20 전면 갱신 — 실제 구현(`backend/app/main.py`) 기준으로 재작성.**
> 이전 문서는 쿠키 세션 + 공유 비밀번호(§1~§6)와 미구현 `/web/*`(§7)을 설명했으나,
> 실제 백엔드는 **단일 Bearer(JWT) 인증 + 전부 `/api/*`** 로 구현돼 있다.
> 프론트엔드 contract 의 단일 원천은 `web/src/api/*.ts`, `web/src/types/index.ts`.

행정리 경계 검수 웹의 백엔드 API 계약서. 프론트엔드는 **React + Vite + OpenLayers + TypeScript**.
이 문서는 `backend/app/main.py` 의 실제 라우트/응답을 기준으로 한다.

- **API 베이스 URL**: `https://gis-hq.tail3b9b19.ts.net/api`
- **타일 베이스 URL**: `https://gis-hq.tail3b9b19.ts.net/tiles/...`
- 백엔드: FastAPI / 모든 응답 `Content-Type: application/json` (타일 제외)
- 인프라: nginx(리버스 프록시) → backend(FastAPI) / titiler / minio. 외부 노출은 nginx 하나(Tailscale Funnel).

---

## 0. CORS (로컬 개발)

backend 에 CORS 미들웨어 적용 — **로컬 개발 PC 에서 공개 API 직접 호출 가능**.

- 허용 Origin: 정규식 `https?://(localhost|127\.0\.0\.1)(:\d+)?`
- `allow_credentials: true`, 허용 메서드/헤더 전체
- 인증은 쿠키가 아니라 **`Authorization: Bearer` 헤더**다. `credentials:"include"` 는 필요 없다.
- 운영에서 프론트가 같은 오리진에서 서빙되면 상대경로 `/api/...` 로 호출, CORS 불필요.

---

## 1. 인증 — 단일 Bearer 토큰

모든 보호 엔드포인트는 `Authorization: Bearer <token>` 헤더 하나로 인증한다. 쿠키 세션 없음.
토큰 종류는 두 가지이며 백엔드가 값으로 자동 구분한다(`get_user`).

### (A) 검수자 — JWT  ← 프론트엔드가 쓰는 것

1. `POST /api/login` 에 `{id, password}` 전송 → `{token, user}` 수신 (JWT, 기본 8시간).
2. 토큰을 `localStorage('auth_token')` 에 저장하고 이후 모든 요청에 `Authorization: Bearer <token>` 첨부.
3. JWT payload: `sub`=admin_cd, `role`=`normal`|`master`, `iat`, `exp`.
4. 역할별 접근:
   - **master** (`role='master'`): 전국 데이터 접근.
   - **normal**: **본인 `adm_cd` 데이터만**. 타 adm_cd 요청 시 `403`.
   - 프론트 `user.role` 은 `master` / `user`(=normal) 로 매핑된다.

> 로그아웃은 클라이언트에서 `localStorage` 토큰 삭제로 처리한다(서버 세션 없음).
> 별도의 `/api/me` 엔드포인트는 없다 — 토큰 보유 여부로 로그인 상태를 판단한다.

### (B) 대전 QGIS 플러그인 — PLUGIN_TOKEN  ← 프론트엔드는 안 씀

`Authorization: Bearer <PLUGIN_TOKEN>` (서버 환경변수). 값이 일치하면 `role=plugin` 으로 전국 write 권한.
`PUT /api/boundary`, `POST /api/cog` 전용. 토큰 값은 여기 적지 않는다.

### 엔드포인트별 인증 요건

| 엔드포인트 | 인증 |
|---|---|
| `GET /api/health`, `POST /api/login` | 불필요 |
| `GET /api/admins`, `GET /api/admin_outline` | Bearer (검수자/플러그인 누구나) |
| `GET /api/boundary`, `GET /api/cog/{adm_cd}`, `GET /api/markup` | Bearer + adm_cd 권한 체크 |
| `POST /api/markup`, `PATCH /api/markup/{id}/apply`, `PATCH /api/markup/{id}/reject`, `DELETE /api/markup/{id}` | Bearer + adm_cd 권한 체크 |
| `PUT /api/boundary`, `POST /api/cog` | **PLUGIN_TOKEN 전용** |

- 토큰 없음/스킴 불일치/만료/무효 → `401 {"detail":"..."}`
- normal 이 본인 외 adm_cd 접근 → `403 {"detail":"본인 adm_cd 외 접근 불가"}`
- plugin 전용에 비-plugin 토큰 → `403 {"detail":"플러그인 권한이 필요합니다"}`

---

## 2. 좌표계 (CRS)

| 구간 | EPSG | 비고 |
|---|---|---|
| DB 내부 저장 | **5179** | Korea 2000 / Unified CS |
| **API GeoJSON 입출력** | **4326** | `/api/boundary`·`/api/markup`·`/api/admin_outline` geometry 전부 경위도(lon,lat) |
| titiler 타일 | **3857** | WebMercator (`WebMercatorQuad`) |

> **OpenLayers**: 뷰는 `EPSG:3857`. GeoJSON 은 `EPSG:4326` 이므로
> `new GeoJSON({ dataProjection:"EPSG:4326", featureProjection:"EPSG:3857" })` 로 읽는다.
> 백엔드가 5179↔4326 변환을 모두 처리하므로 **프론트는 4326 만 알면 된다.**

---

## 3. 엔드포인트

`$BASE = https://gis-hq.tail3b9b19.ts.net`.

### 3.1 `POST /api/login`  (인증 불필요)

```bash
curl -X POST $BASE/api/login -H 'Content-Type: application/json' \
  -d '{"id":"21510110","password":"<발급된 비번>"}'
```
응답 `200`:
```json
{
  "token": "eyJhbGciOiJIUzI1NiJ9....",
  "user": { "id": "21510110", "role": "user", "adm_cd": "21510110", "adm_nm": "기장읍" }
}
```
- master 계정이면 `user = {"id":"00000000","role":"master"}` (adm_cd/adm_nm 없음).
- 실패: `401 {"detail":"ID 또는 비밀번호가 올바르지 않습니다"}`.
- 부수효과: `login_log` 에 ip/user_agent 기록(성공·실패 모두). ip 는 `X-Forwarded-For` 우선.

### 3.2 `GET /api/health`  (인증 불필요)

```json
{"status": "ok", "db": "ok"}
```
DB 장애 시 `503 {"status":"degraded","db":"<error>"}`.

---

### 3.3 `GET /api/admins`  — 검수 대상 목록

`admin_node ⨯ cog_catalog` INNER JOIN — **COG 가 업로드된 admin 만** 노출(picker 용).

```bash
curl -H "Authorization: Bearer $TOKEN" $BASE/api/admins
```

**응답 — 최상위가 순수 배열** (`AdminUnit[]`):
```json
[
  { "adm_cd": "32510110", "adm_nm": "...",
    "sigungu_cd": "32510", "sigungu_nm": "...",
    "sido_cd": "32", "sido_nm": "..." }
]
```
빈 DB: `[]`.

| 필드 | 타입 | 비고 |
|---|---|---|
| `adm_cd` | string(8) | 행정읍면 코드. 다른 엔드포인트의 키 |
| `adm_nm` | string | 행정읍면명 (`admin_node` 기준) |
| `sigungu_cd`/`sigungu_nm` | string | 시군구 (`sgg_*` 별칭) |
| `sido_cd`/`sido_nm` | string | 시도 |

> ⚠️ 최상위가 **배열**이다(`{"admins":[...]}` 아님). 정렬: sido_cd → sgg_cd → adm_cd.
> s3_key/published_at/bounds 등 COG 메타가 필요하면 `GET /api/cog/{adm_cd}` 를 따로 호출.

---

### 3.4 `GET /api/admin_outline`  — 행정읍면 외곽 폴리곤

`admin_outline`(=`bnd_adm_pg.shp` 적재본) 의 GeoJSON FeatureCollection(EPSG:4326).
**`adm_cd` 또는 `bbox` 중 하나 필수** (전국 일괄 로딩 차단).

| 쿼리 | 의미 |
|---|---|
| `adm_cd=<8자>` | 해당 읍면 + `buffer_m`(기본 1000m, EPSG:5179) 거리 내 이웃 폴리곤 |
| `buffer_m=<0~50000>` | adm_cd 와 함께. 이웃 포함 반경 |
| `bbox=minx,miny,maxx,maxy` | EPSG:4326 bbox 와 교차하는 폴리곤 |

```bash
curl -H "Authorization: Bearer $TOKEN" "$BASE/api/admin_outline?adm_cd=21510110&buffer_m=1000"
```
각 feature `properties`: `adm_cd, adm_nm, sgg_cd, sgg_nm, sido_cd, sido_nm, is_target`
(`is_target` = 요청한 adm_cd 본인 폴리곤이면 `true`).
둘 다 미지정/형식오류 → `400`, adm_cd 8자 아님 → `400`.

---

### 3.5 `GET /api/boundary?adm_cd=<8자>`  — 행정리 경계

```bash
curl -H "Authorization: Bearer $TOKEN" "$BASE/api/boundary?adm_cd=21510110"
```
항상 GeoJSON `FeatureCollection`(EPSG:4326). 데이터 없으면 `{"type":"FeatureCollection","features":[]}`.

```json
{
  "type": "FeatureCollection",
  "features": [{
    "type": "Feature", "id": 1,
    "geometry": { "type": "MultiPolygon", "coordinates": [[[[127.5,37.09],[...]]]] },
    "properties": {
      "gid": 1, "adm_cd": "21510110", "adm_nm": "기장읍",
      "ri_cd": "9999999901", "ri_nm": "샘플리", "status": "draft",
      "updated_at": "2026-05-14T09:04:38.582203+00:00", "updated_by": "daejeon"
    }
  }]
}
```
- geometry 는 `MultiPolygon`, 좌표 `EPSG:4326`.
- `adm_cd` 필수(없으면 `422`). normal 이 본인 외 adm_cd → `403`.

### 3.5b `PUT /api/boundary?srid=4326`  (PLUGIN_TOKEN 전용)

플러그인이 GeoJSON FC 를 `boundary` 테이블에 upsert(`adm_cd`+`ri_cd` 키).

- `srid` 쿼리: `4326`(기본) 또는 `5179`. 그 외 `400`.
- body: GeoJSON FeatureCollection. 각 feature `properties.adm_cd` 필수.
- 인식 properties: `adm_cd, ri_cd, adm_nm, ri_nm, status`(미지정 시 신규는 `draft`), `updated_by`(미지정 시 `daejeon`).
- 응답: `{"affected":N,"inserted":I,"updated":U,"features":F}`.

---

### 3.6 `GET /api/cog/{adm_cd}`  — COG 배경 타일 정보

```bash
curl -H "Authorization: Bearer $TOKEN" $BASE/api/cog/32510110
```
**응답 `200`** (`CogInfo`):
```json
{
  "adm_cd": "32510110",
  "s3_key": "cog/32/32510/32510110.tif",
  "s3_url": "s3://gis-scan/cog/32/32510/32510110.tif",
  "width": 12552, "height": 16206,
  "published_at": "2026-05-14T08:03:40.137979+00:00",
  "bounds_geojson": { "type": "Polygon", "coordinates": [[...]] },
  "bbox": [127.791913, 37.580001, 127.969877, 37.762258],
  "tile_url": "/tiles/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=s3%3A%2F%2Fgis-scan%2F...",
  "tilejson_url": "/tiles/cog/WebMercatorQuad/tilejson.json?url=s3%3A%2F%2Fgis-scan%2F..."
}
```
COG 없으면 `404 {"detail":"해당 admin의 COG가 아직 없습니다"}`. adm_cd 8자 아님 → `400`. normal 본인 외 → `403`.

#### tile_url 사용 주의
- `tile_url`/`tilejson_url` 은 **상대경로**(`/tiles/...`). OpenLayers XYZ 소스엔 `BASE + tile_url`.
  운영 same-origin 이면 그대로 써도 됨. `{z}/{x}/{y}` 는 OL 이 치환.
- `.png` 확장자 + `url` 쿼리 파라미터 필수(이미 인코딩돼 박혀 있음).
- titiler 가 자체 생성하는 tilejson 의 `tiles` URL 은 프록시 뒤라 `http://127.0.0.1/...` 로 잘못 나온다 →
  **`tile_url` 직접 사용 권장**. minzoom/maxzoom 만 tilejson 참고.
- `bbox` = `[minLon,minLat,maxLon,maxLat]`(4326). `map.getView().fit(transformExtent(bbox,"EPSG:4326","EPSG:3857"))`.

### 3.6b `POST /api/cog`  (PLUGIN_TOKEN 전용)

플러그인이 COG 를 S3 업로드 후 `cog_catalog` 등록(`adm_cd` PK upsert).
body: `{adm_cd, s3_key, bounds, width?, height?, srid?}`.
`bounds` 는 GeoJSON Polygon 또는 `[minx,miny,maxx,maxy]`. `srid` 기본 5179. 응답 `{"ok":true, ...}`.

---

### 3.7 `GET /api/markup?adm_cd=&status=`  — 수정요청 목록

```bash
curl -H "Authorization: Bearer $TOKEN" "$BASE/api/markup?adm_cd=21510110&status=pending"
```
- `adm_cd`(선택, 지정 시 권한 체크), `status`(선택: `pending`|`applied`|`rejected`, 그 외 `400`).
- 응답: GeoJSON `FeatureCollection`(EPSG:4326). 빈 상태 `features:[]`.

```json
{
  "type": "FeatureCollection",
  "features": [{
    "type": "Feature", "id": 7,
    "geometry": { "type": "Point", "coordinates": [129.2, 35.2] },
    "properties": {
      "id": 7, "kind": "attr", "attrs": { "ri_nm": "샘플리", "ri_cd": "9999999999" },
      "adm_cd": "21510110", "status": "pending", "reject_reason": null,
      "created_by": "21510110", "created_at": "2026-05-18T08:20:08.157+00:00",
      "applied_by": null, "applied_at": null, "rejected_by": null, "rejected_at": null
    }
  }]
}
```
- `kind`: `add`/`delete` → `LineString`, `attr`/`delete_mark` → `Point`.
- `status`: `pending`|`applied`|`rejected`. `created_by`=요청자(admin_cd), `applied_by`/`rejected_by`=처리한 관리자(admin_cd).

### 3.8 `POST /api/markup`  → `201`

body (`MarkupCreate`):
```json
{ "adm_cd":"21510110", "kind":"attr",
  "geometry":{"type":"Point","coordinates":[129.2,35.2]},
  "attrs":{"ri_nm":"샘플리","ri_cd":"9999999999"} }
```
- `kind`/`geometry.type` 짝: `add`·`delete`=`LineString`, `attr`=`Point`,
  `delete_mark`=`LineString`(경계 스냅 구간) 또는 `Point`(구버전). 불일치 → `400`.
  adm_cd 8자 아님 → `400`. 본인 외 adm_cd → `403`.
- `created_by` = 토큰 admin_cd(plugin 이면 `"plugin"`), `status` 기본 `pending`.
- 응답 `201 {"id": <int>}`.

### 3.9 `PATCH /api/markup/{id}/apply`  → `204`

해당 markup 을 `applied` 로(`applied_at=now()`, `applied_by`=토큰 admin_cd, `reject_reason`/`rejected_by` 초기화).
없는 id → `404`, 권한 없음 → `403`. 응답 본문 없음(`204`).

### 3.10 `PATCH /api/markup/{id}/reject`  → `204`

body: `{"reason":"겹침"}` (필수, 빈 값 `400`). `rejected` 로(`rejected_at=now()`, `rejected_by`, `applied_by=NULL`).
없는 id → `404`, 권한 없음 → `403`. 응답 본문 없음(`204`).

### 3.11 `DELETE /api/markup/{id}`  → `204`

수정요청 회수 — 작성자가 잘못 올린 요청을 완전히 삭제(행 제거). 웹 `라인삭제` 툴이
지도에서 마크업을 클릭해 호출. **대기(`pending`)·반려(`rejected`)** 는 삭제 가능
(실제 경계를 바꾼 적 없음). 이미 **반영(`applied`)** 된 요청만 이력 보존으로 `409`.
없는 id → `404`, 본인 adm_cd 외 → `403`(마스터/플러그인은 전체). 응답 본문 없음(`204`).

---

## 4. 타일 직접 호출 형식

```
GET $BASE/tiles/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=<URL-encoded s3:// 경로>
```
- `{z}/{x}/{y}` WebMercator(XYZ) / `.png` 필수 / `url` = COG `s3://gis-scan/...` URL 인코딩(= `tile_url` 에 박힘).
- 응답 `image/png` 256×256. nginx `/tiles/` → titiler 프록시(titiler 가 MinIO COG 직접 읽음). `Cache-Control: 1h`.

---

## 5. 배포 워크플로

### 5.1 nginx 라우팅 (`web/conf/default.conf`)

| location | 대상 | 비고 |
|---|---|---|
| `/` | 정적 SPA (`web/dist`) | `try_files ... /index.html` (SPA 라우팅) |
| `/api/` | backend(FastAPI) | 검수 API |
| `/web/` | backend | nginx 에 프록시 정의는 있으나 **backend 에 `/web/*` 라우트 없음(미사용/사장)** |
| `/s3/` | minio | 플러그인 boto3 업로드(path-style). `/s3` prefix 떼고 전달 |
| `/vworld/` | api.vworld.kr | WMTS 베이스맵 프록시(키는 `VITE_VWORLD_KEY`) |
| `/tiles/` | titiler | COG 타일 |

gzip on(`application/json|javascript|geo+json` 등), `client_max_body_size 5m`(`/s3/` 는 무제한).

### 5.2 프론트 빌드 & 배포

프론트는 `web/`(React+Vite). web 컨테이너(`nginx:1.27`)가 `web/dist` 를 `:ro` 마운트한다.

```bash
# 빌드 (docker-compose.yml 주석에 동일 명령)
cd /srv/gis-src/QGIS_Plugin/web
docker run --rm -v $PWD:/app -w /app node:20-alpine sh -c 'npm install && npm run build'
```
- `web/dist` 가 갱신되면 즉시 반영(디렉터리 마운트). 정적 변경엔 nginx 리로드 불필요.
- nginx 설정(`web/conf/default.conf`) 변경 시에만:
  ```bash
  docker exec gis-web-1 nginx -t && docker exec gis-web-1 nginx -s reload
  ```
- 컨테이너 재시작: `cd /srv/gis/compose && docker compose restart web`

### 5.3 로컬 개발

`npm run dev`(localhost:5173 등)로 띄우고 API 는 공개 URL `$BASE/api` 직접 호출(CORS 허용).
API 베이스 URL 은 env(`VITE_*`)로 로컬/운영 분기. 운영 same-origin 은 상대경로 `/api` 로 동작.

---

## 6. 빠른 점검용 curl

```bash
BASE=https://gis-hq.tail3b9b19.ts.net

# 로그인 → JWT 추출
TOK=$(curl -s -X POST $BASE/api/login -H 'Content-Type: application/json' \
        -d '{"id":"21510110","password":"<비번>"}' | jq -r .token)

curl -s $BASE/api/health
curl -s -H "Authorization: Bearer $TOK" $BASE/api/admins | jq 'length'
curl -s -H "Authorization: Bearer $TOK" "$BASE/api/admin_outline?adm_cd=21510110" | jq '.features|length'
curl -s -H "Authorization: Bearer $TOK" "$BASE/api/boundary?adm_cd=21510110" | jq '.features|length'
curl -s -H "Authorization: Bearer $TOK" $BASE/api/cog/21510110 | jq '.tile_url'
curl -s -H "Authorization: Bearer $TOK" "$BASE/api/markup?adm_cd=21510110&status=pending" | jq '.features|length'

# 마크업 등록 → 반영/반려
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"adm_cd":"21510110","kind":"attr","geometry":{"type":"Point","coordinates":[129.2,35.2]},"attrs":{"ri_nm":"샘플리"}}' \
  $BASE/api/markup
curl -s -X PATCH -H "Authorization: Bearer $TOK" $BASE/api/markup/7/apply -o /dev/null -w '%{http_code}\n'
curl -s -X PATCH -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"reason":"겹침"}' $BASE/api/markup/7/reject -o /dev/null -w '%{http_code}\n'
```

---

## 부록 — 백엔드/인프라 파일 위치

| | |
|---|---|
| docker compose | `server/compose/docker-compose.yml` (`/srv/gis/compose/...` 로 배포) |
| backend 소스 | `server/backend/app/main.py` (FastAPI, 단일 파일) |
| 프론트 소스 | `web/` (React+Vite+OpenLayers). API: `web/src/api/*.ts`, 타입: `web/src/types/index.ts` |
| nginx 설정 | `server/web/conf/default.conf` |
| 프론트 정적 루트(배포) | `web/dist` → nginx `:ro` 마운트 |
| DB 초기 스키마 | `server/compose/init.sql` (boundary / cog_catalog / review_markup, EPSG:5179) |
| 마이그레이션 | `server/migrations/*.sql` (auth, login_log, admin_node, admin_outline, review_markup, kind=delete_mark, markup processed_by 등) |
| 계정 시드 | `server/scripts/seed_web_accounts.py`, `seed_all_users.py` / SHP 적재 `load_admin_shp.sh` |
| 계정 자격증명 | `server/compose/.web-credentials.txt` (chmod 600) |

이 문서는 `backend/app/main.py` 의 실제 라우트 기준이며, API 변경 시 함께 갱신한다.
