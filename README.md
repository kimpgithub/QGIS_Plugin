# GIS Scan Tools - QGIS Plugin

스캔된 지도 이미지의 좌표 생성, 분할도 병합, 경계 검수를 수행하는 QGIS 플러그인입니다.

## 주요 기능

- 좌표 생성 (FFT+Powell 지오레퍼런싱)
- 분할도 병합 (N분할 이미지 정합/병합)
- 파일명 관리 (OCR 기반 일괄 리네임)
- 결과 검수 (cost 기준 QA)
- 경계 검수 (오렌지 마스크 vs SHP 비교)
- 이미지 검증 (DPI, 손상 여부 확인)
- PostGIS 참조 레이어 자동 로드 (지적도, 건물, 도로)

## 설치 방법

### 1단계. install.bat 실행

1. 시작 메뉴에서 **OSGeo4W Shell** 검색하여 실행
2. 플러그인 폴더로 이동:
   ```
   cd C:\경로\gis_scan_tools
   ```
3. 설치 스크립트 실행:
   ```
   install.bat
   ```

자동으로 다음을 수행합니다:
- Python 패키지 설치 (psycopg2-binary, pytesseract, opencv-python 등)
- Tesseract OCR 미설치 시 다운로드 및 설치
- 한국어 OCR 언어 데이터 다운로드

### 2단계. 플러그인 설치

`gis_scan_tools` 폴더를 QGIS 플러그인 디렉토리에 복사:
- `C:\Users\{사용자}\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`

또는: QGIS 메뉴 > 플러그인 > ZIP에서 설치 > `gis_scan_tools.zip` 선택

### 3단계. 플러그인 활성화

QGIS 재시작 > 플러그인 > 플러그인 관리 및 설치 > "GIS Scan Tools" 활성화

### 4단계. PostGIS 설정 (선택사항)

좌표 생성 후 참조 레이어(지적도, 건물, 도로)를 자동으로 깔려면:

1. PostgreSQL + PostGIS 설치
2. 데이터베이스 생성 및 백업 복원:
   ```sql
   CREATE DATABASE census;
   \c census
   CREATE EXTENSION postgis;
   ```
   ```cmd
   pg_restore -h localhost -U postgres -d census "백업파일경로.backup"
   ```
3. 플러그인 좌표 생성 탭에서 "좌표 생성 후 참조 레이어 자동 로드" 체크 후 접속 정보 입력

## 사용법

1. QGIS 툴바에서 아이콘 클릭 또는 단축키 사용 (F1~F8: 편집 도구)
2. 원하는 기능 탭 선택
3. 입력 파일/폴더 설정
4. 실행 버튼 클릭
