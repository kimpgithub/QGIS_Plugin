"""Stage 6: 병합 결과 COG 변환 + 서버 업로드 + cog_catalog 등록.

Stage 4 산출물({시도}/{시군구}/{admin}_scan_merged.jpg + .jgw)을 COG(Cloud
Optimized GeoTIFF)로 변환 → MinIO 업로드 → 서버 cog_catalog 등록.
검수 웹이 titiler 로 이 COG 를 타일 스트리밍한다.

서버 접속 설정은 QGIS 설정(QSettings)에서 로드 — 'DB 작업 > 서버 연결' 탭에서
저장한 값. 플러그인 프로세스 안에서 실행되므로 argv 로 시크릿을 넘기지 않는다.

CLI:
    python -m gis_scan_tools.tools.stage6_publish \\
        --merged 5_merged/ --out 7_published/
"""
import argparse
import csv
import glob
import os
import re
import time

try:
    from ..db_tools import api_client
except ImportError:                       # 표준 CLI 실행 경로
    from gis_scan_tools.db_tools import api_client


def jpg_to_cog(jpg_path, tif_path):
    """JGW 동반 JPG → COG GeoTIFF (EPSG:5179, JPEG 압축 + 오버뷰).

    GDAL 이 .jgw 월드파일을 자동 인식해 지오트랜스폼을 잡고, outputSRS 로
    좌표계만 부여(-a_srs, 재투영 아님).
    """
    from osgeo import gdal
    gdal.UseExceptions()
    gdal.Translate(
        tif_path, jpg_path,
        format='COG',
        outputSRS='EPSG:5179',
        creationOptions=['COMPRESS=JPEG', 'QUALITY=85', 'BLOCKSIZE=512',
                         'OVERVIEW_RESAMPLING=AVERAGE'],
    )


def cog_bounds(tif_path):
    """COG 의 (bounds[xmin,ymin,xmax,ymax], width, height) — EPSG:5179."""
    from osgeo import gdal
    ds = gdal.Open(tif_path)
    gt = ds.GetGeoTransform()
    w, h = ds.RasterXSize, ds.RasterYSize
    xmin, ymax = gt[0], gt[3]
    xmax = xmin + gt[1] * w
    ymin = ymax + gt[5] * h
    ds = None
    return [xmin, ymin, xmax, ymax], w, h


def publish_one(jpg_path, merged_root, out_dir, cfg):
    """admin 1건 — COG 변환 + 업로드 + 등록. result dict 반환."""
    name = os.path.basename(jpg_path)
    m = re.match(r'(\d{8})', name)
    if not m:
        return {'admin_code': '', 'status': 'ERROR',
                'message': f'파일명에서 admin code 추출 실패: {name}'}
    admin = m.group(1)
    jgw = os.path.splitext(jpg_path)[0] + '.jgw'
    if not os.path.exists(jgw):
        return {'admin_code': admin, 'status': 'ERROR',
                'message': f'JGW 없음: {jgw}'}

    rel = os.path.relpath(os.path.dirname(jpg_path), merged_root)
    rel = '' if rel == '.' else rel.replace('\\', '/')

    tif_dir = os.path.join(out_dir, rel) if rel else out_dir
    os.makedirs(tif_dir, exist_ok=True)
    tif_path = os.path.join(tif_dir, f'{admin}.tif')

    jpg_to_cog(jpg_path, tif_path)
    bounds, w, h = cog_bounds(tif_path)

    key = f'cog/{rel}/{admin}.tif' if rel else f'cog/{admin}.tif'
    api_client.upload_s3(cfg, tif_path, key, content_type='image/tiff')
    api_client.register_cog(cfg, admin, key, bounds=bounds, width=w, height=h)
    return {'admin_code': admin, 'status': 'OK', 'message': key}


def main():
    ap = argparse.ArgumentParser(
        description='Stage 6: 병합 COG 변환 + 서버 업로드')
    ap.add_argument('--merged', required=True, help='Stage 4 병합 출력 폴더')
    ap.add_argument('--out', dest='out_dir', required=True,
                    help='COG 산출 폴더 (로컬 보관본)')
    args = ap.parse_args()

    cfg = api_client.load_config()
    if not cfg.base_url or not cfg.s3_access_key:
        raise SystemExit('서버 설정 없음 — [DB 작업 > 서버 연결] 탭에서 '
                         'URL/토큰/S3 키를 저장하세요.')

    os.makedirs(args.out_dir, exist_ok=True)
    targets = sorted(glob.glob(os.path.join(
        args.merged, '**', '*_scan_merged.jpg'), recursive=True))
    print(f'[Stage 6] 대상 {len(targets)}장 → {cfg.s3_endpoint}')

    csv_path = os.path.join(args.out_dir, '_status.csv')
    ok = err = 0
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        wr = csv.writer(f)
        wr.writerow(['admin_code', 'status', 'message', 'elapsed_s'])
        for i, jpg in enumerate(targets, 1):
            t0 = time.time()
            print(f'\n[{i}/{len(targets)}] {os.path.basename(jpg)}')
            try:
                r = publish_one(jpg, args.merged, args.out_dir, cfg)
            except Exception as e:
                r = {'admin_code': '', 'status': 'ERROR', 'message': str(e)}
            el = f'{time.time() - t0:.1f}'
            wr.writerow([r.get('admin_code', ''), r['status'],
                         r.get('message', ''), el])
            if r['status'] == 'OK':
                ok += 1
                print(f'  OK  {r.get("message", "")}  ({el}s)')
            else:
                err += 1
                print(f'  ERROR  {r.get("message", "")}')

    print(f'\n[Stage 6] 완료 — OK {ok}, ERROR {err}')


if __name__ == '__main__':
    main()
