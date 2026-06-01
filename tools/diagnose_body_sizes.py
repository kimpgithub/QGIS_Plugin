"""stage_extract_map 산출 body crop 크기 일관성 진단.

PDF-less 가상병합(`stage_virtual_merge`) 은 body 평균 크기로 강제 resize 한다.
시트별 body 크기 편차가 크면 그리드 배치 시 본문이 인접 시트와 어긋난다.
이 스크립트는 admin 별로 9-x body 사이즈를 비교하고, 평균 대비 편차가
큰 시트를 표시한다.

CLI:
    python -m gis_scan_tools.tools.diagnose_body_sizes \\
        --in 3_map_extracted/   [--admin 36570111] [--threshold 3.0]
"""
import argparse
import os
import re
import sys
import warnings
from collections import defaultdict


FILENAME_PAT = re.compile(r'^(\d{8})_(\d+)-(\d+)\.(jpg|jpeg|png)$', re.I)


def _read_size(path):
    """JPG/PNG 의 (width, height) 만 가볍게 읽기 (PIL 우선, 폴백 cv2).

    스캔 본문은 1억 픽셀 초과가 일상이라 PIL DecompressionBomb 경고 끔.
    """
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            return im.size  # (W, H)
    except Exception:
        pass
    try:
        import cv2
        img = cv2.imread(path)
        if img is None:
            return None
        h, w = img.shape[:2]
        return (w, h)
    except Exception:
        return None


def discover(in_dir):
    """{admin: {(N, i): [(path, w, h), ...]}} — 같은 시트가 여러 경로면 모두 보유."""
    by_admin = defaultdict(lambda: defaultdict(list))
    dups = []
    for root, _, files in os.walk(in_dir):
        if '.ipynb_checkpoints' in root.replace('\\', '/').split('/'):
            continue
        for f in sorted(files):
            m = FILENAME_PAT.match(f)
            if not m:
                continue
            admin = m.group(1)
            N, i = int(m.group(2)), int(m.group(3))
            path = os.path.join(root, f)
            size = _read_size(path)
            if size is None:
                continue
            w, h = size
            key = (N, i)
            by_admin[admin][key].append((path, w, h))
            if len(by_admin[admin][key]) > 1:
                dups.append((admin, f'{N}-{i}'))
    return by_admin, dups


def report_admin(admin, sheet_dict, threshold_pct):
    """단일 admin 의 N-x 비교 리포트. sheet_dict: {(N,i): [(path,w,h)...]}"""
    if not sheet_dict:
        return
    keys = sorted(sheet_dict.keys())
    # 대표 사이즈 — 중복이면 max 폭 선택 (보통 정상 본문이 더 큼)
    rep = {k: max(sheet_dict[k], key=lambda t: t[1]) for k in keys}
    Ns = sorted({k[0] for k in keys})
    print(f'\n=== {admin}  ({len(keys)}장, N={Ns})')
    ws = [rep[k][1] for k in keys]
    hs = [rep[k][2] for k in keys]
    mean_w = sum(ws) / len(ws)
    mean_h = sum(hs) / len(hs)
    print(f'  평균 body: {mean_w:.0f} x {mean_h:.0f}  '
          f'(min W={min(ws)} max W={max(ws)} / min H={min(hs)} max H={max(hs)})')
    print(f'  {"sheet":>6}  {"W":>6} {"H":>6}   '
          f'{"ΔW%":>7} {"ΔH%":>7}   비고')
    flagged = []
    for k in keys:
        N, i = k
        path, w, h = rep[k]
        dw = (w - mean_w) / mean_w * 100
        dh = (h - mean_h) / mean_h * 100
        flag = ''
        if abs(dw) >= threshold_pct or abs(dh) >= threshold_pct:
            flag = f'⚠ {threshold_pct:g}% 초과'
            flagged.append(f'{N}-{i}')
        n_dup = len(sheet_dict[k])
        if n_dup > 1:
            flag = (flag + ' ' if flag else '') + f'(중복 {n_dup})'
        sid = f'{N}-{i}'
        print(f'  {sid:>6}  {w:>6} {h:>6}   '
              f'{dw:+7.1f} {dh:+7.1f}   {flag}')
    if flagged:
        print(f'  → 편차 큰 시트: {", ".join(flagged)}')
    else:
        print(f'  → 모든 시트 편차 < {threshold_pct:g}%')


def main():
    ap = argparse.ArgumentParser(
        description='stage_extract_map body crop 크기 일관성 진단')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='stage_extract_map 산출 루트 (재귀 스캔)')
    ap.add_argument('--admin', default='',
                    help='특정 admin code 만 보기 (8자리). 비우면 전체')
    ap.add_argument('--threshold', type=float, default=3.0,
                    help='평균 대비 편차 임계 %% (기본 3.0). 초과 시 ⚠ 마킹')
    args = ap.parse_args()

    if not os.path.isdir(args.in_dir):
        print(f'폴더 없음: {args.in_dir}', file=sys.stderr)
        sys.exit(1)

    warnings.filterwarnings('ignore')  # PIL DecompressionBomb 등 묵음
    by_admin, dups = discover(args.in_dir)
    if not by_admin:
        print('body crop 파일 없음 (파일명 규칙: {8자리}_{N}-{i}.jpg)',
              file=sys.stderr)
        sys.exit(2)

    targets = [args.admin] if args.admin else sorted(by_admin.keys())
    for adm in targets:
        sd = by_admin.get(adm)
        if not sd:
            print(f'\n=== {adm}  (해당 admin body 없음)')
            continue
        report_admin(adm, sd, args.threshold)

    if dups:
        n = len(set(dups))
        print(f'\n[참고] 같은 시트가 여러 경로에서 발견됨 — {n}건. '
              f'각 시트별 max 폭본을 대표로 사용. '
              f'실제 stage_extract_map 산출 폴더만 입력하면 중복 없음.')


if __name__ == '__main__':
    main()
