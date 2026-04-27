# GIS Scan Tools — QGIS 플러그인

스캔된 종이 지도에 좌표를 부여해 GIS 데이터로 변환하고, 그 위에
행정리 경계를 편집해 PostGIS에 저장하는 QGIS 플러그인.

같은 내용의 PDF를 좌표 운반체로 사용하므로 스캔 품질에 영향을 덜 받습니다.

## 구성

QGIS 툴바에 아이콘 3개 + 전용 편집 툴바:

| 아이콘 / 툴바 | 역할 |
|---|---|
| **파이프라인** (7탭 다이얼로그) | 스캔 → 좌표부여 → 병합 → 검수 |
| **DB 작업** (4탭 다이얼로그) | PostGIS 연결 + 엑셀 탑재 + 행정리 경계 편집 |
| **"행정리 편집" 툴바** | Toggle Editing / Save / Split / Simplify |

---

## 파이프라인 (자동화)

### 처리 흐름

| 단계 | 역할 | 핵심 기술 |
|---|---|---|
| **1. PDF 좌표생성** | 메인 PDF에 JGW 부여 | PDF 메타(축척+분할도 라벨) + SHP admin bbox — 즉시, ≤4m. SIFT/Powell 폴백 |
| **2. 스캔 식별** | 스캔의 (admin_code, sheet_id) 판정 | 헤더 OCR + SHP 한글명/fuzzy 회수 + PDF 후보 매칭 |
| **2a. 미식별 보강** | 실패 스캔 수동 지정 | SHP 드롭다운 UI — CSV 편집 불필요 |
| **2b. 지도영역 추출** | 스캔 프레임 안쪽 잘라냄 | HSV 어두운 무채색 게이트 (참조 PDF 불필요) |
| **3. 매칭+워핑** | 스캔 ↔ 분할 PDF → 픽셀 정합 | SIFT + TPS (비선형) |
| **4. 사분면 병합** | 시트별 크롭 → 모자이크 | world bbox 기반 + 테두리 여유 옵션 |
| **5. 경계 검수** | 병합 결과 vs SHP 경계 비교 | 오렌지 마스크 distance map |

### 입력 파일 컨벤션

- **메인 PDF**: `{8자리}.pdf` (예: `21510110.pdf`)
- **분할 PDF**: `{8자리}_{N}-{i}.pdf` (예: `21510110_4-1.pdf`, `39010320_7-3.pdf`)
- **스캔 이미지**: 자유 이름 (헤더 OCR로 자동 식별)
- **SHP**: 전국 행정경계 `bnd_adm_pg.shp` (EPSG:5179, `adm_cd`/`adm_nm` 컬럼 필수)

### 폴더 기반 워크플로 (CSV 편집 불필요)

```
실패 스캔이 생겨도 CSV를 손대지 않고 UI로 해결:

Stage 2 실행 → FAIL이면 _unmatched/에 격리
  ↓
[2a. 미식별 보강] 탭 → 파일 선택 → 썸네일 확인
  → admin_code 드롭다운(SHP 전국 3561개 검색) + sheet_id 드롭다운
  → [저장] → identified/{시도}/{시군구}/{admin}_{sheet}.jpg 로 복사
  ↓
Stage 3 재실행 → identified/ 폴더 스캔 → 자동 포함
```

Stage 1 실패 admin은:
```
Stage 1 탭 → [외부 JGW 가져오기] 버튼
  → QGIS Georeferencer 등으로 만든 {code}.jgw 선택
  → 1_pdf_geo/ 로 자동 복사 (같은 경로의 .jpg, .prj 동반)
```

---

## DB 작업 (행정리 경계 편집)

### 다이얼로그 4탭

| 탭 | 기능 |
|---|---|
| **1. PG 연결** | PostgreSQL/PostGIS 접속 프로파일 관리. 여러 환경(개발/운영) 저장 |
| **2. 엑셀 탑재** | 행정리현황 엑셀 → `ri_status` 테이블 업서트. 한글/영문 컬럼 관용 |
| **3. 행정리 작업** | 전국 읍면동 리스트 + 더블클릭 맵 줌 + `bnd_job_pg` 자동 생성 |
| **4. 경량화** | (Processing simplify 다이얼로그로 대체, 편집 툴바 참조) |

### 편집 워크플로 (Split UX C)

```
1. [3. 행정리 작업] 탭 → 읍면동 리스트 로드 → 대상 읍면 더블클릭
   → 맵이 해당 영역으로 줌 + (설정 시) 워프 스캔 자동 로드
2. 같은 탭에 RI 리스트 자동 표시 → 대상 행정리 클릭 (예: "동부1리 001")
3. [작업 시작] → bnd_job_pg만 편집 가능, 나머지 레이어 readOnly
4. QGIS "행정리 편집" 툴바:
     · Toggle Editing (이미 자동 활성화됨)
     · Split Features — 라인으로 폴리곤 자르기
       → 새로 생긴 피처에 adm_cd/adm_nm/ri_cd/ri_nm 자동 기록
     · Save — 편집 저장
     · Simplify — Processing 다이얼로그 (허용 오차 입력)
5. [작업 종료] → commit + 잠금 해제
```

### bnd_job_pg 스키마 (자동 생성)

```sql
gid         SERIAL PRIMARY KEY
geom        geometry(MultiPolygon, 5179)
adm_cd      VARCHAR(8)  NOT NULL
adm_nm      VARCHAR(100)
ri_cd       VARCHAR(10) NOT NULL
ri_nm       VARCHAR(100)
status      VARCHAR(20) DEFAULT 'draft'
created_at, updated_at

인덱스: GIST(geom), btree(adm_cd), UNIQUE(adm_cd, ri_cd)
```

### ri_status 스키마 (엑셀 탑재 시 자동 생성)

image5 기준 10컬럼 + `UNIQUE (adm_cd, ri_cd)`. 재탑재 시 ON CONFLICT DO UPDATE.

---

## 설치

### 1. 의존성

OSGeo4W Shell 또는 QGIS 내장 Python에서:

```bash
pip install -r requirements.txt
# Tesseract OCR (시스템 바이너리)
# Linux/Mac: conda install -c conda-forge tesseract
# Windows: install.bat 참조
```

필수 Python 패키지:
- `opencv-python`, `numpy`, `scipy`, `scikit-image`
- `PyMuPDF` (fitz), `Pillow`, `pytesseract`
- `geopandas`, `shapely`
- `psycopg2` (DB 작업)
- `openpyxl` (엑셀 탑재)

### 2. 플러그인 등록

`gis_scan_tools` 폴더를 QGIS 플러그인 디렉토리에 복사:
- Windows: `C:\Users\{사용자}\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
- Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

QGIS 재시작 → 플러그인 관리 → "GIS Scan Tools" 활성화.

### 3. PostGIS 준비 (DB 작업용)

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
-- 필수 선행 데이터: bnd_adm_pg 테이블 (읍면동 경계)
-- ri_status, bnd_job_pg는 플러그인이 자동 생성
```

### 4. CLI 실행 시 환경변수

QGIS 외부에서 stage 모듈을 직접 실행할 때:

```bash
export PROJ_DATA=/opt/conda/envs/{env}/share/proj
export PROJ_LIB=$PROJ_DATA
```

---

## CLI 사용법

각 stage는 독립 실행 가능:

```bash
cd /path/to/parent_of_gis_scan_tools

# Stage 1 — PDF 메타 기반 자동 georef (SIFT 폴백 내장)
python -m gis_scan_tools.tools.stage1_pdf_georef \
    --in pdf_input/ --shp bnd_adm_pg.shp --out pdf_main_geo/

# Stage 2 — OCR 식별 (--shp 권장: 자릿수 오류 + 한글명 회수)
python -m gis_scan_tools.tools.stage2_scan_identify \
    --in scan/ --pdf-input pdf_input/ \
    --pdf-main pdf_main_geo/ --shp bnd_adm_pg.shp \
    --out scan_identified/

# S7: 지도영역 추출 (HSV 게이트 — 참조 불필요, < 0.5s/장)
python -m gis_scan_tools.tools.stage_extract_map \
    --identified scan_identified/identified --out map_extracted/

# Stage 3 — identified/ 폴더 기반 입력 (수동 보강 파일 자동 포함)
python -m gis_scan_tools.tools.stage3_scan_warp \
    --identified scan_identified/identified \
    --sheets-geo scan_identified/sheets_geo \
    --out warped/

# Stage 4 — 병합 (시트 경계 여유 옵션)
python -m gis_scan_tools.tools.stage4_merge \
    --warped warped/ --sheet-bboxes scan_identified/sheet_bboxes.json \
    --pdf-main pdf_main_geo/ --inner-margin 30 --out merged/

# Stage 5 — 경계 검수
python -m gis_scan_tools.tools.stage5_validate \
    --merged merged/ --shp bnd_adm_pg.shp --out validation/
```

---

## 처리 시간 (참고)

| 단계 | 단위 | 단가 | 비고 |
|---|---|---|---|
| Stage 1 | admin | ~26s | PDF 렌더링이 대부분 |
| Stage 2 | scan | ~22s | OCR + PDF 라벨 (SIFT 우회) |
| S7 | scan | ~3s | HSV 게이트 + 라인 프로파일 |
| Stage 3 | sheet | ~50s | SIFT + TPS 워핑 |
| Stage 4 | admin | ~14s | 병합 |
| Stage 5 | admin | ~27s | 검수 + 시각화 |

**만장 스케일** (예: 10000 admin × 4시트):
- 단일 프로세스 ~30일 → 4프로세스 병렬 ~7-8일

상세 분석: `OPTIMIZATION_NOTES.md`.

---

## 주요 산출 파일

```
{프로젝트}/
├── 1_pdf_geo/
│   ├── {code}.jpg, .jgw, .prj
│   └── _status.csv
├── 2_scan_id/
│   ├── _identification.csv        # 식별 결과 (감사 로그)
│   ├── sheet_bboxes.json          # admin별 시트별 world bbox
│   ├── identified/                # 표준명 스캔 (Stage 3 입력)
│   │   └── {시도}/{시군구}/{admin}_{sheet}.jpg
│   ├── _unmatched/                # 실패 스캔 (2a 탭으로 보강)
│   ├── sheets_geo/                # 분할 PDF + JGW (Stage 3 매칭용)
│   └── _sheet_cache/
├── 2b_map_only/                   # 헤더/프레임 제거 스캔 (HSV 게이트)
├── 3_warped/
│   └── {시도}/{시군구}/{admin}_{sheet}/
│       ├── {admin}_{sheet}.jpg, .jgw, .prj
│       └── status.json
├── 4_merged/
│   └── {시도}/{시군구}/{admin}_scan_merged.{jpg,jgw,prj}
└── 5_validation/
    └── {admin}_*_check_result.png
```

---

## 트러블슈팅

### 파이프라인

- **Stage 1 cost > 2px / PDF 메타 실패**: 텍스트 임베드 없는 PDF(이미지만)인지 확인. `--no-pdf-meta`로 SIFT 폴백 강제 가능
- **Stage 1 다도해 admin(추자면 등) 실패**: PDF 메타 경로가 자동 대응. 여전히 실패 시 [외부 JGW 가져오기] 수동 해결
- **Stage 2 OCR 실패**: `--shp` 필수. 그래도 실패한 파일은 `_unmatched/`에 격리 → [2a. 미식별 보강] 탭으로 해결
- **Stage 2 sheet bbox 부정확**: PDF 텍스트 라벨 없으면 SIFT 폴백. 모서리 ±15m 허용
- **Stage 3 inliers < 30**: 스캔 품질 문제. `04_matches_inliers.jpg` 시각화 확인
- **Stage 4 누락 시트**: `{admin}_status.json`의 `skipped` 필드 체크

### DB 작업

- **[1. PG 연결] 실패**: host/port/db/user 확인. 네트워크 방화벽 체크. 테스트 버튼으로 PostGIS 확장 유무도 보고됨
- **[3. 행정리 작업] 리스트 비어있음**: PG에 `bnd_adm_pg` 테이블 필요. schema 지정 확인 (기본 `census_23p`)
- **Split 후 속성 안 들어감**: `[3. 행정리 작업]` 탭에서 RI 행을 미리 클릭해야 함 (활성 RI 상태 설정)
- **"행정리 편집" 툴바가 안 보임**: QGIS 재시작 후 보기 → 툴바에서 체크

---

## 아키텍처

```
gis_scan_tools/
├── plugin.py                    # QGIS 진입점 — 툴바/다이얼로그 등록
├── db_editor.py                 # DB 작업 다이얼로그
├── tools/                       # 파이프라인 stage 모듈
│   ├── stage1_pdf_georef.py
│   ├── stage2_scan_identify.py
│   ├── stage_extract_map.py     # S7
│   ├── stage3_scan_warp.py
│   ├── stage4_merge.py
│   ├── stage5_validate.py
│   └── _legacy/                 # SHP Georeferencer, 공통 유틸
├── db_tools/                    # DB 작업 백엔드
│   ├── pg_connection.py         # PGProfile + 연결 테스트
│   ├── excel_loader.py          # 엑셀 → ri_status 업서트
│   ├── admin_list.py            # bnd_adm_pg 리스트 쿼리
│   ├── ri_list.py               # ri_status 조회
│   ├── job_table.py             # bnd_job_pg CREATE
│   └── layer_control.py         # QGIS 레이어 편집/가시성 + featureAdded 훅
├── OPTIMIZATION_NOTES.md
└── README.md
```

---

## 변경 이력 주요 사항

- PDF 메타 기반 Stage 1 자동 georef (다도해/특이 admin 자동 회수)
- SHP 기반 Stage 2 OCR 회수 (자릿수 fuzzy + 한글명 lookup)
- PDF 라벨 기반 sheet bbox (SIFT 우회, ±15m)
- CSV → 폴더 기반 파이프라인 전환
- 미식별 보강 UI (드롭다운)
- DB 작업 플러그인 (PG 연결 + 엑셀 탑재 + 행정리 편집)
- QGIS 3.40 편집 툴바 (Toggle/Save/Split/Simplify)

## 라이선스 / 기여

(생략)
