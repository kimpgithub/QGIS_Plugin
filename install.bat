@echo off
chcp 65001 >nul

REM === OSGeo4W 환경 자동 탐색 ===
REM 이미 OSGeo4W 환경이면 바로 설치 진행
if defined OSGEO4W_ROOT goto :do_install

REM QGIS 설치 경로 탐색
set "OSGEO_ENV="
for %%P in (
    "C:\Program Files\QGIS 3.42.1"
    "C:\Program Files\QGIS 3.40.5"
    "C:\Program Files\QGIS 3.40.4"
    "C:\Program Files\QGIS 3.38.3"
    "C:\Program Files\QGIS 3.36.3"
    "C:\Program Files\QGIS 3.34.15"
    "C:\Program Files\QGIS 3.34.14"
    "C:\OSGeo4W"
) do (
    if exist "%%~P\OSGeo4W.bat" (
        set "OSGEO_ENV=%%~P\OSGeo4W.bat"
        goto :found
    )
)

REM 와일드카드로 재탐색
for /d %%D in ("C:\Program Files\QGIS*") do (
    if exist "%%D\OSGeo4W.bat" (
        set "OSGEO_ENV=%%D\OSGeo4W.bat"
        goto :found
    )
)

echo QGIS/OSGeo4W 설치를 찾을 수 없습니다.
echo OSGeo4W Shell에서 직접 이 파일을 실행하세요.
pause
exit /b 1

:found
echo OSGeo4W 환경 발견: %OSGEO_ENV%
echo OSGeo4W 환경을 로드하고 설치를 진행합니다...
echo.

REM OSGeo4W 환경을 로드한 뒤 이 스크립트의 :do_install로 재진입
call "%OSGEO_ENV%"
goto :do_install

:do_install
echo ============================================
echo  GIS Scan Tools - 의존성 설치
echo ============================================
echo.

REM === Python 패키지 설치 ===
echo [1/3] Python 패키지 설치 중...
python -m pip install --quiet psycopg2-binary pytesseract opencv-python numpy geopandas shapely rasterio scipy matplotlib koreanize-matplotlib Pillow
if %errorlevel% equ 0 (
    echo Python 패키지 설치 완료!
) else (
    echo Python 패키지 설치 중 오류 발생. 로그를 확인하세요.
)
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
