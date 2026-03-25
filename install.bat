@echo off
chcp 65001 >nul
echo ============================================
echo  GIS Scan Tools - 의존성 설치
echo ============================================
echo.

REM === Python 패키지 설치 (OSGeo4W Shell 환경 기준) ===
echo [1/3] Python 패키지 설치 중...
pip install psycopg2-binary pytesseract opencv-python numpy geopandas shapely rasterio scipy matplotlib koreanize-matplotlib Pillow
echo.

REM === Tesseract OCR 설치 확인 ===
echo [2/3] Tesseract OCR 확인 중...

set "TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe"
if exist "%TESSERACT_PATH%" (
    echo Tesseract가 이미 설치되어 있습니다: %TESSERACT_PATH%
    goto :kor_check
)

echo Tesseract가 설치되어 있지 않습니다. 다운로드 중...

set "INSTALLER=%TEMP%\tesseract-installer.exe"
set "URL=https://github.com/tesseract-ocr/tesseract/releases/download/5.5.0/tesseract-ocr-w64-setup-5.5.0.20241111.exe"

REM curl로 다운로드
curl -L -o "%INSTALLER%" "%URL%"

if not exist "%INSTALLER%" (
    echo 다운로드 실패. 수동으로 설치하세요:
    echo %URL%
    goto :end
)

echo 다운로드 완료. 설치를 시작합니다...
echo (설치 창에서 "Additional language data" 항목에서 Korean 을 반드시 체크하세요)
echo.
"%INSTALLER%"

if exist "%TESSERACT_PATH%" (
    echo Tesseract 설치 완료!
) else (
    echo Tesseract 설치가 완료되지 않았습니다. 수동으로 설치하세요.
    goto :end
)

:kor_check
REM === 한국어 데이터 확인 ===
echo [3/3] 한국어 언어 데이터 확인 중...

set "KOR_DATA=C:\Program Files\Tesseract-OCR\tessdata\kor.traineddata"
if exist "%KOR_DATA%" (
    echo 한국어 언어 데이터가 설치되어 있습니다.
) else (
    echo 한국어 언어 데이터가 없습니다. 다운로드 중...
    curl -L -o "%KOR_DATA%" "https://github.com/tesseract-ocr/tessdata/raw/main/kor.traineddata"
    if exist "%KOR_DATA%" (
        echo 한국어 데이터 설치 완료!
    ) else (
        echo 다운로드 실패. 수동으로 설치하세요.
    )
)

:end
echo.
echo ============================================
echo  설치 완료
echo ============================================
pause
