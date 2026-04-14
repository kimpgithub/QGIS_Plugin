"""Stage 5: 경계 검수 (오렌지/빨강 마스크 vs SHP)

Stage 4 병합 결과를 SHP와 비교하여 경계 정합 품질 리포트.

CLI:
  python -m gis_scan_tools.tools.stage5_validate \\
      --merged merged/ --shp bnd_adm_pg.shp --out validation/
"""
import argparse
import csv
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from gis_scan_tools.tools._legacy.boundary_validator import BoundaryValidator
    from gis_scan_tools.tools.stage2_scan_identify import check_tesseract
except ImportError:
    from .boundary_validator import BoundaryValidator
    from .stage2_scan_identify import check_tesseract


def _configure_pytesseract():
    """pytesseract가 tesseract.exe를 찾도록 경로 설정 (Windows 대응)."""
    cmd, err = check_tesseract()
    if cmd:
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = cmd
        except ImportError:
            pass
        # PATH에도 추가 (다른 subprocess 호출 대비)
        tess_dir = os.path.dirname(cmd)
        if tess_dir not in os.environ.get('PATH', ''):
            os.environ['PATH'] = tess_dir + os.pathsep + os.environ.get('PATH', '')


def main():
    ap = argparse.ArgumentParser(description='Stage 5: 경계 검수')
    ap.add_argument('--merged', required=True, help='Stage 4 산출 폴더')
    ap.add_argument('--shp', required=True)
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--threshold', type=float, default=8.0,
                    help='문제구간 픽셀 임계 (기본 8px)')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    _configure_pytesseract()

    targets = sorted(glob.glob(os.path.join(
        args.merged, '*_scan_merged.jpg')))
    print(f'[Stage 5] 검수 대상 {len(targets)}장')

    csv_path = os.path.join(args.out_dir, '_status.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['merged', 'status', 'message', 'elapsed_s'])
        for i, m in enumerate(targets, 1):
            t0 = time.time()
            print(f'\n[{i}/{len(targets)}] {os.path.basename(m)}')
            jgw = os.path.splitext(m)[0] + '.jgw'
            if not os.path.exists(jgw):
                w.writerow([m, 'ERROR', f'JGW 없음: {jgw}',
                            f'{time.time()-t0:.1f}'])
                print(f'  ERROR: JGW 없음 {jgw}')
                continue
            try:
                bv = BoundaryValidator(m, jgw, args.shp)
                # admin_code는 파일명에서 직접 (OCR 불필요)
                import re as _re
                mt = _re.match(r'(\d{8})', os.path.basename(m))
                if mt:
                    bv.admin_code = mt.group(1)
                    bv.extract_admin_code_ocr = lambda: bv.admin_code
                    print(f'  admin_code (filename): {bv.admin_code}')
                bv.run(output_dir=args.out_dir,
                       threshold_px=args.threshold)
                w.writerow([m, 'OK', '', f'{time.time()-t0:.1f}'])
            except Exception as e:
                w.writerow([m, 'ERROR', str(e), f'{time.time()-t0:.1f}'])
                print(f'  ERROR: {e}')

    print(f'\n[Stage 5] 완료')


if __name__ == '__main__':
    main()
