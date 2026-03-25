# GIS Scan Tools - QGIS Plugin

Scanned map georeferencing, subdivision merging, boundary validation plugin for QGIS.

## Features

- Coordinate generation (FFT+Powell georeferencing)
- Subdivision merging (N-split image alignment)
- Filename management (OCR-based bulk rename)
- Result review (cost-sorted QA)
- Boundary validation (orange mask vs SHP comparison)
- Image validation (DPI, corruption check)
- PostGIS reference layer auto-loading (cadastral, buildings, roads)

## Installation

### Step 1. Run install.bat

1. Open **OSGeo4W Shell** (search "OSGeo4W" in Start menu)
2. Navigate to the plugin folder:
   ```
   cd C:\path\to\gis_scan_tools
   ```
3. Run the installer:
   ```
   install.bat
   ```

This will automatically:
- Install all required Python packages (psycopg2-binary, pytesseract, opencv-python, etc.)
- Download and install Tesseract OCR if not present
- Download Korean language data for OCR

### Step 2. Install the plugin

Copy `gis_scan_tools` folder to QGIS plugin directory:
- `C:\Users\{user}\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`

Or: QGIS Menu > Plugins > Install from ZIP > select `gis_scan_tools.zip`

### Step 3. Activate

Restart QGIS > Plugins > Manage and Install Plugins > enable "GIS Scan Tools"

### Step 4. PostGIS setup (optional)

For reference layer auto-loading after georeferencing:

1. Install PostgreSQL + PostGIS on local machine
2. Create database and restore backup:
   ```sql
   CREATE DATABASE census;
   \c census
   CREATE EXTENSION postgis;
   ```
   ```cmd
   pg_restore -h localhost -U postgres -d census "path\to\backup.backup"
   ```
3. In the plugin, check "PostGIS reference layer auto-load" and enter connection info

## Usage

1. Click toolbar icons or use keyboard shortcuts (F1-F8 for editing)
2. Select the desired tab
3. Set input files/folders
4. Click Run
