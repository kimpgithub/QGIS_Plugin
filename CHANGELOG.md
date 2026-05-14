# Changelog

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
