# 검수 웹 API 레퍼런스 (프론트엔드 개발자용)

> **2026-05-18 갱신 — Phase 1 검수 웹 도입.** 검수자 측 API 가 `/api/*` (공유 비밀번호 쿠키) →
> `/web/*` (admin_cd 계정 + JWT) 로 이관됨. 마크업 스키마 변경(`kind` add/delete/attr,
> `status` pending/applied/rejected, `attrs` JSONB). `POST /api/markup`, `PATCH /api/markup/{id}` 는
> 제거. **신규 프론트엔드는 §7 `/web/*` 만 사용**. `/api/*` 의 cog/boundary 조회·플러그인 업로드는 그대로.

행정리 경계 검수 웹의 백엔드 API 계약서. 프론트엔드는 OpenLayers로 새로 개발하며,
이 문서의 **"실제 응답"을 기준**으로 맞춘다. (Phase 1 검증일: 2026-05-18)

- **API 베이스 URL**: `https://gis-hq.tail3b9b19.ts.net/api`
- **타일 베이스 URL**: `https://gis-hq.tail3b9b19.ts.net/tiles/...`
- 백엔드: FastAPI / 모든 응답 `Content-Type: application/json` (타일 제외)
- 서버 인프라: nginx(리버스 프록시) → backend(FastAPI) / titiler / minio. 외부 노출은 nginx 하나.

---

## 0. CORS (로컬 개발)

backend에 CORS 미들웨어가 적용돼 있다. **로컬 개발 PC에서 공개 API를 직접 호출 가능**.

- 허용 Origin: `http://localhost:<any-port>`, `http://127.0.0.1:<any-port>`, https 동일
  (정규식 `https?://(localhost|127\.0\.0\.1)(:\d+)?`)
- `allow_credentials: true` — 쿠키 인증 엔드포인트도 로컬에서 호출 가능
- 허용 메서드/헤더: 전체

> fetch 시 쿠키를 주고받으려면 **`credentials: "include"`** 필수.
> ```js
> fetch("https://gis-hq.tail3b9b19.ts.net/api/admins", { credentials: "include" })
> ```
> 운영 단계에서 허용 Origin 축소 예정 (현재는 개발 편의로 넓게 열어둠).

---

## 1. 인증

API에는 **두 가지 인증 주체**가 있다. 프론트엔드는 **(A) 발주자 쿠키 인증만** 사용한다.

### (A) 발주자 — 쿠키 세션  ← 프론트엔드가 쓰는 것

1. `POST /api/login` 에 공유 비밀번호를 보내면 `Set-Cookie: gis_session=...` (HttpOnly, SameSite=Lax, 7일) 발급
2. 이후 모든 요청에 이 쿠키가 자동 동봉됨 (`credentials: "include"`)
3. `GET /api/me` 로 현재 로그인 여부 확인 가능
4. `POST /api/logout` 으로 쿠키 삭제

쿠키는 HttpOnly라 JS에서 읽을 수 없다. 로그인 상태 판단은 `/api/me` 로 한다.

### (B) 대전 QGIS 플러그인 — Bearer 토큰  ← 프론트엔드는 안 씀

`Authorization: Bearer <PLUGIN_TOKEN>` 헤더. `PUT /api/boundary`, `POST /api/cog` 전용.
프론트엔드와 무관하므로 토큰 값은 여기 적지 않는다.

### 엔드포인트별 인증 요건

| 엔드포인트 | 인증 |
|---|---|
| `POST /api/login`, `POST /api/logout`, `GET /api/me`, `GET /api/health` | 불필요 |
| `GET /api/admins`, `GET /api/boundary`, `GET /api/cog/{adm_cd}`, `GET /api/markup` | 쿠키 **또는** Bearer |
| `POST /api/markup`, `PATCH /api/markup/{id}` | **쿠키 전용** (발주자) |
| `PUT /api/boundary`, `POST /api/cog` | Bearer 전용 (플러그인) |

인증 실패 시 `401 {"detail": "..."}`.

---

## 2. 좌표계 (CRS)

| 구간 | EPSG | 비고 |
|---|---|---|
| DB 내부 저장 | **5179** | Korea 2000 / Unified CS |
| **API GeoJSON 입출력** | **4326** | `/api/boundary`, `/api/markup` 의 geometry는 전부 경위도(lon, lat) |
| titiler 타일 | **3857** | WebMercator (`WebMercatorQuad`) |

> **OpenLayers 설정**: 뷰는 보통 `EPSG:3857`. `/api/boundary`·`/api/markup` GeoJSON은
> `EPSG:4326`이므로 `new GeoJSON({ dataProjection: "EPSG:4326", featureProjection: "EPSG:3857" })`
> 로 읽어야 한다. 타일(XYZ)은 3857이라 뷰와 그대로 일치.
> 백엔드가 5179↔4326 변환을 모두 처리하므로 **프론트는 4326만 알면 된다.**

---

## 3. 엔드포인트

아래 예시는 모두 **실제 서버 응답**(2026-05-14 기준). `$BASE = https://gis-hq.tail3b9b19.ts.net`.

### 3.1 `POST /api/login`

```bash
curl -c cookie.txt -X POST $BASE/api/login \
  -H 'Content-Type: application/json' \
  -d '{"password": "<발주자 공유 비밀번호>"}'
```
응답 `200`:
```json
{"ok": true}
```
실패 `401`: `{"detail": "비밀번호가 올바르지 않습니다"}`
응답 헤더에 `Set-Cookie: gis_session=...` 포함.

### 3.2 `GET /api/me`
```bash
curl -b cookie.txt $BASE/api/me
```
```json
{"authenticated": true}
```
(비로그인 시 `{"authenticated": false}` — 이 엔드포인트는 401을 내지 않음)

### 3.3 `POST /api/logout`
```json
{"ok": true}
```
→ 쿠키 삭제됨.

### 3.4 `GET /api/health` (인증 불필요)
```json
{"status": "ok", "db": "ok"}
```

---

### 3.5 `GET /api/admins`  — 검수 대상 목록

cog_catalog(= COG가 업로드된 admin)를 기준으로 한 목록.

```bash
curl -b cookie.txt $BASE/api/admins
```

**응답 — 데이터 있을 때** (실제):
```json
{
  "admins": [
    {
      "adm_cd": "32510110",
      "adm_nm": null,
      "s3_key": "cog/32/32510/32510110.tif",
      "width": 12552,
      "height": 16206,
      "published_at": "2026-05-14T08:03:40.137979+00:00",
      "bounds_geojson": {
        "type": "Polygon",
        "coordinates": [[[127.791913802,37.580571651],[127.792626372,37.762258227],
                         [127.969877107,37.761684813],[127.968732948,37.58000196],
                         [127.791913802,37.580571651]]]
      },
      "open_markups": 0
    },
    { "adm_cd": "32510330", "adm_nm": null, "s3_key": "cog/32/32510/32510330.tif",
      "width": 18759, "height": 24347, "published_at": "...", "bounds_geojson": {...},
      "open_markups": 0 }
  ]
}
```

**응답 — 빈 DB일 때**:
```json
{"admins": []}
```

| 필드 | 타입 | 비고 |
|---|---|---|
| `adm_cd` | string(8) | 행정동 코드. 다른 엔드포인트의 키 |
| `adm_nm` | string \| **null** | 행정동명. **boundary 데이터가 아직 없으면 `null`** |
| `s3_key` | string | MinIO 객체 키 |
| `width`,`height` | int \| null | COG 픽셀 크기 |
| `published_at` | string(ISO8601) | COG 등록 시각 |
| `bounds_geojson` | GeoJSON Polygon (4326) | COG 영역. 목록에서 줌 처리에 사용 |
| `open_markups` | int | 해당 admin의 `status=open` 마크업 개수 (배지 표시용) |

> ⚠️ 최상위가 **배열이 아니라 `{"admins": [...]}` 객체**다.
> ⚠️ `adm_nm`이 `null`일 수 있다 — UI에서 `adm_nm ?? adm_cd` 식으로 폴백 권장.

---

### 3.6 `GET /api/cog/{adm_cd}`  — COG 배경 타일 정보

```bash
curl -b cookie.txt $BASE/api/cog/32510110
```

**응답 `200`** (실제):
```json
{
  "adm_cd": "32510110",
  "s3_key": "cog/32/32510/32510110.tif",
  "s3_url": "s3://gis-scan/cog/32/32510/32510110.tif",
  "width": 12552,
  "height": 16206,
  "published_at": "2026-05-14T08:03:40.137979+00:00",
  "bounds_geojson": { "type": "Polygon", "coordinates": [[...]] },
  "bbox": [127.79191380243101, 37.58000196008204,
           127.96987710708464, 37.762258226832216],
  "tile_url": "/tiles/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=s3%3A%2F%2Fgis-scan%2Fcog%2F32%2F32510%2F32510110.tif",
  "tilejson_url": "/tiles/cog/WebMercatorQuad/tilejson.json?url=s3%3A%2F%2Fgis-scan%2Fcog%2F32%2F32510%2F32510110.tif"
}
```

**해당 adm_cd에 COG가 없으면 `404`**: `{"detail": "해당 admin의 COG가 아직 없습니다"}`

#### ⭐ 스켈레톤 가정 vs 실제 — 중요

| 스켈레톤 가정 | 실제 |
|---|---|
| `tile_url` 키가 있다 | ✅ **있다.** 키명 `tile_url` 그대로 |
| `/tiles/{z}/{x}/{y}` 형식 | ❌ 실제는 `/tiles/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=<encoded>` — **`.png` 확장자 + `url` 쿼리 파라미터 필수** |

- `tile_url`은 **상대경로**다. OpenLayers XYZ 소스에 쓸 때 **베이스 URL을 앞에 붙인다**:
  ```js
  const cog = await (await fetch(`${BASE}/api/cog/${admCd}`, {credentials:"include"})).json();
  new ol.layer.Tile({
    source: new ol.source.XYZ({
      url: BASE + cog.tile_url,   // {z}/{x}/{y} 는 OpenLayers가 치환
      // titiler 타일은 512px 아닌 256px(.png 경로). bbox 로 extent 제한 권장
    })
  });
  map.getView().fit(ol.proj.transformExtent(cog.bbox, "EPSG:4326", "EPSG:3857"));
  ```
- `bbox`는 `[minX,minY,maxX,maxY]` (4326). 줌/익스텐트 제한용.
- `tilejson_url`도 제공하지만 **titiler가 자체 생성하는 tilejson의 `tiles` URL은
  `http://127.0.0.1/...` 로 잘못 나온다**(프록시 뒤라서). **`tile_url`을 직접 쓰는 것을 권장.**
  타일의 minzoom/maxzoom(현재 데이터 기준 11~17)이 필요하면 tilejson에서 그 값만 참고.

---

### 3.7 `GET /api/boundary?adm_cd=<code>`  — 경계 GeoJSON

```bash
curl -b cookie.txt "$BASE/api/boundary?adm_cd=32510110"
```

**응답** — 항상 GeoJSON `FeatureCollection` (EPSG:4326). 데이터 없으면 `features: []`.

데이터 있을 때 (shape 예시):
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": 1,
      "geometry": { "type": "MultiPolygon", "coordinates": [[[[127.5,37.0986],[...]]]] },
      "properties": {
        "gid": 1,
        "adm_cd": "32510110",
        "adm_nm": "샘플동",
        "ri_cd": "9999999901",
        "ri_nm": "샘플리",
        "status": "draft",
        "updated_at": "2026-05-14T09:04:38.582203+00:00",
        "updated_by": "daejeon-qgis"
      }
    }
  ]
}
```
빈 DB:
```json
{"type": "FeatureCollection", "features": []}
```

- geometry는 항상 `MultiPolygon`, 좌표 `EPSG:4326`.
- `adm_cd` 쿼리 파라미터 필수 (없으면 422).
- properties: `gid`(PK), `adm_cd`, `adm_nm`, `ri_cd`, `ri_nm`, `status`, `updated_at`, `updated_by`.

---

### 3.8 `GET /api/markup?adm_cd=<code>`  — 마크업 GeoJSON  (플러그인 회수용)

```bash
curl -H "Authorization: Bearer $PLUGIN_TOKEN" "$BASE/api/markup?adm_cd=21510110"
```
`adm_cd` 파라미터는 **선택** (생략 시 전체 마크업). 신규 스키마 기준 — 대전 플러그인 회수용으로
유지됨. 검수자 측에서는 §7 의 `/web/markup` 사용.

**응답** — GeoJSON `FeatureCollection` (EPSG:4326):
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": 1,
      "geometry": { "type": "Point", "coordinates": [129.20, 35.20] },
      "properties": {
        "id": 1,
        "kind": "attr",
        "attrs": { "ri_nm": "샘플리", "ri_cd": "9999999999" },
        "adm_cd": "21510110",
        "status": "applied",
        "reject_reason": null,
        "created_by": "21510110",
        "created_at": "2026-05-18T08:20:08.157+00:00",
        "applied_at": "2026-05-18T08:20:08.479+00:00",
        "rejected_at": null
      }
    }
  ]
}
```
빈 상태: `{"type": "FeatureCollection", "features": []}`

- geometry 타입은 `kind` 에 따라 `LineString`(add/delete) / `Point`(attr).
- `kind`: `"add"` | `"delete"` | `"attr"`. `status`: `"pending"` | `"applied"` | `"rejected"`.

---

### 3.9 ~~POST /api/markup~~  /  3.10 ~~PATCH /api/markup/{id}~~ — **제거됨 (2026-05-18)**

검수자 측 마크업 CRUD 는 §7 `/web/markup` 으로 이관. 플러그인이 마크업을 직접 수정해야 할
필요가 생기면 신규 스키마 기반으로 다시 신설 예정 (별도 Phase).

---

## 4. 타일 직접 호출 형식

`/api/cog/{adm_cd}` 의 `tile_url`을 그대로 쓰면 되지만, 형식을 명시하면:

```
GET https://gis-hq.tail3b9b19.ts.net/tiles/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=<URL-encoded s3:// 경로>
```
- `{z}/{x}/{y}` : WebMercator(XYZ) 타일 좌표
- `.png` 확장자 필수
- `url` 쿼리 파라미터 필수 — COG의 `s3://gis-scan/...` 경로를 URL 인코딩한 값
  (이 값은 `tile_url`에 이미 박혀 있으므로 프론트가 직접 만들 필요 없음)
- 응답: `image/png`, 256×256
- nginx `/tiles/` → titiler 프록시. titiler가 MinIO에서 COG를 직접 읽어 타일로 변환.

검증됨: `z=11` 타일 `200 image/png 113KB` 정상 렌더.

---

## 5. 배포 워크플로

### 5.1 서버 디렉터리 구조 (web 관련)

```
/srv/gis/web/
├── html/                ← nginx 가 / 로 서빙하는 정적 파일 루트
│   ├── index.html
│   ├── app.js           (현재: 기존 MapLibre 프론트 — OpenLayers 빌드물로 교체 예정)
│   └── style.css
└── conf/
    └── default.conf     ← nginx 설정 (라우팅: / /api/ /tiles/ /s3/)
```
- `web` 컨테이너(`nginx:1.27`)에 두 경로가 디렉터리 마운트돼 있다:
  - `/srv/gis/web/html` → `/usr/share/nginx/html` (정적 루트)
  - `/srv/gis/web/conf` → `/etc/nginx/conf.d`
- nginx 라우팅: `/` = 정적 파일(`try_files $uri $uri/ /index.html` — SPA 라우팅 지원),
  `/api/` → backend, `/tiles/` → titiler, `/s3/` → minio.

### 5.2 프론트 정적파일 반영 방법

OpenLayers 앱을 **로컬에서 빌드**한 뒤 결과물(`dist/`)을 `/srv/gis/web/html/` 에 넣는다.

```bash
# 로컬 PC 에서 빌드 후, 서버로 전송 (서버는 Tailscale 망에 있음)
rsync -av --delete ./dist/  root@100.106.19.100:/srv/gis/web/html/
#  또는  scp -r ./dist/* root@100.106.19.100:/srv/gis/web/html/
```

- **정적 파일 내용 변경만**이면 nginx 재시작/리로드 **불필요** — 디렉터리 마운트라 즉시 반영.
  브라우저 새로고침이면 끝.
- **nginx 설정(`conf/default.conf`)을 바꾼 경우에만** 리로드:
  ```bash
  docker exec gis-web-1 nginx -t          # 문법 검사
  docker exec gis-web-1 nginx -s reload   # 무중단 리로드
  ```
- 컨테이너 자체 재시작이 필요한 경우: `cd /srv/gis/compose && docker compose restart web`

### 5.3 권장 개발 → 배포 절차

1. **로컬 개발**: OpenLayers 앱을 로컬에서 띄우고(`localhost:5173` 등), API는 공개 URL
   `https://gis-hq.tail3b9b19.ts.net/api` 를 직접 호출 (CORS 허용됨, `credentials:"include"`).
2. **빌드**: 번들러로 정적 파일 산출(`dist/`). API 베이스 URL은 환경변수로 빼서
   로컬/운영 분기 권장 (운영은 같은 오리진이라 상대경로 `/api` 로도 동작).
3. **배포**: `dist/` 를 `/srv/gis/web/html/` 로 rsync. 기존 `index.html/app.js/style.css` 는
   덮어쓰면 됨 (교체 대상). `index.html` 이 루트에 있어야 `/` 로 서빙됨.
4. **확인**: `https://gis-hq.tail3b9b19.ts.net/` 새로고침.
5. 롤백이 필요하면 이전 `dist/` 를 다시 rsync. (원하면 `/srv/gis/web/releases/` 식으로
   버전 디렉터리를 두고 심볼릭 링크 전환하는 방식도 가능 — 현재는 단순 덮어쓰기.)

> 운영에서 프론트가 같은 오리진(`https://gis-hq.tail3b9b19.ts.net`)에서 서빙되면
> API 호출은 상대경로 `/api/...` 로 가능하고 CORS도 필요 없다. CORS는 **로컬 개발용**.

---

## 6. 빠른 점검용 curl 모음

```bash
BASE=https://gis-hq.tail3b9b19.ts.net

# 로그인 → 쿠키 저장
curl -c cookie.txt -X POST $BASE/api/login -H 'Content-Type: application/json' \
  -d '{"password":"<발주자 비밀번호>"}'

curl -b cookie.txt $BASE/api/me
curl -b cookie.txt $BASE/api/admins
curl -b cookie.txt "$BASE/api/boundary?adm_cd=32510110"
curl -b cookie.txt $BASE/api/cog/32510110
curl -b cookie.txt "$BASE/api/markup?adm_cd=32510110"

# 마크업 생성/수정
curl -b cookie.txt -X POST $BASE/api/markup -H 'Content-Type: application/json' \
  -d '{"geom":{"type":"Point","coordinates":[127.8,37.6]},"kind":"pin","comment":"확인","target_adm_cd":"32510110"}'
# 신규 검수자 흐름은 §7 참고.
```

---

## 7. `/web/*` — 검수자 웹 (admin_cd 계정 + JWT)  **Phase 1**

검수자(읍면 담당자/마스터) 로그인 후 사용하는 API. **OpenLayers 프론트는 이 섹션만 사용.**

### 7.0 인증 모델

- 계정 단위: `admin_cd` (8자 행정읍면 코드). `auth` 테이블에 bcrypt 해시 저장.
- 마스터: `admin_cd='00000000'`, `role='master'`. 전국 데이터 접근.
- 일반(normal): `role='normal'`. **자기 admin_cd 의 데이터만** 접근.
- 토큰: `POST /web/login` 으로 JWT 발급(기본 8시간). 이후 모든 `/web/*` 호출에
  `Authorization: Bearer <token>` 헤더 첨부.
- 잘못된 토큰/만료 → `401`. 권한 불일치 → `403`.

```js
const BASE = "https://gis-hq.tail3b9b19.ts.net";
const token = localStorage.getItem("jwt");
fetch(`${BASE}/web/boundary?adm_cd=21510110`, {
  headers: { "Authorization": "Bearer " + token },
});
```

### 7.1 `POST /web/login`

요청:
```bash
curl -X POST $BASE/web/login -H 'Content-Type: application/json' \
  -d '{"admin_cd":"21510110","password":"<발급된 비번>"}'
```
응답 `200`:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiJ9....",
  "role": "normal",
  "admin_cd": "21510110"
}
```
실패: `401 {"detail":"admin_cd 또는 비밀번호가 올바르지 않습니다"}`.
부수효과: `login_log` 에 ip/user_agent/ts 기록 (성공·실패 모두).

### 7.2 `GET /web/admin_tree`  (master 전용)

응답:
```json
{ "sido": [
    { "sido_cd": "21", "sido_nm": "부산광역시",
      "sgg": [
        { "sgg_cd": "21510", "sgg_nm": "기장군",
          "adm": [
            { "adm_cd": "21510110", "adm_nm": "기장읍" },
            { "adm_cd": "21510111", "adm_nm": "일광읍" }
          ]
        }
      ]
    }
] }
```
normal 토큰으로 호출 시 `403`. admin_node 가 비어있으면 `{"sido": []}`.

### 7.3 `GET /web/admin_line?adm_cd=<8자>`

읍면 외곽 폴리곤 GeoJSON FeatureCollection (EPSG:4326). 데이터 소스는 `admin_outline` 테이블
(행정경계 SHP 적재 예정). **현재는 SHP 미적재 상태이므로 `501` 반환.** 외주 프론트는 일단
boundary 만 렌더링하고 SHP 도착 후 활성화 예정.

### 7.4 `GET /web/boundary?adm_cd=<8자>`

해당 읍면의 행정리 폴리곤들. EPSG:4326. `Cache-Control: max-age=5`.
properties: `gid, adm_cd, adm_nm, ri_cd, ri_nm, status, updated_at`.

```bash
curl -H "Authorization: Bearer $TOKEN" "$BASE/web/boundary?adm_cd=21510110"
```
normal 토큰으로 본인 외 adm_cd 요청 → `403`.

### 7.4b `GET /web/cog/{adm_cd}`

해당 admin의 COG s3_key + titiler 타일 URL 템플릿. `/api/cog/{adm_cd}` 와 동일 응답 shape이지만
JWT + admin 접근 체크. 없는 admin → `404`. 응답에 `tile_url`(z/x/y 템플릿), `tilejson_url`,
`bbox`(WGS84, fitBounds 용), `bounds_geojson`, `width/height` 포함.

### 7.5 `GET /web/markup?adm_cd=&status=&page=&size=`

페이지네이션된 마크업 목록. status 미지정 시 전체(pending 우선, 그 후 created_at desc).

응답:
```json
{
  "total": 12, "page": 1, "size": 50,
  "items": [
    { "id": 7, "adm_cd": "21510110", "kind": "attr",
      "geometry": { "type": "Point", "coordinates": [129.2, 35.2] },
      "attrs": { "ri_nm": "샘플리" },
      "status": "pending", "reject_reason": null,
      "created_by": "21510110",
      "created_at": "2026-05-18T08:20:08.157+00:00",
      "applied_at": null, "rejected_at": null }
  ]
}
```

### 7.6 `POST /web/markup`

요청 body:
```json
{
  "adm_cd": "21510110",
  "kind": "attr",
  "geometry": { "type": "Point", "coordinates": [129.2, 35.2] },
  "attrs": { "ri_nm": "샘플리", "ri_cd": "9999999999" }
}
```
- `kind`/`geometry.type` 짝: `add`·`delete` → `LineString`, `attr` → `Point` (불일치 시 400)
- 응답 `201`: 생성된 markup full object (위 GET items 와 동일 shape)
- `created_by` 자동으로 JWT sub(admin_cd), `status` 기본 `pending`
- normal 이 본인 외 adm_cd 로 등록 시 `403`

### 7.7 `PATCH /web/markup/{id}`

요청 body:
```json
{ "status": "applied" }
```
또는
```json
{ "status": "rejected", "reject_reason": "겹침" }
```
- `applied` → `applied_at=now()`, `reject_reason=NULL`
- `rejected` → `rejected_at=now()`, `reject_reason` **필수** (없으면 400)
- normal 은 본인 adm_cd 의 markup 만 변경 가능 (`403`)
- 없는 id → `404`

응답 `200`: 갱신된 markup full object.

### 7.8 빠른 검증 curl

```bash
BASE=https://gis-hq.tail3b9b19.ts.net
TOK=$(curl -s -X POST $BASE/web/login -H 'Content-Type: application/json' \
        -d '{"admin_cd":"21510110","password":"<비번>"}' | jq -r .access_token)
curl -s -H "Authorization: Bearer $TOK" "$BASE/web/boundary?adm_cd=21510110" | jq '.features | length'
curl -s -H "Authorization: Bearer $TOK" "$BASE/web/markup?adm_cd=21510110" | jq '.total'
```

---

## 부록 — 백엔드/인프라 파일 위치 (참고)

| | |
|---|---|
| docker compose | `/srv/gis/compose/docker-compose.yml` |
| backend 소스 | `/srv/gis/backend/app/main.py`, `app/web.py` (FastAPI) |
| nginx 설정 | `/srv/gis/web/conf/default.conf` |
| 프론트 정적 루트 | `/srv/gis/web/html/` |
| DB 초기 스키마 | `/srv/gis/compose/init.sql` (boundary / cog_catalog / review_markup, EPSG:5179) |
| 마이그레이션 | `/srv/gis/migrations/202605_web_review.sql` (auth, login_log, admin_node, admin_outline, review_markup 재생성) |
| 계정 시드 스크립트 | `/srv/gis/scripts/seed_web_accounts.py` |
| 계정 자격증명 | `/srv/gis/compose/.web-credentials.txt` (chmod 600) |

문의/스키마 변경 요청은 서버측에. 이 문서는 실제 응답 검증 기준이며, API 변경 시 갱신된다.
