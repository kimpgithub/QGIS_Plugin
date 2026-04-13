# GIS Scan Tools — 5-단계 파이프라인

스캔된 종이 지도에 GPS 좌표를 입혀 GIS 데이터로 변환하는 QGIS 플러그인.

같은 내용의 PDF를 좌표 운반체로 사용하여 스캔 품질에 영향을 덜 받습니다.

## 처리 흐름

| 단계 | 역할 | 출력 |
|---|---|---|
| **Stage 1** | 메인 PDF를 SHP와 정합 → 좌표 부여 | `pdf_main_geo/{code}.{jpg,jgw,prj}` |
| **Stage 2** | 스캔 식별 — 헤더 OCR로 admin_code, 분할 PDF SIFT로 sheet_id | `_identification.csv`, `sheet_bboxes.json` |
| **Stage 3** | 스캔 ↔ 메인 PDF SIFT + 호모그래피 워핑 | `warped/{code}/{code}_{sheet}/{code}_{sheet}.jpg` |
| **Stage 4** | 사분면 크롭 + 모자이크 병합 | `merged/{code}_scan_merged.jpg` |
| **Stage 5** | 경계 검수 (오렌지 마스크 vs SHP) | `validation/{code}_report` |

## 입력 파일 컨벤션

- **메인 PDF**: `{8자리 행정코드}.pdf` (예: `22520317.pdf`)
- **분할 PDF**: `{8자리}_{N}-{i}.pdf` (예: `22520317_4-1.pdf`)
- **스캔 이미지**: 자유 (재귀 검색, 헤더 OCR로 식별)
- **SHP**: 행정경계 (`bnd_adm_pg.shp` 등)

## 설치

### 1단계. 의존성 설치

OSGeo4W Shell 또는 conda 환경에서:

```bash
pip install -r requirements.txt
# Tesseract OCR (시스템 바이너리)
conda install -c conda-forge tesseract  # Linux/Mac
# Windows: install.bat 실행
```

### 2단계. 플러그인 등록

`gis_scan_tools` 폴더를 QGIS 플러그인 디렉토리에 복사:
- `C:\Users\{사용자}\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\` (Windows)
- `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` (Linux)

QGIS 재시작 → 플러그인 관리 → "GIS Scan Tools" 활성화

### 3단계. 환경변수 (CLI 사용 시)

QGIS 외부에서 stage 모듈을 직접 실행할 때는 PROJ 데이터 경로 필요:

```bash
export PROJ_DATA=/opt/conda/envs/{env}/share/proj
export PROJ_LIB=$PROJ_DATA
```

QGIS 안에서는 자체 PROJ를 쓰므로 불필요.

## 사용법 (UI)

QGIS 툴바 → "GIS Scan Tools" 클릭 → 5-탭 다이얼로그.

각 탭에서:
1. 입력 폴더/파일 지정
2. 옵션 설정 (선택)
3. **실행** 버튼 → 백그라운드 처리, 로그 실시간 표시
4. 완료 시 상태 표 자동 로드 (OK=초록, FAIL=빨강)
5. **출력 폴더 열기** / **상태 CSV 열기**로 산출물 검토
6. 다음 탭으로 진행

오류 발생 시 그 단계만 단독 재실행 가능. 이전 단계 산출물은 그대로 사용.

## 사용법 (CLI)

각 stage는 독립 CLI 진입점:

```bash
cd /path/to/parent_of_gis_scan_tools

# Stage 1
python -m gis_scan_tools.tools.stage1_pdf_georef \
    --in pdf_input/ --shp bnd.shp --out pdf_main_geo/

# Stage 2
python -m gis_scan_tools.tools.stage2_scan_identify \
    --in scan/ --pdf-input pdf_input/ \
    --pdf-main pdf_main_geo/ --out scan_identified/

# Stage 3
python -m gis_scan_tools.tools.stage3_scan_warp \
    --identification scan_identified/_identification.csv \
    --pdf-main pdf_main_geo/ --out warped/

# Stage 4
python -m gis_scan_tools.tools.stage4_merge \
    --warped warped/ \
    --sheet-bboxes scan_identified/sheet_bboxes.json \
    --pdf-main pdf_main_geo/ --out merged/

# Stage 5
python -m gis_scan_tools.tools.stage5_validate \
    --merged merged/ --shp bnd.shp --out validation/
```

## 처리 시간 (참고)

- Stage 1: 메인 PDF당 ~30초
- Stage 2: 스캔당 1~10초 (OCR variant 수에 따라)
- Stage 3: 메인 SIFT 1회 ~30초 + 시트당 ~15초
- Stage 4: 행정코드당 수초
- 4시트 1세트 전체: 약 2-3분 (단일 스레드)

만장 스케일은 다중 프로세스 병렬화 권장 (4-8시간).

## 주요 산출 파일

```
pdf_main_geo/
├── 22520317.jpg, .jgw, .prj    ← 좌표 부여된 메인 PDF
├── 22520317_gcp.vrt
└── _status.csv

scan_identified/
├── _identification.csv          ← 스캔 → (admin_code, sheet_id) 매핑
├── sheet_bboxes.json            ← admin별 시트별 world bbox
└── _sheet_cache/                ← 분할 PDF 렌더 캐시 (재실행 시 재사용)

warped/
└── 22520317/22520317_4-1/
    ├── 22520317_4-1.jpg, .jgw, .prj   ← 좌표 부여된 워핑 스캔
    ├── 02_scan_raw.jpg                 ← 중간산출 (검수용)
    ├── 03_scan_prep.jpg
    ├── 04_matches_inliers.jpg
    ├── 05_warped_scan.jpg
    └── status.json

merged/
├── 22520317_scan_merged.jpg, .jgw, .prj   ← 최종 모자이크
└── 22520317_status.json
```

## 트러블슈팅

- **Stage 1 cost > 2px**: 메인 PDF 정합 정확도 낮음. SHP 좌표계/버전 확인
- **Stage 2 OCR 실패**: `--copy-unmatched`로 격리 후 수동 검토
- **Stage 3 inliers < 30**: 스캔 품질 또는 PDF 스케일 차이 큰 경우. 잔차 확인
- **Stage 4 누락 시트**: 상태 JSON의 `skipped` 필드 확인

## 라이선스 / 기여

(생략)
