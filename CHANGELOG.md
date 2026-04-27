# Changelog

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
