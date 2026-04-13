"""Stage 1: PDF 메인 좌표 생성 (SHP 정합)

PDF 폴더의 모든 메인 PDF/JPG에 대해 SHP와 자동 정합하여 JGW/PRJ/VRT 생성.

CLI:
  python -m gis_scan_tools.tools.stage1_pdf_georef \\
      --in pdf_main/ --shp bnd_adm_pg.shp --out pdf_main_geo/

산출: pdf_main_geo/{code}.{jpg,jgw,prj,_gcp.vrt,_tps.vrt} + _status.csv
"""
import argparse
import csv
import glob
import os
import sys
import time

# 기존 georef 그대로 사용
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from gis_scan_tools.tools._legacy.shp_georeferencer import SHPGeoreferencer
except ImportError:
    from .shp_georeferencer import SHPGeoreferencer


def main():
    ap = argparse.ArgumentParser(description='Stage 1: PDF 메인 좌표 생성')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='메인 PDF/JPG 폴더 (재귀)')
    ap.add_argument('--shp', required=True, help='행정경계 SHP')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--cost-threshold', type=float, default=2.0,
                    help='실패 판정 cost 임계값(px)')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    # 입력 수집
    inputs = []
    for ext in ('*.pdf', '*.jpg', '*.jpeg', '*.tif', '*.tiff'):
        inputs += glob.glob(os.path.join(args.in_dir, '**', ext), recursive=True)
    inputs = sorted(set(p for p in inputs if 'checkpoint' not in p))
    print(f'[Stage 1] 입력 {len(inputs)}개 처리 시작')

    g = SHPGeoreferencer(args.shp)

    csv_path = os.path.join(args.out_dir, '_status.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['input', 'status', 'admin_code', 'cost_px',
                    'gcp_count', 'pixel_size', 'message', 'elapsed_s'])

        n_ok = n_fail = 0
        for i, src in enumerate(inputs, 1):
            t0 = time.time()
            print(f'\n[{i}/{len(inputs)}] {os.path.basename(src)}')
            try:
                r = g.georeference_image(src, args.out_dir)
                cost = float(r.get('cost', 999))
                status = 'OK' if cost <= args.cost_threshold else 'WARN'
                w.writerow([src, status, r.get('admin_code', ''),
                            f'{cost:.3f}', r.get('gcp_count', 0),
                            f'{float(r.get("pixel_size", 0)):.4f}',
                            '', f'{time.time()-t0:.1f}'])
                if status == 'OK':
                    n_ok += 1
                else:
                    n_fail += 1
                print(f'  → {status} cost={cost:.2f}px')
            except Exception as e:
                w.writerow([src, 'ERROR', '', '', '', '', str(e),
                            f'{time.time()-t0:.1f}'])
                n_fail += 1
                print(f'  → ERROR: {e}')

    print(f'\n[Stage 1] 완료: OK={n_ok}, FAIL/ERROR={n_fail}')
    print(f'  결과: {csv_path}')


if __name__ == '__main__':
    main()
