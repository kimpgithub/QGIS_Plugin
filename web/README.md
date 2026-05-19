# 행정리경계 검수 웹 (kostat_front)

발주자가 행정리 경계 작업물을 검토하고 수정요청을 등록 / 작업자가
수정요청을 반영·반려 처리하는 React 웹 페이지.

같은 백엔드(Funnel) 를 QGIS Scan Tools 플러그인과 공유:

- `/api/admins`, `/api/boundary`, `/api/markup`, `/api/login`, `/s3` 등
- 베이스맵: vworld WMTS (proxy `/vworld`)
- COG 타일: 백엔드의 titiler 등에서 받은 XYZ URL

## 스택

| | |
|---|---|
| Build | Vite 8 |
| UI | React 19 + TypeScript |
| Map | OpenLayers 10 |
| Lint | ESLint 10 + Prettier 3 |

## 실행

```bash
cd web
npm install
cp .env.example .env       # VITE_API_TARGET 가리킬 백엔드 호스트 입력
npm run dev                # http://localhost:3000
npm run build              # dist/ 생성 — nginx 정적 호스팅용
```

## 디렉토리

```
src/
├── App.tsx               AuthProvider + 조건 라우팅
├── types/                Markup, AuthUser, AdminUnit, 최소 GeoJSON 타입
├── api/                  fetch 래퍼 + 도메인별 모듈
│   ├── client.ts         Bearer 자동 부착 + ApiError
│   ├── auth.ts           POST /api/login (dev mock 폴백)
│   ├── admins.ts         GET  /api/admins
│   ├── boundary.ts       GET/PUT /api/boundary
│   └── markup.ts         GET/POST/PATCH /api/markup
├── store/AuthContext.tsx 토큰 localStorage 보존
├── pages/
│   ├── LoginPage.tsx
│   └── InspectPage.tsx
└── components/
    ├── common/{Modal,CommonSelect}.tsx
    ├── map/
    │   ├── MapView.tsx       vworld 베이스 + COG + admin overlay + 경계 + markup
    │   ├── ToolBar.tsx       라인등록/라인삭제/삭제표기/속성등록/행정읍면선택
    │   ├── LayerControls.tsx 좌측 체크박스 (4레이어)
    │   └── tools.ts          OL Draw interaction 어태치 헬퍼
    ├── panel/
    │   ├── MarkupPanel.tsx   필터(미처리/반영/반려) + 카드 리스트
    │   └── MarkupCard.tsx    kind 배지 + [반영][반려]
    └── modal/
        ├── SaveMarkupModal.tsx     drawend 후 저장/취소
        ├── RejectReasonModal.tsx   반려 사유
        ├── AttrFormModal.tsx       속성등록 폼
        └── AdminPickerModal.tsx    마스터용 행정읍면 선택
```

## 인증

- 사용자 ID = 행정읍면 8자리 코드 (해당 코드만 접근 가능)
- 마스터 ID 는 별도 (`role: 'master'`) — 행정읍면 선택 팝업으로 전국 조회
- 토큰은 `localStorage('auth_token')` 보존. 401 응답 시 호출부가 로그아웃 트리거

## 백엔드 엔드포인트 (현재 가정)

```
POST   /api/login              { id, password } → { token, user }
GET    /api/admins             AdminUnit[]
GET    /api/boundary?adm_cd=   GeoJSON FeatureCollection
PUT    /api/boundary           FC → upsert
GET    /api/markup?adm_cd=&status=   GeoJSON FC (properties = Markup)
POST   /api/markup             MarkupCreate
PATCH  /api/markup/{id}/apply
PATCH  /api/markup/{id}/reject { reason }
GET    /api/cog/{adm_cd}       { tile_url }  ← 추후 연결
```

`/api/admins, /boundary, /markup` 은 QGIS 플러그인 `db_tools/api_client.py`
와 호환. 나머지는 본 페이지 추가용으로 백엔드 합의 필요.

## 배포

`npm run build` → `web/dist/` → nginx 정적 루트로 복사. API 는 같은
nginx 에 `/api`, `/s3` 로 reverse-proxy 되므로 same-origin.

## 진행 상황

- ✅ 로그인 (슬라이드 1)
- ✅ 지도 + 레이어 컨트롤 (슬라이드 2~3)
- ✅ 라인등록/라인삭제/삭제표기/속성등록 (슬라이드 4~7)
- ✅ 카드 클릭 → Extent 자동줌 (슬라이드 8)
- ✅ 반영/반려 처리 (슬라이드 9~10)
- ✅ 행정읍면 선택 팝업 (슬라이드 11)
- ⏳ COG 타일 URL 연결 (`/api/cog/{adm_cd}`)
- ⏳ 삭제표기 드래그 멀티선택
