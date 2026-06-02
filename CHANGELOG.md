# Changelog

## 2026-06-02 — 플러그인 서버 주소 자동 마이그레이션 (구 Funnel → 정식 도메인)

작업자 PC 의 QSettings 에 구 Tailscale Funnel 주소가 저장돼 있으면 연결 테스트가
계속 구 주소로 나가던 문제. `load_config()` 가 구 주소(`LEGACY_BASE_URLS`)를
감지하면 `https://www.kosisgis.kr` 로 자동 치환 후 재저장 — 작업자 수동 조치 불필요.
서버 URL 입력칸 placeholder 도 정식 도메인으로 교체.

## 2026-06-02 — 행정리 목록: 기본 "비고 있음"만 표시 + [전체] 토글

행정리 목록 패널이 열릴 때 비고(remark)가 있는 행정리만 기본 표시 — 작업자
확인이 필요한 항목에 집중. 상단 [비고 있음 (N)] / [전체 (M)] 토글로 전환.

- `BoundaryListPanel` — `showAll` 상태(기본 false) + 비고 필터, 토글 버튼 2개,
  제목 카운트 모드 연동.
- 비고 있는 행정리가 0건이면 안내 문구 + [전체 행정리 보기] 버튼 표시.
- 검색은 현재 모드(비고/전체) 안에서 동작.
- 검증: 웹 `tsc -b` OK.

## 2026-06-02 — 빈 부호(ri_cd) 가짜 채움 제거: 경계 저장을 읍면 단위 전체 교체로

QGIS 에서 부호가 빈 폴리곤을 제출하면 웹에서 '001','002' 같은 부호가 채워져
보이던 문제. 원인은 플러그인 제출 직전의 `ensure_unique_ri_cd()` 자동부여 —
서버 upsert 키 (adm_cd, ri_cd) 충돌(빈 부호 여러 개 → 한 행으로 뭉개짐)을 막기
위한 우회책이었으나, 가짜 부호가 진짜처럼 저장되는 부작용.

- **서버** — `PUT /api/boundary` 를 upsert → **읍면 단위 전체 교체**(제출된
  adm_cd 의 기존 행 DELETE 후 INSERT)로 변경. 키 매칭이 없으므로 빈 부호
  폴리곤도 그대로 저장. *실제 부호* 중복만 400 거부.
  응답에 `deleted`/`admins` 추가, `updated` 제거.
- **플러그인** — `ensure_unique_ri_cd()` 함수·호출(작업 제출 + 완료 데이터
  업로드 2곳) 삭제. 빈 부호는 빈 채로 제출 → 웹에서 발주자가 속성등록 요청.
  기본 서버 주소를 정식 도메인 `https://www.kosisgis.kr` 로 교체
  (기존 작업자 PC 는 QSettings 저장값 우선 — 서버 연결 탭에서 직접 변경 필요).
  마크업 제거(165a429) 잔재 정리: `_MARKUP_KIND_STYLE`, UI 문구.
- **마이그레이션** — `202606_boundary_full_replace.sql`: 유니크 인덱스를
  부분 인덱스(실제 부호만 유일 강제, 빈 부호 다수 허용)로 교체. init.sql 동기.
- 검증: 서버/플러그인 `py_compile` OK.

## 2026-06-02 — 정식 도메인 https://www.kosisgis.kr 개통 (Caddy HTTPS 종단)

전국 단위 공개를 위해 Tailscale Funnel 대신 정식 도메인으로 접속하는 경로를 신설.
기존 compose 스택은 손대지 않고, 그 앞단에 HTTPS 종단 계층(Caddy)을 별도 컨테이너로 추가.

```
인터넷 → 공유기(443 포워딩) → 192.168.0.105:443 Caddy → 127.0.0.1:8080 기존 nginx
```

- **`server/caddy/` 신설** — Caddyfile + 운영 README. 운영 경로는 `/srv/gis/caddy/`.
- 인증서: Let's Encrypt 자동 발급/갱신. 80 포트가 다른 서비스(geoband)에 점유되어 있어
  **TLS-ALPN-01**(443만 사용) 챌린지로 발급 — `http://` 접속 불가, `https://` 필수.
- 443 은 tailscaled 와의 충돌을 피해 사내망 IP(192.168.0.105)에만 바인딩.
- 도메인: kosisgis.kr (가비아), A 레코드 www → 180.71.194.230 (SK브로드밴드 고정 IP).
- Tailscale Funnel(gis-hq.tail3b9b19.ts.net)은 당분간 병행 유지 — 같은 백엔드의 두 번째 출입구.
- 이번 세션에 DB 수정요청(markup) 데이터 전체 초기화도 수행 (운영 데이터 작업, 코드 변경 없음).

## 2026-06-01 — 웹 UX 개선 5종: 카드 하이라이트·attr 라벨·모달 드래그·GeoJSON 다운로드·겹침 플래시

- **카드 클릭 → 지도 하이라이트** — 수정요청 카드를 클릭하면 지도 이동과 함께
  해당 마크업(선/점)에 노란 강조 표시. 다른 카드 선택 시 강조가 옮겨감
  (`highlightId={deleteTargetId ?? selectedId}`).
- **속성등록 라벨 표시** — attr 마크업(파란 점) 위에 등록한 행정리명·부호를
  텍스트 라벨로 표시 (예: "수산3리 · 010"). 흰 외곽선으로 배경 위에서도 가독.
- **모달 드래그 이동** — 공통 Modal 에 제목줄 드래그 이동 추가. 저장 모달
  (라인등록/삭제표기/속성등록)은 `dim=false` — 배경을 어둡게 하지 않고 모달이
  떠 있는 동안에도 지도 이동/확대 가능 (스캔 이미지의 지명을 보면서 입력).
  경계 클릭 정보 카드도 머리 드래그로 이동 가능.
- **공간정보 다운로드** — 수정요청 패널에 [⬇ 공간정보] 버튼. 라인등록/삭제표기/
  속성등록을 종류별 GeoJSON 파일(EPSG:4326)로 저장 — QGIS 에 드래그하면 바로 열림.
  행정리명/부호/사유/작업자/상태 속성 포함. 현재 상태 필터에 보이는 것만 대상.
- **겹침 행정리 플래시** — 카드 클릭 이동 완료 시, 해당 선/점과 겹치는 행정리
  폴리곤들을 노란 펄스로 잠시 깜빡여 영향 범위 표시 (점=포함 폴리곤, 선=지나가는
  폴리곤 전부). 선을 따라 ~20m 간격 좌표 샘플링으로 브라우저에서 즉시 판정.
  `MapHandle.flashIntersectingBoundaries(markupId)` 신설, 기존 플래시를 다중
  피처 공용(`flashFeatures`)으로 리팩토링.
- 검증: 웹 `tsc -b` + vite build OK, 신규 lint 오류 없음.

## 2026-06-01 — 수정요청 목록 정렬 고정 (최신 등록이 위로)

`GET /api/markup` 에 ORDER BY 가 없어 처리(반영/반려) 시 해당 카드가 목록 맨 뒤로
이동하던 문제(PostgreSQL 이 UPDATE 된 행을 물리적으로 재배치하는 특성).
`json_agg(... ORDER BY id DESC)` 로 항상 최신 등록(id 큰 것)이 먼저 오도록 고정 —
처리해도 카드 위치가 바뀌지 않는다.

## 2026-06-01 — 근본 재설계: 마크업 처리를 전부 웹으로, QGIS 동기화 제거

발주처 요구 반영 — QGIS 와 웹의 수정요청을 동기화할 필요가 없음. 작업자는 웹의
요청 카드를 보고 QGIS 로 경계만 수정·제출하고, 요청의 반영/반려는 웹에서 처리한다.

```
pending ──[반영](웹 작업자)──> applied (끝)
   └────[반려·사유](웹 작업자)──> rejected (끝)
```

- **서버** — `apply`/`reject` 를 master(웹 작업자) 권한으로 변경(plugin 도 허용).
  `close` 엔드포인트·`closed` 상태 제거. `PUT /api/boundary` 의
  `resolved_markup_ids`(원자적 결합) 제거 — 경계 데이터만 다룸.
- **웹** — 요청 카드(pending)에 **[반영]/[반려]** 버튼(master). 반려는 사유 모달
  (`RejectReasonModal` 복원). `closed` 필터·라벨 제거. version 낙관적 잠금 유지.
- **QGIS 플러그인** — 마크업 기능 전체 제거: `MarkupReviewDialog`, [마크업 받기],
  상태 필터, [처리함] 체크, `resolved_markup_ids` 제출, `api_client.get_markup`/
  `reject_markup`, `layer_control.load_markup_layer`. 플러그인은 경계 제출 + COG
  업로드만 담당.
- **마이그레이션** — `202606_simplify_lifecycle.sql`: 기존 `closed`→`applied` 변환,
  status 제약 3상태, `closed_by/closed_at/reopened_at` 컬럼 제거. init.sql 동기.
- 검증: 서버/플러그인 `py_compile` OK, 웹 `tsc -b` OK
  (vite build 는 로컬 Node 버전 제약으로 배포 서버에서 수행).

## 2026-06-01 — 마크업 라이프사이클 단순화 (반려=QGIS 전용, reopen 제거)

웹/QGIS 양쪽에 반려 버튼이 중복돼 있고, 되돌리기/재요청(reopen) 분기가 많아
흐름이 복잡하던 것을 단순 한 방향 구조로 정리.

```
대기(pending) ─┬─→ 반영됨(applied) ─→ [웹 확인] → 종료(closed)
               └─→ 반려됨(rejected, QGIS 작업자·사유) — 끝
```

역할 분리: **웹(발주자)** = 요청 등록·회수(삭제)·결과 확인 / **QGIS(작업자)** = 반영·반려.
반려됐거나 반영 결과가 다르면 → 새 요청을 등록한다(reopen 없음).

- **백엔드** — `PATCH .../reopen` 엔드포인트 제거, `PATCH .../reject` 는
  PLUGIN_TOKEN 전용으로 제한(웹 master 반려 불가). 상태머신에서 `→ pending`
  재전이 삭제. 기존 데이터/DB 스키마 변경 없음(`reopened_at` 컬럼은 유지).
- **웹** — 수정요청 카드의 [반려]/[되돌리기]/[재요청] 버튼 제거 → 처리 버튼은
  반영됨 상태의 [확인] 하나만. 반려 카드에는 사유 + "새 요청을 등록하세요" 안내.
  `RejectReasonModal` 삭제, `rejectMarkup`/`reopenMarkup` API 함수 삭제.
- **QGIS** — 변경 없음(기존 '반려 (사유)' 버튼·반영 제출이 그대로 작업자 경로).
- API_REFERENCE.md 상태머신/권한 표 갱신.
- 검증: 웹 `tsc -b` + vite build OK, 백엔드 syntax OK.

## 2026-06-01 — QGIS 마크업 줌 이동 수정 (Point/수평선 bbox 0 → 줌 무시되던 버그)

마크업 검토 다이얼로그의 [줌 이동]/더블클릭이 속성등록(Point) 마크업에서 동작하지
않던 문제. `bbox.isEmpty()` 가 폭/높이 0 인 Point bbox 를 항상 걸러내 줌 자체가
무시됐다(작업레이어 유무와 무관).

- Point·수평/수직 라인 bbox 를 캔버스 단위 최소 크기(지리좌표 0.002°/투영 200m)로
  확장 후 줌. `isEmpty()` 조기 반환 제거.
- 빈 프로젝트(캔버스 CRS 미설정)면 4326 그대로 사용, 좌표 변환 실패 시 경고 표시
  (기존엔 조용히 무시).
- 검증: `py_compile` OK.

## 2026-06-01 — 행정리 목록 패널 (ri_nm/ri_cd/remark 테이블 + 더블클릭 이동)

- **BoundaryListPanel 신설** — 좌측 "행정리 목록" 버튼으로 토글되는 지도 위 패널.
  현재 행정읍면의 행정리를 행정리명/부호/비고 3컬럼 테이블로 표시(부호순 정렬,
  검색 입력 지원). 행 더블클릭 시 해당 행정리 영역으로 화면 이동(fit).
- **빈 데이터 처리** — 경계 데이터가 없으면 안내 문구, ri_nm 누락은 "(이름 없음)",
  ri_cd/remark 누락은 "-" 표기, 검색 결과 없음 안내.
- **이동 후 플래시 강조** — 더블클릭 이동(fit) 완료 시 해당 행정리 영역을 노란
  펄스(채움+외곽선, 3회 깜빡임 후 페이드아웃)로 강조. `MapView.flashBoundary(gid)`
  핸들 추가 — 임시 레이어에 피처를 복제해 rAF 애니메이션 후 제거.
- **도킹 패널로 변경** — 지도 위 오버레이가 지도를 가리는 문제 → 지도 옆
  사이드 패널(레이어 컨트롤과 지도 사이)로 이동. 토글 시 `map.updateSize()`.
- **자동 동기화 시 화면 유지** — 30초 자동 동기화가 boundary 를 다시 내려줄 때마다
  전체 범위로 fit 되어 사용자가 이동해 둔 화면이 풀리던 문제 수정 — fit 은
  읍면(adm_cd)이 바뀔 때만 실행. COG fit 도 동일 가드.
- **COG 404 콘솔 도배 해소** — titiler 는 이미지 범위 밖 타일 요청에 404 를
  반환(정상 동작). COG 레이어에 `setExtent(bbox)` 를 걸어 범위 밖 타일 요청
  자체를 차단.
- 검증: 웹 `tsc -b` + vite build OK, 신규 lint 오류 없음.

## 2026-06-01 — 속성등록 모달 통합 (2단계 입력 → 단일 모달)

속성등록 시 "수정사유 입력 → 저장 → 행정리명/부호 입력" 으로 모달이 두 번 뜨던
흐름을 하나로 합침. 첫 모달에서 수정사유를 입력해도 버려지던 문제도 함께 해소.

- **SaveMarkupModal** — kind=attr 일 때 모달 위쪽에 행정리명/부호 입력칸 표시
  (필수 — 비어 있으면 저장 비활성), 그 아래 기존 수정사유 입력. 저장 한 번에
  `attrs: { ri_nm, ri_cd, note? }` 로 등록.
- **AttrFormModal 제거** — 별도 2차 모달 및 `attrOpen` 상태 삭제.
- 검증: 웹 `tsc -b` + vite build OK, 신규 lint 오류 없음.

## 2026-06-01 — 왕복 워크플로우 마감: 비고 가시화 + 죽은 코드 정리 + 배치 upsert

lifecycle 구조 검토에서 나온 결함/비효율 정리. "QGIS 비고 → 웹에서 확인" 경로가
끊겨 있던 것(저장만 되고 반환 안 됨)을 복구하고, 선언만 돼 있던 낙관적 잠금을
실사용으로 연결.

- **비고(remark) 왕복 완성** — `GET /api/boundary` properties 에 `remark` 포함.
  웹은 툴 비활성 상태에서 경계 클릭 → 정보 카드(행정리명/코드 + **작업자 비고** +
  수정자/일시) 표시. `MapView` 에 `infoMode`/`onPickBoundary` 추가.
- **version(낙관적 잠금) 실사용** — reject/close/reopen 요청 body 에 `version` 수용
  (`expected_version` 가드는 있었으나 호출자가 없었음). 웹/QGIS 모두 회수 시점
  version 을 되돌려 보내고, 409 시 "다른 곳에서 먼저 처리됨" 안내 + 자동 새로고침.
- **죽은 컬럼 제거** — `resolved_boundary_gid`(추가만 되고 채우는 코드 없음) 를
  마이그레이션/응답/타입에서 제거.
- **PUT /api/boundary 배치화** — 피처당 UPDATE→INSERT 2쿼리 루프를 단일 CTE 쿼리
  (UPDATE 매칭분 + INSERT 신규분)로 교체. `boundary_adm_ri_uniq` 유니크 인덱스로
  (adm_cd, ri_cd) 키를 DB 가 직접 보장.
- **init.sql 동기화** — review_markup 이 구 스키마(pin/arrow/area, open/resolved)로
  남아 있어 신규 DB 구축 시 API 와 즉시 불일치하던 문제 해소. 마이그레이션 누적
  결과와 동일한 최신 스키마로 전면 재작성(auth/login_log/admin_node/admin_outline/
  markup_event 포함).
- **웹 자동 동기화** — 30초 주기로 markup/boundary 재조회(그리기·모달 중엔 일시정지)
  → QGIS 반영 결과가 수동 새로고침 없이 화면에 나타남.
- **부분 실패 가시화** — QGIS 제출 시 `resolve_failed` 를 id+사유로 메시지에 명시,
  제출 후 다이얼로그를 서버 재회수로 갱신(로컬 추정 패치 제거). 웹 다중삭제도
  실패 id 명시.
- 검증: 서버 `py_compile` OK, 웹 `tsc -b` OK.

## 2026-05-29 — 마크업 lifecycle 정식화 (왕복 상태머신 + 원자적 경계 결합)

웹↔QGIS 수정요청이 "단방향 + 수동 봉합"이라 상태가 표류하던 구조를 명시적
상태머신으로 교체. **경계를 고친 트랜잭션 = 요청을 처리한 트랜잭션** 으로 묶어
두 시스템 상태가 갈라질 수 없게 함.

- **상태머신** — `pending →(QGIS 반영) applied →(웹 확인) closed`, `pending →(반려)
  rejected`, `applied|rejected →(reopen) pending`. 전이는 전부 서버 전이 API 경유,
  `from-status` 가드 위반 시 `409`. `version`(낙관적 잠금) + `markup_event`
  (append-only 이력) 추가. 같은 상태로의 재호출은 멱등.
- **권한 분리** — `apply`(반영)는 **plugin 전용**(경계를 실제 고친 QGIS만 선언).
  `close`/`reject`는 master, `reopen`은 작성자/master. 웹의 "반영 도장" 제거.
- **원자적 결합** — `PUT /api/boundary` 에 `resolved_markup_ids` 추가 → 경계 upsert
  와 같은 트랜잭션에서 `pending→applied`. stale id 는 `resolve_failed` 로 보고.
  `feature.properties.remark` 로 **비고 서버화**(boundary.remark, 엑셀 로컬 탈피).
- **신규 엔드포인트** — `PATCH /api/markup/{id}/close`·`/reopen`. `GET /api/markup`
  status 에 `closed` 추가, 응답에 `version/closed_*/reopened_at/resolved_boundary_gid`.
- **QGIS** — 검토 다이얼로그에 `[처리함]` 체크 + `[반려]`. 제출 시 체크한 마크업
  id 를 경계와 함께 전송(원자적 반영). `api_client.submit_boundary(resolved_markup_ids=)`,
  `reject_markup()` 추가.
- **웹** — `MarkupCard` 가 상태별 버튼 분기(대기=반려 / 반영됨=확인·되돌리기 /
  반려=재요청), `closed` 필터·라벨. 처리 버튼은 master(`canProcess`)만 노출.
- **마이그레이션** — `202605_markup_lifecycle.sql`(가산적·멱등). init.sql 동기.
- 검증: 서버 `py_compile` OK, 웹 `tsc -b` OK.

## 2026-05-29 — 웹 삭제/삭제표기 마감: Ctrl+드래그 다중삭제 + ✕ 캔버스 렌더러

동료 제안(다중선택·X 렌더러)을 검토 후, 현재 동작 버전을 베이스로 좋은
아이디어만 이식(A안). 시그니처/undo·cancel·API삭제는 그대로 보존.

- **라인삭제 다중선택** — `MapView` eraseMode 에 `ol/interaction/DragBox`
  (`platformModifierKeyOnly`) 추가. **Ctrl(⌘)+드래그** 박스 안 마크업 id 목록을
  `onPickMarkupMany` 로 전달(맨 드래그는 지도 이동이라 modifier 요구).
  `InspectPage` 가 "N건 삭제하시겠습니까?" 확인 → `Promise.allSettled` 일괄
  `deleteMarkup`, 성공/실패(409·403) 건수 요약.
- **삭제표기 표시 업그레이드** — `delete_mark` 완성 스타일을 `Text.repeat('✕')`
  에서 **캔버스 `renderer`** 로 교체. 빨간 선 + 26px 등간격 ✕(흰 헤일로→빨강
  2패스로 선·지도 어디서나 가독). 픽셀 간격/크기 정밀 제어.
- 단일 삭제(클릭+확인+노란 하이라이트), 삭제표기 스냅 드로잉, 그리기 중
  undo/cancel 은 변경 없이 유지. 동료 버전의 2중 등록/로컬-only 삭제 버그는
  미채택.

## 2026-05-29 — 웹 `삭제표기`: 빨간 점 → 경계선 스냅 구간 + ✕ 반복 표시

`삭제표기(delete_mark)` 가 빈 자리에 빨간 점 하나 찍는 방식이라 "어느 경계가
잘못됐다"를 작업자에게 전달하기 어려웠음. 행정리경계 위를 따라 구간을 그어
✕ 로 표시하는 방식으로 전환(작업자 전달용 주석).

- **그리기 = 경계 스냅 라인** — `tools.ts` 의 `delete_mark` 를 Point→LineString.
  `attachTool(..., snapSource)` 신규 — `ol/interaction/Snap` 을 boundary 소스에
  걸어 경계 꼭짓점/선분에 자동 스냅. 시작점~끝점 클릭으로 구간 지정, 더블클릭 완료.
- **표시 = 빨간 선 + ✕ 반복** — `MapView.styleMarkup(delete_mark)` 가 빨간 선 위에
  `Text{ text:'✕', repeat:26, placement:'line' }` 로 ✕ 를 일정 간격 반복.
  그리는 중에는 빨간 점선(`KIND_STYLE`).
- **`MapView`** — `MapHandle.getBoundarySource()` 추가(스냅 대상 전달).
  하이라이트 헤일로: delete_mark 를 점→선 처리로 정정.
- **`InspectPage`** — `delete_mark` 면 boundary 소스를 스냅으로 넘김. DrawHint
  `isLine` 에 delete_mark 포함, 안내 문구 분기(경계 스냅 안내).
- **서버** — `_KIND_GEOM["delete_mark"]` 를 `("LineString","Point")` 로 확장
  (신규 구간 + 구버전 점 호환). 스키마 변경 없음.

## 2026-05-29 — 웹 `라인삭제`: 새 선 그리기 → 기존 마크업 선택·삭제(요청 회수)

`라인삭제(delete)` 가 `라인등록` 과 똑같이 새 빨간 선을 그리는 방식이라
직관과 어긋났음("기존에 그린 선을 지우는" 동작 기대). 그리기 대신
지도에서 마크업을 클릭해 회수하는 모드로 전환.

- **서버** — `DELETE /api/markup/{id}` 신규. 작성자가 잘못 올린 요청을 행 삭제.
  처리 전(`pending`)만 허용, `applied`/`rejected` 는 이력 보존으로 `409`.
  권한은 본인 adm_cd 만(마스터/플러그인 전체). 스키마 변경 없음.
- **`api/markup.ts`** — `deleteMarkup(id)` 추가.
- **`MapView.tsx`** — `eraseMode`/`onPickMarkup` prop. 삭제모드 시 마크업 레이어
  hit-test(`forEachFeatureAtPixel`, hitTolerance 6) + 커서 pointer. 피처의
  `id`(properties) 로 대상 식별.
- **`InspectPage.tsx`** — `tool==='delete'` 면 Draw 미부착, `eraseMode` 활성.
  클릭 → "수정요청 삭제하시겠습니까?" 확인 모달 → `deleteMarkup` → 재로딩.
- **`DrawHint.tsx`** — 삭제모드 안내 분기("지울 수정요청을 클릭하세요" + 툴 종료).
- 참고: 기존 `delete` 마크업(플러그인 dissolve용)은 표시·처리 그대로. 웹에서
  *새* delete 마크업을 만드는 경로만 사라짐 — 경계선 삭제요청은 `삭제표기` 사용.

  보정(같은 날):
  - 삭제 허용 범위를 `pending` 단독 → **`pending`+`rejected`** 로 완화. 반려된
    기존 라인이 `409` 로 안 지워지던 문제 해결(실측: DB 마크업이 전부 rejected
    였음). `applied` 만 이력 보존으로 `409`.
  - 삭제 실패 alert 를 상태코드별 메시지로 구체화(409/403/404).
  - **선택 라인 강조** — `MapView` 에 `highlightId` prop. 클릭한 마크업을 노란
    헤일로(선=굵은 반투명 노란선, 점=노란 링)로 표시. 삭제 확인 모달이 떠 있는
    동안 어떤 피처가 대상인지 시각 확인 가능.

## 2026-05-29 — 웹 그리기: 지우기/취소 단계 추가 (잘못 그린 선 되돌리기)

라인등록 등 그리기 툴에 *그리는 도중* 잘못 찍은 점을 지우거나 도형 전체를
취소하는 수단이 없어, 더블클릭으로 종료한 뒤 저장 모달에서 취소하는 것이
유일한 방법이었음. 그리기 중 되돌리기 흐름을 신설.

- **`tools.ts`** — `attachTool` 에 `onDrawingChange(drawing)` 콜백 추가
  (drawstart/drawend/drawabort 구독). `ActiveTool` 에 `removeLastPoint()`
  (마지막 꼭짓점 되돌림, OL `removeLastPoint`), `abort()`(`abortDrawing`),
  `isLine`(add/delete 만 다점) 노출. 점이 1개뿐인 라인은 removeLastPoint 대신
  전체 취소로 폴백.
- **`DrawHint.tsx`** 신규 — 툴 활성 시 지도 상단에 뜨는 안내·조작 바.
  그리는 중: `[↶ 마지막 점 취소]`(라인 한정) `[✕ 그리기 취소]`,
  대기 중: `[✕ 툴 종료]` + 단계 안내 문구.
- **`InspectPage.tsx`** — `drawing` 상태 추적, 단축키 추가
  (Backspace=마지막 점 취소, Esc=그리던 도형 취소→없으면 툴 종료).
  입력란 포커스/저장 모달 표시 중에는 단축키 비활성.
- "실제로 등록하시겠습니까?" 확인은 기존 [[SaveMarkupModal]]("등록한 내용을
  저장하시겠습니까?" + 수정사유)가 이미 담당 — 그 *앞 단계*의 지우기/취소를 보강.

## 2026-05-20 — 서버 `PUT /api/boundary` ri_cd 충돌 거부 (안전망)

클라이언트 보정([[ensure_unique_ri_cd]])과 별개로, 직접 API 호출 등 어떤
경로로 들어와도 조용한 데이터 손실이 없도록 백엔드에 가드 추가.

- `PUT /api/boundary` 가 payload 안의 `(adm_cd, ri_cd)` 키 중복을 사전 검사 →
  중복(특히 한 admin 에 ri_cd 누락 다발) 시 **400 거부** + 해당 adm_cd 안내.
  기존엔 upsert (adm_cd, ri_cd) 가 같은 행을 덮어써 365→17 처럼 조용히 소실.
- 빈 문자열/공백 `ri_cd` 를 `None` 으로 정규화 — `''` 와 `NULL` 동일 취급.
- admin 당 폴리곤 1건(ri_cd 없음)인 정상 케이스는 그대로 허용(거부 아님).

## 2026-05-20 — 행정리 작업: split→팝업 폐기, 선택 폴리곤 일괄 부여로 전환

분할할 때마다 `RiAssignDialog` 팝업이 떠 연속 분할이 끊기던 흐름을 변경.

- **분할은 자유롭게** — split/도형변경 시 area 필드만 자동 재계산
  (`layer_control.recalc_area`, featureAdded + geometryChanged 훅). 팝업 없음.
- **부여는 [선택 폴리곤에 행정리 부여] 버튼** — 지도에서 폴리곤(들) 선택 +
  명부 행 선택 → 버튼. 다중 선택 폴리곤 모두 같은 행정리로 부여 + 명부 Y 마킹.
- `RiAssignDialog` 클래스 제거(미사용). `recalc_area` 는 `_recalc_area` 에서
  공개 함수로 승격.

## 2026-05-20 — 경계 제출 시 ri_cd 유일성 보정 (데이터 손실 차단)

작업 SHP 의 ri_cd 가 비어 있으면(또는 admin 내 중복) 서버 boundary
upsert 키 (adm_cd, ri_cd) 가 충돌해 한 읍면의 폴리곤들이 한 행으로
뭉개졌음 — 실측: 365 피처 제출 → DB 17행 생존(admin 당 마지막 1조각),
"365건 반영" 은 INSERT 17 + 같은 행 UPDATE 348 의 합산이었음.

- **`layer_control.ensure_unique_ri_cd(fc)`** 신규 — admin 안에서 한 번만
  등장하는 실제 ri_cd 는 보존, 빈 값/중복은 미사용 3자리 일련번호
  ('001','002'…) 자동 부여. ri_nm 은 건드리지 않음(실제 이름은 사용자 몫).
- [3. 완료 데이터 업로드] / [2. 행정리 작업] 의 [제출] 양쪽 모두 제출 직전
  적용 + 자동부여 건수 로그/상태 표시.
- 검증: 22_bnd_job_pg(365 피처, ri_cd 전부 NULL, 17 admin×~21) → 365개
  전부 (adm_cd, ri_cd) 유일 키 확보, 손실 0.

## 2026-05-20 — API_REFERENCE.md 실제 구현 기준 전면 갱신

- 문서가 설계안 상태(쿠키 세션 + 공유 비밀번호 §1~6, 미구현 `/web/*` §7)로 굳어
  실제 백엔드(`backend/app/main.py`)와 어긋나 있어 전면 재작성.
- 인증: 쿠키 세션 → **단일 Bearer(JWT) 헤더**(localStorage `auth_token`). `/api/me`·`/api/logout`·
  `Set-Cookie` 설명 제거. 로그인은 `{id,password}` → `{token,user}`.
- `/web/*`(§7) 통째로 삭제 — 해당 기능은 전부 `/api/*` 로 구현됨(프론트 `web/src/api/*.ts` 와 일치).
- `/api/admins` 응답을 실제대로 정정: `{"admins":[...]}` 래퍼 → **순수 배열**(adm_cd/adm_nm/sido/sigungu).
- `/api/admin_outline`(이웃/bbox), `PUT /api/boundary`, `POST /api/cog`, markup `kind=delete_mark`,
  `POST /api/markup`·`PATCH .../apply`·`PATCH .../reject`(204) 추가/정정.
- 배포 섹션: 정적 루트 `/srv/gis/web/html`(MapLibre) → `web/dist`(React+Vite+OpenLayers)로 갱신,
  nginx 라우팅 표(`/web/` 는 backend 미구현 사장 라우트 명시), 부록 파일 경로 정정.

## 2026-05-20 — DB 작업: 완료 데이터 업로드 탭 신규

작업 완료 데이터를 폴더째 물려 일괄 업로드하는 [3. 완료 데이터 업로드]
탭 추가. 기존 [2. 행정리 작업] 의 [제출] 은 *세션에 로드된 레이어* 만
가능했음 — 이미 완성해둔 SHP/이미지를 별도 경로로 올릴 수 없었던 한계 해소.

- **폴더 구조** (`01_data` 규약):
  - `02_행정리경계/{시도}/{시도}_bnd_job_pg.shp` — 경계 (시도당 1 SHP)
  - `03_스캔이미지/{시도}/{시군구}/{adm}_scan_merged.jpg` — merge 이미지
- **[인식]** — 경계 SHP / 이미지 수집 + adm_cd 매칭표(경계○/이미지○) 표시
- **체크박스** `[경계 제출]` `[COG 업로드]` (둘 다 기본 ON, 한쪽만도 가능)
- **업로드** (`CompletedUploadWorker(QThread)`, 백그라운드):
  - 경계: SHP → `boundary_to_geojson`(메인스레드, RI_CD 소문자 정규화) →
    SHP 통째로 1번 `submit_boundary` (adm_cd+ri_cd upsert)
  - 이미지: `stage6_publish.publish_one` 재사용 (jpg→COG scale 0.5→S3→
    cog_catalog). S3 key = `cog/{시도}/{시군구}/{adm}.tif`
- 서버 설정은 [1. 서버 연결] 탭 값 사용. 결과 `_upload_status.csv` 기록.

## 2026-05-19 — DB 작업 명부 패널 4종 개선

- **split 후 area 자동 재계산** — `attach_autofill` 에 `featureAdded`
  처리 + `geometryChanged` 훅 추가. split 으로 줄어든 원본 폴리곤과
  새로 생긴 조각 모두 `area` 필드를 geometry.area() 로 갱신
  (기존엔 상속된 원본 면적 그대로 남아 있었음).
- **Y/N 저장 백그라운드 워커** — `WorkYnSaveWorker(QThread)` 신설.
  openpyxl 전체 워크북 reload/save 가 직렬 동기여서 38K행 명부에서
  콤보 변경마다 1~3초 UI 멈춤이 있었음. 콤보 시그널은 즉시 메모리·UI
  반영, 엑셀 쓰기는 백그라운드. 실패 시 (adm_cd, ri_cd) 키로 원래 행 찾아 revert.
- **명부 테이블 헤더 클릭 정렬** — `setSortingEnabled(True)` +
  work_yn 콤보 셀에 `QTableWidgetItem` 동기화(정렬 키). 워커 콜백은
  row index 가 아닌 (adm_cd, ri_cd) 로 행 lookup 해서 정렬 후에도 안정.
- **현재면적(㎡) 컬럼 신규** — bnd_job_pg 의 (adm_cd, ri_cd) 별
  geometry.area() 합. 작업데이터 `editingStopped` 시그널에 훅 → 편집
  저장하면 자동 갱신. 정렬도 숫자 기준.

## 2026-05-19 — Stage 6 외부 병합 폴더 입력 지원

- 기존엔 공통입력 `프로젝트 폴더` 하위 `5_merged/` 만 인풋 가능 → 미리
  생성해둔 병합본을 별도로 가리킬 수 있도록 Stage 6 탭에 두 PathRow 추가.
  - **병합 폴더 직접 지정** (비우면 프로젝트/5_merged)
  - **출력 폴더 직접 지정** (비우면 프로젝트/7_published)
- 둘 다 채우면 공통입력의 프로젝트 폴더 없이도 실행됨
  (`common.validate(need_proj=False)` 분기).
- CLI 측은 기존 `--merged`/`--out` 인자 그대로 — 플러그인 UI 만 개선.

## 2026-05-19 — 검수 웹 (kostat_front) 통합 — `web/`

- 행정리경계 검수용 React + Vite + OpenLayers 페이지를 같은 저장소
  하위 `web/` 폴더로 합류. QGIS 플러그인과 같은 백엔드(Funnel) 공유.
- 화면: 로그인 → 지도 검수 (라인등록/라인삭제/삭제표기/속성등록) →
  수정요청 우측 패널 (미처리/반영/반려 필터 + [반영][반려]) → 행정읍면
  선택 팝업(마스터). 화면정의서 11장 모두 대응.
- 백엔드 호환: `/api/admins`, `/api/boundary`, `/api/markup`(GET/POST/PATCH)
  — `db_tools/api_client.py` 와 스키마 일치 (kind/status/attrs 신규
  스키마 기준). `/api/login`, `/api/cog/{adm_cd}` 는 신규.
- 베이스맵: vworld WMTS (`/vworld` proxy). COG 타일은 백엔드의 titiler
  URL 받아 XYZ 레이어 갱신.
- 로컬: `cd web && npm install && npm run dev`. 배포: `npm run build` →
  `dist/` 를 nginx 정적 루트로.

## 2026-05-19 — Stage 6 bnd_job_pg 시드 동봉

- **`stage6_publish.write_bnd_job_pg()`** — COG 생성 직후 같은 슬롯에
  `{admin}_bnd_job_pg.shp` 시드 생성. bnd_adm_pg 에서 admin_cd 행 1건만
  필터 + 작업용 4컬럼(`spec`, `RI_NM`, `RI_CD`, `REMARK`) 추가 + `area`
  파생. 사용자는 QGIS 에서 이 폴리곤을 split 해 행정리별로 쪼개 나간다
  (기존 "0 피처 시드" 와 동일한 초기 상태를 파일로 미리 제공).
- CLI `--shp` 추가, plugin `Stage6Tab` 는 공통입력 SHP 가 채워져 있으면
  자동 전달. 미지정 시 COG 만 생성 (회귀 0).
- 산출 경로: `{out}/{시도}/{시군구}/{admin}_bnd_job_pg.{shp,dbf,shx,prj,cpg}`

## 2026-05-18 — Stage 6 COG 다운샘플 + 작업 흐름 개편

- **`stage6_publish.jpg_to_cog(scale=0.5, resample='lanczos')`** — 베이스
  해상도 1/2 (0.46m → 0.92m), 파일 크기 ~17% 로 축소. 워프 오차도 평균
  효과로 가려짐. 원본 유지가 필요하면 `--scale 1.0`. CLI `--scale` /
  `--resample` 옵션 추가.
- **DB 작업 패널 → QDockWidget** — QGIS 우측 도크에 고정, layout 보존.
- **명부 사전 필터** — `read_excel(adm_codes=...)`: 38K행 명부에서
  11_병합이미지 admin 코드 매칭 행(예: 48행)만 메모리 적재.
- **작업여부 인라인 편집** — 명부 테이블 콤보(공백/Y/N), 변경 시 엑셀
  즉시 저장(`update_work_yn` + `.bak` 백업).
- **split→팝업→자동Y 흐름** — 분할 즉시 `RiAssignDialog` 로 미완료 행정리
  선택 → 새 피처에 adm/ri 속성 자동 부여 + 명부 Y 마킹 + 엑셀 저장 +
  분류심볼 색 갱신.
- **0 피처 시드** — 작업데이터가 비어 있으면 [작업 시작] 시 행정경계의
  해당 읍면동 폴리곤을 자동 복사 (split 대상 모집단 확보).
- **작업데이터 분류 심볼** — ri_cd 별 색, 미부여(공백) = 회색으로 진척
  시각화.
- **화면 구성 시 qgz 스타일 적용** — `data/styles/*.qml` (12레이어)
  자동 loadNamedStyle, 캔버스를 작업데이터 extent 로 줌.

## 2026-05-14 — v3.0.0: 서버 연동 (HTTPS 배치 동기화)

PostGIS 직접 접속을 폐기하고 서울 서버와 HTTPS로만 통신하는 구조로 전환.
대전은 로컬 GeoPackage에서 디지타이징하고 결과만 제출, 발주자 검수는 웹.

- **신규 `db_tools/api_client.py`** — `ServerConfig`(QSettings) + REST/S3 클라이언트:
  `submit_boundary` / `get_markup` / `register_cog` / `upload_s3` / 연결 테스트
- **`db_editor.py` 재작성** — 2탭(서버 연결 / 행정리 작업):
  - "PG 연결" → "서버 연결" (API URL·토큰·S3 키)
  - "행정리 작업" — 작업 폴더 지정 시 하위 폴더 규칙(01_~13_)으로 13개
    레이어 슬롯 자동 인식 → [화면 구성]으로 QGIS 로드(on/off 기본값) +
    명부 로드 → [작업 시작] → 명부에서 행정리 선택 → 작업데이터 split →
    [제출](PUT /api/boundary) + [마크업 받기](GET /api/markup)
    (화면정의서 슬11~12 흐름)
- **`layer_control.py` 재작성** — `detect_work_folder` / `load_workspace` /
  `boundary_to_geojson`(속성 소문자 정규화) / `load_markup_layer` 추가,
  PostGIS 직결 제거. 작업데이터는 미리 분할된 시군구 SHP 를 그대로 편집
- **신규 Stage 6 (`tools/stage6_publish.py` + Stage6Tab)** — 병합 결과 →
  COG(GDAL) 변환 → MinIO 업로드 → `cog_catalog` 등록
- 제거: `db_tools/{pg_connection,admin_list,ri_list,job_table}.py` (PostGIS 직결 모듈)
- `requirements.txt`: `requests`, `boto3` 추가 / `psycopg2-binary` 는 파이프라인 잔존

## 2026-05-14 — Stage 4 PDF-less 자동 virtual merge 통합

Stage 4 가 PDF 없는 admin 만나면 자동으로 stage_virtual_merge 폴백 호출 →
SKIPPED 사라지고 모든 admin 이 동일한 출력 경로 (`{시도}/{시군구}/{admin}_scan_merged.{jpg,jgw,prj}`) 에 병합됨.

- Stage 4 신규 인자: `--extract-dir`, `--shp`, `--extract-csv`, `--auto-scale`,
  `--paper-w`, `--tile-gap`, `--center-mode`
- 인자 다 주면 PDF-less admin 도 자동 가상 병합 (status=`OK_VIRTUAL`)
- 안 주면 기존대로 SKIPPED (회귀 0)
- `merge_admin_virtual` 에 `flat_layout` + `basename` 옵션 추가 → Stage 4 와
  경로/파일명 통일

CLI:
```
python -m gis_scan_tools.tools.stage4_merge \\
    --warped warped/ --sheet-bboxes scan_id/sheet_bboxes.json \\
    --pdf-main pdf_main_geo/ --out merged/ \\
    --extract-dir extract/ --shp data/bnd_adm_pg.shp \\
    --extract-csv extract/_status.csv --auto-scale
```

## 2026-05-14 — PDF-less 분할 스캔 파이프라인

PDF 가 없는 분할 스캔 (admin 분할 N×N 시트만 보유) 도 같은 파이프라인으로
흘러가도록 정비. SHP 행정경계만 있으면 가상 메인 georef 합성까지 자동.

**분기 정책 (per-scan)**:

| 단계 | PDF 있을 때 | PDF 없을 때 |
|---|---|---|
| Stage 2 | 기존 OCR + valid_sheets 필터 | SHP fuzzy + 무필터 OCR, status=`OK_NO_PDF` |
| Stage 3 | SIFT ↔ sheet PDF 워핑 | passthrough (원본 복사, status=`PASSTHROUGH`) |
| stage_extract_map | ORB matching + HSV | HSV 단독 (기존 폴백 그대로) |
| Stage 4 | sheet_bboxes 기반 mosaic | SKIPPED (병합 불가, virtual merge 로 우회) |
| **stage_virtual_merge (NEW)** | — | admin 폴리곤 중심 + OCR scale → 가상 메인 georef |

**stage_virtual_merge.py** — 신규 stage:
- 입력: stage_extract_map 산출 body crop 들 + admin SHP
- N→grid: N=4→2x2, N=9→3x3 (그 외 = 일부 누락)
- sheet 배치: row-major top-down (1=TL, N=BR)
- ps 결정 우선순위:
  - `--ps <m/px>` 명시
  - `--auto-scale` + `--extract-csv`: scan 헤더 "1:K" OCR → ps = 0.925 × K / scan_w_px
    (한국 분할도 paper 폭 925mm 실측, EXIF DPI 명목 300의 ~1% 보정 흡수)
  - 폴백: admin bbox/canvas anisotropic
- canvas 중심: admin polygon centroid 또는 bbox center (`--centers`)
- 타일 간격: `--tile-gap 3` (기본, 흰 픽셀)
- 출력: `{admin}_virtual_merged.{jpg,jgw,prj}` + `_bbox.shp` (canvas 영역)

**검증 사례**:
- 36060320 옥룡면 (N=4): PDF 메타 1:7727 → ps=0.6542. SHP polygon 정확히 본문 외곽 일치
- 36570111 화순읍 (N=9, PDF 없음): scan 헤더 OCR K=1:5566 → ps=0.4765 (925mm 가정).
  육안 검증, polygon ↔ admin 동리 외곽 정합

**OCR 위치 (한국 분할도 헤더 표준)**:
- 헤더 좌측 "출력 축척" 셀, scan 좌표 (y=2.5~6%, x=30~40%)
- grayscale + threshold(180) + tesseract psm=6 kor+eng
- regex `r'1[:.]?(\d{1,2},?\d{3,4})'`

**산출 파일 흐름** (PDF-less):
```
scan/ → Stage2 → identified/ → Stage3 → passthrough copy
                            ↘ stage_extract_map → body crops
                                                ↓
                            stage_virtual_merge ← admin SHP
                                                ↓
                            {admin}_virtual_merged.{jpg,jgw}
```

## 2026-04-27 — S7 zone 분리 검출 + 폴백 strength filter

극단적으로 약한 시트 (39020120_4-4: 본문 거의 비고 헤더 약함, row max=0.158)
처치 강화:

- **헤더 / 본문 하단을 zone 별 독립 검출** — 전체 row_thr → 폴백 식이면
  본문 안 noise 가 row_lines 전체에 끼어 zone 선택 망가짐. zone 안에서
  primary → relaxed 폴백 으로 일관화
- **헤더 zone 좁힘**: header_zone × 0.4 (= 위 12%) — 본문 도시 영역 oversampling
  방지. 모든 알려진 시트 헤더 분리선이 위 10% 안. 미검출 시 0.7 까지 확장
- **strength filter**: 폴백 검출 시 라인 후보의 *peak 강도* 가 max × 0.5
  미만이면 헤더 라인으로 인정 안 함 — 본문 라벨/도시 경계 등 약한 신호 회피
- **bot_zone 가장 강한 peak 선택** (min 대신): 폴백 시 다중 라인 중 진짜
  본문 하단 프레임 (가장 강한 peak) 선택 → 본문 안 noise spike 회피
- **검증**: 11 케이스 (6 silent + 4 다양한 FAIL + 1 정상) 모두 ratio 0.84~0.90

## 2026-04-27 — S7 적응 HSV 게이트 + col 검출 보강 + row 임계 폴백

스캐너 캘리브레이션·종이 노화 차이로 시트별 "흰 톤" V 가 234~254 로
편차 있음 — 고정 임계 V≤130 이 밝은 시트에서 라인 검출 미달.

- **적응 v_max**: `max(130, percentile_95(V) - 70)`
  - 시트의 흰 톤에서 darkness offset 만큼 아래까지 통과
  - 어두운 시트는 130 floor 로 보호 (회귀 0)
- **col_thr 0.25 → 0.10**: 좌/우 외곽 약한 시트 회수
- **col 좌/우 zone 별 검출**: outer_zone=0.15. 좌(< w*0.15) / 우(> w*0.85)
  zone 내 라인 사용. 미검출 시 image edge 폴백
- **row 검출 폴백 2단계**:
  - 헤더조차 검출 실패 → 전체 row_pct p99·0.9 로 완화 재탐색
  - bot_zone 검출 실패 → bot_zone 내 p99·0.9 로 완화 재탐색
  - 그래도 신호 없으면 image bottom (스캔 잘림 케이스)
- **검증**: 11 케이스 (6 silent + 4 cols=0 / rows=0 FAIL + 1 정상 표본)
  모두 OK, ratio 0.85~0.90

## 2026-04-27 — S7 silent fail 차단 + 하단 인쇄 약함/스캔 잘림 회수

- **`extract_map_region_scan` 하단 검출 강화**
  - 본문 하단 프레임이 row_thr=0.20 임계 *바로 아래* (~0.18) 라 검출 실패 →
    임계 0.17 로 완화. 정상 시트 회귀 없음 (header 셀선은 더 높음, 본문 안엔
    중간 영역 신호 없음 — 실측 확인)
  - bot_zone (아래 30%) 검출 실패 시 폴백: 이미지 하단 (`h-1`) 사용
    · 스캔 잘림 시트 (39010320_7-3, 7-4, 39010330_4-1) 회수
    · 인쇄 약함 시트 (39010110_4-2 등) 회수
  - 사이즈 sanity: 추출 영역이 image × 0.30 미만이면 하단/우측을 이미지
    가장자리로 강제 폴백, 그래도 안되면 ValueError. **silent fail 차단**
    (이전: status=OK 인데 crop=9311×2 인 6장이 다음 단계로 흘러감)
- 신규 인자: `min_size_ratio=0.30` (사이즈 sanity 임계)
- 검증: 6 silent-fail 케이스 모두 ratio_h ≈ 0.86 ~ 0.90 으로 정상 추출

## 2026-04-27 — Stage 3 정합 정확도 회복 (TPS → 단일 H + 폴리곤 필터)

- **TPS 폐기 → 단일 호모그래피 워핑**
  - 분할시트 한 장 안에선 종이 휨이 충분히 선형 → 단일 H 가 TPS 보다 정확
  - TPS smoothing=0 + 400 GCP 가 GCP 사이 진동(Runge 현상) 일으켜 mean
    abs-diff 가 23→30 으로 회귀하는 것을 확인 (39010110_4-1 케이스)
  - 단일 H 로 회복: mean **22.96** / p95 **54** / p99 **96** (폴리곤 ∩ ¬마커)
  - 워핑 시간 2.3s → 0.1s (23배). 시트당 처리 ~20s → ~17s
  - `cv2.warpPerspective(WARP_INVERSE_MAP)` 한 번으로 scan→world 출력 frame 까지
    합성변환 적용 (중간 grid resample 제거)
- **행정리 폴리곤 필터 추가**
  - SHP `bnd_adm_pg.shp` 의 admin_cd 폴리곤을 sheet PDF px 로 투영해 마스크 생성
  - MAGSAC inlier 중 폴리곤 내부 점만 선별 → outlier 가 들어올 영역(헤더/바다)
    원천 차단, sparse 시트에서 효과 큼
  - 폴백: in-polygon < 50 이면 전체 inlier 사용 (도서/특수 케이스 보호)
  - SHP/admin 미존재 시 graceful 폴백 (전체 inlier 사용)
- **CLI**: `--shp` 인자 추가 (default: 패키지 `data/bnd_adm_pg.shp`)
- **헬퍼**: `tools/_legacy/common.py` 에 `load_admin_polygon_world()`,
  `build_admin_polygon_mask()` 추가. (shp_path, admin_cd) 캐시 — 같은 시트
  여러 admin 처리 시 SHP 재스캔 0회

## 2026-04-27 — S7 지도영역 추출 알고리즘 교체

- **SIFT + MAGSAC + TPS 폴백 → HSV "어두운 무채색" 게이팅**
  - 인쇄 프레임선의 본질(저채도 + 중간 어두움)을 직접 게이팅 → 스캐너 색감
    변동·용지 노화에 강건. 빨강 수기·주황 경계는 자동 제외.
  - **참조 PDF(sheets_geo) 불필요** — 시트당 처리 시간 ~7s → < 3s
  - 다도해/빈 시트(예: `39010320_7-6`) — SIFT 키포인트 부족으로 실패하던
    케이스가 정상 처리됨
  - 행/열 매칭 비율 프로파일에서 헤더/지도 분리선 + 외곽 4변 동시 검출,
    inset px 안쪽 자르기로 잔여 검정 줄 회피
  - 신함수 `extract_map_region_scan()` 추가 (PDF용 `extract_map_region()` 은 유지)
  - `stage_extract_map.py` 전면 재작성, plugin `ExtractMapTab`에서 `--sheets-geo` 인자 제거
  - 한계: 종이 휨/회전이 큰 경우 검출 라인이 단일 행/열로 합쳐지지 않을 수 있음
    — 후속 단계에서 4변 곡선 추적 + TPS rectify 도입 검토

## 2026-04 — 대규모 개선 (Phase 0 ~ Phase 7)

### 파이프라인 견고화 (Phase 1c, 1d, 1e)

- **Stage 1 PDF 메타 기반 자동 georef**
  - PDF 텍스트의 축척(1:N) + 분할도 라벨 좌표 + SHP admin bbox로 즉시 정합
  - 발주처 SIFT 실패 2건(추자면 다도해, 서귀포) 자동 회수 → 12/12 OK
  - SIFT/Powell은 메타 추출 실패 시 폴백
  - 정확도: 기존 SIFT 결과 대비 ≤4m
- **Stage 2 OCR 회수율 35% → 0%**
  - SHP 전국 행정명 lookup + 7~9자리 자릿수 fuzzy 매칭
  - PDF 분할 후보군(N-i) 활용 + 숫자만 인식 (7→`[` 오인식 회수)
  - 발주처 데이터 8건 FAIL → 0건 FAIL
- **Stage 2 sheet bbox — SIFT 우회**
  - 메인 PDF 라벨 좌표 + 고정 오프셋(-13.82, -2.76 pt)으로 즉시 추출
  - 정확도 ±15m (SIFT 대비 충분), 시트당 ~10s 단축
- **폴더 기반 파이프라인** (Phase 1e-1)
  - Stage 3 입력을 `_identification.csv`에서 `identified/` 폴더 스캔으로
  - 사용자 수동 rename 파일이 자동 포함 → CSV 편집 불필요

### 사용자 경험 (Phase 1a, 1b, 1e-2, 1e-3)

- **[2a. 미식별 보강] 탭** — 실패 스캔 드롭다운 UI로 복구
  - SHP 전국 3561개 코드 자동완성 + 썸네일 미리보기
- **[2b. 지도영역 추출] 탭** — S7 화면정의서 대응
- **Stage 1 [외부 JGW 가져오기] 버튼** — QGIS Georeferencer 수동 결과 자동 복사
- **Stage 4 `--inner-margin` 옵션** — S9 테두리 여유

### DB 작업 플러그인 (Phase 2~6)

- **별도 툴바 아이콘** — 같은 패키지에 2개 진입점 (파이프라인 / DB 작업)
- **[1. PG 연결] 탭** — PGProfile QSettings 영속 + 연결 테스트
- **[2. 엑셀 탑재] 탭** — 행정리현황 엑셀 → `ri_status` 업서트
  - 영문/한글 컬럼명 alias 관용 (SIDO_CD == 시도코드)
  - `ON CONFLICT (adm_cd, ri_cd) DO UPDATE`
- **[3. 행정리 작업] 탭** — 전국 읍면동 리스트 + 더블클릭 맵 줌
  - `bnd_job_pg` 스키마 자동 생성 (GIST/btree/UNIQUE 인덱스)
  - 워프 스캔 자동 로드 + 이전 admin 레이어 제거
  - RI 선택 → Split 시 자동 속성 부여 (UX C 구현)
- **"행정리 편집" QGIS 툴바** — PyQGIS 3.40 내장 액션 재사용
  - Toggle Editing / Save Active Layer Edits / Split Features /
    `native:simplifygeometries` 다이얼로그

### 문서/성능 (Phase 0, 7)

- Stage별 프로파일링 (`OPTIMIZATION_NOTES.md`)
- README 전면 개편 (DB 작업 섹션, 폴더 기반 워크플로, 트러블슈팅)
- CHANGELOG 신설

## 기존 이력 (요약)

- TPS 워핑 10배 가속, 분할 PDF 매칭으로 스케일 일치
- 한글 경로 cv2 우회
- Tesseract 크로스플랫폼 자동 탐색
- 5-탭 파이프라인 UI (공통입력 + 자동출력)
