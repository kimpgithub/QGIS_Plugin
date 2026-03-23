# GIS Scan Tools - QGIS 플러그인

스캔된 지도 이미지의 좌표 변환, 병합, 경계 검수 등을 수행하는 QGIS 플러그인입니다.

## 기능

### 1. 경계 검수
- 스캔 이미지에서 경계선 자동 추출
- SHP 파일 경계와 비교
- 시각화 결과 출력

### 2. 이미지 속성 검증
- 파일 손상 여부 확인
- 해상도(DPI) 검사
- 이미지 크기 검사
- 파일 용량 검사

### 3. 분할도 좌표 변환
- 메인 이미지(좌표 있음) 기준으로 분할도에 자동 좌표 부여
- 특징점 매칭 + 멀티스케일 방식
- JGW 파일 자동 생성

### 4. 분할도 병합
- 분할도들을 하나의 이미지로 병합
- 테두리 간격 조절 가능
- 좌표 정보(JGW) 자동 생성

## 설치 방법

1. `gis_scan_tools` 폴더를 QGIS 플러그인 디렉토리에 복사:
   - Windows: `C:\Users\{사용자}\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`

2. QGIS 재시작

3. 메뉴 → 플러그인 → 플러그인 관리 및 설치 → "GIS Scan Tools" 활성화

## 의존성

- Python 3.8+
- OpenCV (cv2)
- NumPy
- GeoPandas
- Shapely
- Pillow (PIL)
- easyocr (경계 검수 OCR용, 선택)

## 사용법

1. QGIS 툴바에서 "GIS Scan Tools" 아이콘 클릭
2. 원하는 기능 탭 선택
3. 입력 파일/폴더 설정
4. 실행 버튼 클릭

## 파일 구조

```
gis_scan_tools/
├── __init__.py          # 플러그인 초기화
├── metadata.txt         # 메타데이터
├── plugin.py            # 메인 플러그인 클래스
├── README.md            # 이 파일
├── tools/
│   ├── __init__.py
│   ├── boundary_validator.py      # 경계 검수
│   ├── image_validator.py         # 이미지 속성 검증
│   ├── subdivision_georeferencer.py  # 좌표 변환
│   └── subdivision_merger.py      # 분할도 병합
└── resources/
    └── icon.png         # 아이콘 (선택)
```

## 버전

- v1.0.0 (2026-02-11): 최초 릴리즈
