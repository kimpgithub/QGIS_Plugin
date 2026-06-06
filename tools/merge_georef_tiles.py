#!/usr/bin/env python3
"""이미 정합(georeferenced)된 분할 타일을 하나의 병합본으로 합성.

수동(또는 서버사이드) 정합으로 만들어진 GeoTIFF/월드파일 타일들을 입력받아
공통 격자에 재투영·합성한 단일 JPG + JGW + PRJ 를 생성한다. (stage_virtual_merge
의 SHP 정합 산출물과 유사한 병합본을, '정합 단계 없이' 만든다.)

전제:
    - 입력 타일은 모두 동일 CRS(EPSG:5179)로 이미 정합됨 (north-up 권장).
    - 흰 여백(스캔 바깥)은 near-white(RGB min > 245) → 합성 시 투명 취급.

연산최적화:
    - 풋프린트 한정 재투영 — 타일마다 전체 캔버스가 아닌 자기 윈도우만 reproject.
      (캔버스 N장 풀-리샘플 대비 메모리·시간 ~1/N)
    - 출력 버퍼 1회 할당(uint8), 타일 윈도우 버퍼만 순회 재사용 후 즉시 del.
    - bilinear 단일 패스 (입력이 이미 워핑됨 → 고차 보간 불필요).
    - near-white 마스크는 uint8 min-reduce + bool, float 미사용.

사용:
    python tools/merge_georef_tiles.py --in 0605/made \
        --out 0605/37570360_scan_merged.jpg [--ps 0.5604] [--quality 90]
"""
import argparse
import glob
import os
import sys
import time

import numpy as np

try:
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_origin
except ImportError:
    sys.stderr.write('rasterio 필요: pip install rasterio\n')
    raise

import cv2

PRJ_5179 = ('PROJCS["Korea_2000_Korea_Unified_Coordinate_System",'
            'GEOGCS["GCS_Korea_2000",DATUM["D_Korea_2000",'
            'SPHEROID["GRS_1980",6378137.0,298.257222101]],'
            'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
            'PROJECTION["Transverse_Mercator"],'
            'PARAMETER["False_Easting",1000000.0],'
            'PARAMETER["False_Northing",2000000.0],'
            'PARAMETER["Central_Meridian",127.5],'
            'PARAMETER["Scale_Factor",0.9996],'
            'PARAMETER["Latitude_Of_Origin",38.0],UNIT["Meter",1.0]]')

WHITE_THRESH = 245   # RGB min 이 이 값 초과면 여백(투명) 취급


def _find_tiles(in_dir):
    """입력 폴더에서 정합 타일 후보 수집. _modified.tif 우선, 없으면 *.tif/jpg."""
    tif_mod = sorted(glob.glob(os.path.join(in_dir, '*_modified.tif')))
    if tif_mod:
        return tif_mod
    tifs = sorted(glob.glob(os.path.join(in_dir, '*.tif')))
    if tifs:
        return tifs
    # 월드파일 동반 jpg (rasterio 가 jgw/aux.xml 로 georef 인식)
    return sorted(glob.glob(os.path.join(in_dir, '*.jpg')))


def _tile_meta(path):
    """타일 georef 메타: (minx, miny, maxx, maxy, ps_x, ps_y, w, h)."""
    with rasterio.open(path) as ds:
        b = ds.bounds
        t = ds.transform
        return (b.left, b.bottom, b.right, b.top,
                abs(t.a), abs(t.e), ds.width, ds.height)


def merge_tiles(in_dir, out_path, ps=None, quality=90, viz=True):
    t0 = time.time()
    tiles = _find_tiles(in_dir)
    if not tiles:
        raise SystemExit(f'입력 타일 없음: {in_dir}')
    print(f'[입력] {len(tiles)}개 타일')

    metas = [_tile_meta(p) for p in tiles]
    minx = min(m[0] for m in metas)
    miny = min(m[1] for m in metas)
    maxx = max(m[2] for m in metas)
    maxy = max(m[3] for m in metas)
    # 출력 픽셀크기: 미지정 시 입력 중앙값(타일별 미세차 흡수)
    if ps is None:
        ps = float(np.median([m[4] for m in metas] + [m[5] for m in metas]))
    out_w = int(round((maxx - minx) / ps))
    out_h = int(round((maxy - miny) / ps))
    dst_transform = from_origin(minx, maxy, ps, ps)
    dst_crs = rasterio.crs.CRS.from_epsg(5179)
    print(f'[격자] ps={ps:.5f}m  canvas={out_w}×{out_h}px  '
          f'bbox=[{minx:.0f},{miny:.0f},{maxx:.0f},{maxy:.0f}]')

    # 출력 버퍼 1회 할당 (흰 배경) — (bands, H, W)
    out = np.full((3, out_h, out_w), 255, np.uint8)

    for path, m in zip(tiles, metas):
        tminx, tminy, tmaxx, tmaxy = m[0], m[1], m[2], m[3]
        # 출력 픽셀 윈도우 (풋프린트 한정)
        c0 = max(0, int(np.floor((tminx - minx) / ps)))
        c1 = min(out_w, int(np.ceil((tmaxx - minx) / ps)))
        r0 = max(0, int(np.floor((maxy - tmaxy) / ps)))
        r1 = min(out_h, int(np.ceil((maxy - tminy) / ps)))
        ww, wh = c1 - c0, r1 - r0
        if ww <= 0 or wh <= 0:
            print(f'  [skip] {os.path.basename(path)} (윈도우 없음)')
            continue
        win_transform = from_origin(minx + c0 * ps, maxy - r0 * ps, ps, ps)

        win = np.full((3, wh, ww), 255, np.uint8)
        with rasterio.open(path) as ds:
            src = ds.read(indexes=[1, 2, 3])
            reproject(
                source=src, destination=win,
                src_transform=ds.transform, src_crs=ds.crs,
                dst_transform=win_transform, dst_crs=dst_crs,
                resampling=Resampling.bilinear,
                src_nodata=None, dst_nodata=255)
        del src

        # near-white(여백) 제외하고만 합성 → 인접 타일 여백이 본문 덮지 않음
        content = win.min(axis=0) <= WHITE_THRESH       # (wh, ww) bool
        sub = out[:, r0:r1, c0:c1]
        sub[:, content] = win[:, content]
        n_px = int(content.sum())
        print(f'  [합성] {os.path.basename(path)}  win={ww}×{wh}  '
              f'content={n_px/1e6:.1f}Mpx')
        del win, content, sub

    # 인코딩: (3,H,W)→(H,W,3) BGR, cv2 로 JPEG (한글경로 안전 tofile)
    bgr = np.ascontiguousarray(out[::-1].transpose(1, 2, 0))  # RGB→BGR
    del out
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    ok, buf = cv2.imencode('.jpg', bgr,
                           [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise RuntimeError('JPEG 인코딩 실패')
    buf.tofile(out_path)

    stem = os.path.splitext(out_path)[0]
    with open(stem + '.jgw', 'w') as f:
        f.write(f'{ps:.13f}\n0.0000000000000\n0.0000000000000\n'
                f'{-ps:.13f}\n{minx + ps / 2:.7f}\n{maxy - ps / 2:.7f}\n')
    with open(stem + '.prj', 'w') as f:
        f.write(PRJ_5179)

    if viz:
        sc = 1600.0 / max(bgr.shape[:2])
        prev = cv2.resize(bgr, None, fx=sc, fy=sc,
                          interpolation=cv2.INTER_AREA)
        vp = stem + '_preview.jpg'
        ok, b = cv2.imencode('.jpg', prev, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            b.tofile(vp)
        print(f'[미리보기] {vp} {prev.shape}')
    del bgr

    print(f'[완료] {out_path}  ({out_w}×{out_h})  {time.time()-t0:.1f}s')
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description='정합된 분할 타일 → 단일 병합본 (JPG+JGW+PRJ)')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='정합 타일 폴더 (_modified.tif 우선)')
    ap.add_argument('--out', required=True, help='출력 JPG 경로')
    ap.add_argument('--ps', type=float, default=None,
                    help='출력 픽셀크기(m). 미지정 시 입력 중앙값')
    ap.add_argument('--quality', type=int, default=90, help='JPEG 품질')
    ap.add_argument('--no-viz', action='store_true', help='미리보기 생략')
    args = ap.parse_args()
    merge_tiles(args.in_dir, args.out, ps=args.ps, quality=args.quality,
                viz=not args.no_viz)


if __name__ == '__main__':
    main()
