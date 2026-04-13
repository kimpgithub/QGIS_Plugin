@echo off
echo ============================================
echo  GIS Scan Tools - Install Dependencies
echo ============================================
echo  Run this from OSGeo4W Shell
echo ============================================
echo.

REM === 1. Python packages ===
echo [1/3] Installing Python packages...
pip install psycopg2-binary pytesseract opencv-python numpy geopandas shapely rasterio scipy matplotlib koreanize-matplotlib Pillow PyMuPDF scikit-image
echo.

REM === 2. Tesseract OCR ===
echo [2/3] Checking Tesseract OCR...

set "TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe"
if exist "%TESSERACT_PATH%" (
    echo Tesseract already installed.
    goto :kor_check
)

echo Tesseract not found. Downloading...
set "INSTALLER=%TEMP%\tesseract-installer.exe"
set "URL=https://github.com/tesseract-ocr/tesseract/releases/download/5.5.0/tesseract-ocr-w64-setup-5.5.0.20241111.exe"

curl -L -o "%INSTALLER%" "%URL%"
if not exist "%INSTALLER%" (
    echo Download failed. Install manually: %URL%
    goto :end
)

echo Download complete. Starting installer...
echo NOTE: Check "Korean" in "Additional language data"
echo.
"%INSTALLER%"

if not exist "%TESSERACT_PATH%" (
    echo Tesseract installation not completed.
    goto :end
)
echo Tesseract installed.

:kor_check
REM === 3. Korean language data ===
echo [3/3] Checking Korean language data...

set "KOR_DATA=C:\Program Files\Tesseract-OCR\tessdata\kor.traineddata"
if exist "%KOR_DATA%" (
    echo Korean data already installed.
    goto :end
)

echo Downloading Korean data...
curl -L -o "%KOR_DATA%" "https://github.com/tesseract-ocr/tessdata/raw/main/kor.traineddata"
if exist "%KOR_DATA%" (
    echo Korean data installed.
) else (
    echo Download failed.
)

:end
echo.
echo ============================================
echo  Done
echo ============================================
pause
