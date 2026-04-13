"""Stage 2: 스캔 식별 (행정코드 매핑)

각 스캔 이미지가 어느 PDF 메인(행정코드)에 속하는지 식별.

Tier A: 헤더 영역 OCR (다중 전처리 + 다수결 + valid_codes 화이트리스트)
Tier B: pHash로 후보 좁히기 (Tier A 실패 시)
Tier C: 저해상도 SIFT 검증 (Tier B 후보)

CLI:
  python -m gis_scan_tools.tools.stage2_scan_identify \\
      --in scan/ --pdf-main pdf_main_geo/ --out scan_identified/

산출:
  scan_identified/
    _identification.csv
    _unmatched/  (짝 못찾은 스캔 격리)
"""
import argparse
import csv
import glob
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter

import cv2
import numpy as np

# ============================================================
# Tier A: 헤더 OCR
# ============================================================

def crop_header(img, ratio_h=0.18, ratio_w=0.40):
    """좌상단 헤더 영역 추출."""
    h, w = img.shape[:2]
    return img[:int(h * ratio_h), :int(w * ratio_w)]


def _tesseract(img, config='--psm 6', lang='kor+eng'):
    """tesseract OCR. 성공시 stdout, 실패시 빈 문자열."""
    cv2.imwrite('/tmp/_ocr.png', img)
    try:
        r = subprocess.run(
            ['tesseract', '/tmp/_ocr.png', '-', '-l', lang] + config.split(),
            capture_output=True, text=True, timeout=30)
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ''


def ocr_admin_code(scan_img, valid_codes=None, fast=False):
    """헤더 영역에서 8자리 행정코드 추출.

    Args:
        scan_img: BGR 이미지
        valid_codes: 유효 행정코드 셋 (None이면 필터 안 함)
        fast: True면 1-variant만 시도 (속도)

    Returns:
        (code, confidence, method) — code=None이면 추출 실패
    """
    hdr = crop_header(scan_img)
    g = cv2.cvtColor(hdr, cv2.COLOR_BGR2GRAY)

    variants = []

    # variant 1: raw psm6 kor+eng
    variants.append(('raw', hdr, '--psm 6', 'kor+eng'))

    if not fast:
        # variant 2: otsu 이진화 + 숫자 화이트리스트
        _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(('otsu', bw, '--psm 6 -c tessedit_char_whitelist=0123456789()', 'eng'))

        # variant 3: adaptive threshold
        bw2 = cv2.adaptiveThreshold(
            g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 31, 10)
        variants.append(('adaptive', bw2, '--psm 11 -c tessedit_char_whitelist=0123456789()', 'eng'))

        # variant 4: 2x upscale + otsu
        big = cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, bw3 = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(('upscale', bw3, '--psm 6', 'kor+eng'))

    all_codes = []
    for name, im, cfg, lang in variants:
        text = _tesseract(im, cfg, lang)
        codes = re.findall(r'\d{8}', text)
        if valid_codes is not None:
            codes = [c for c in codes if c in valid_codes]
        all_codes.extend(codes)

    if not all_codes:
        return None, 0.0, 'OCR_FAIL'

    most, votes = Counter(all_codes).most_common(1)[0]
    confidence = votes / max(1, len(variants))
    return most, confidence, 'OCR'


# ============================================================
# Tier B: pHash 인덱스
# ============================================================

def compute_phash(img, hash_size=16):
    """간단한 perceptual hash. (hash_size×hash_size) 비트.

    cv2만 사용 (imagehash 의존성 회피).
    """
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = cv2.resize(g, (hash_size * 4, hash_size * 4),
                   interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(g)
    dct_low = dct[:hash_size, :hash_size]
    med = np.median(dct_low.flatten()[1:])  # DC 제외
    bits = (dct_low > med).flatten()
    return np.packbits(bits.astype(np.uint8))


def hamming(a, b):
    return int(np.unpackbits(a ^ b).sum())


def build_pdf_index(pdf_dir):
    """pdf_main_geo/{code}.jpg 들을 pHash 인덱싱.

    Returns:
        {admin_code: (phash_bytes, jpg_path, jgw_path)}
    """
    index = {}
    for jpg in sorted(glob.glob(os.path.join(pdf_dir, '*.jpg'))):
        base = os.path.splitext(os.path.basename(jpg))[0]
        if not re.match(r'^\d{8}$', base):
            continue
        jgw = os.path.splitext(jpg)[0] + '.jgw'
        if not os.path.exists(jgw):
            continue
        img = cv2.imread(jgw[:-4] + '.jpg')  # jpg 명시
        if img is None:
            img = cv2.imread(jpg)
        if img is None:
            continue
        ph = compute_phash(img)
        index[base] = (ph, jpg, jgw)
    return index


def identify_by_phash(scan_img, pdf_index, top_k=5):
    """pHash로 후보 행정코드 top_k 반환."""
    ph = compute_phash(scan_img)
    dists = [(code, hamming(ph, p)) for code, (p, _, _) in pdf_index.items()]
    dists.sort(key=lambda x: x[1])
    return [code for code, _ in dists[:top_k]]


# ============================================================
# Tier C: 저해상도 SIFT 검증
# ============================================================

def _sift_quick(img, max_dim=1200):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = g.shape
    if max(h, w) > max_dim:
        s = max_dim / max(h, w)
        g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    sift = cv2.SIFT_create(nfeatures=5000, contrastThreshold=0.04)
    return sift.detectAndCompute(g, None)


def verify_by_sift(scan_img, candidate_codes, pdf_index, min_inliers=30):
    """후보 PDF들과 빠른 SIFT 매칭. 최고 inlier 코드 반환."""
    kp1, des1 = _sift_quick(scan_img)
    if des1 is None:
        return None, 0
    matcher = cv2.FlannBasedMatcher({'algorithm': 1, 'trees': 5}, {'checks': 30})
    best_code, best_inl = None, 0
    for code in candidate_codes:
        pdf_jpg = pdf_index[code][1]
        pdf_img = cv2.imread(pdf_jpg)
        if pdf_img is None:
            continue
        kp2, des2 = _sift_quick(pdf_img)
        if des2 is None:
            continue
        try:
            pairs = matcher.knnMatch(des1, des2, k=2)
        except cv2.error:
            continue
        good = [m for m, n in pairs if len(pairs[0]) == 2 and m.distance < 0.75 * n.distance]
        if len(good) < 8:
            continue
        src = np.float32([kp1[m.queryIdx].pt for m in good])
        dst = np.float32([kp2[m.trainIdx].pt for m in good])
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if mask is None:
            continue
        inl = int(mask.sum())
        if inl > best_inl:
            best_inl, best_code = inl, code
    if best_inl >= min_inliers:
        return best_code, best_inl
    return None, best_inl


# ============================================================
# 통합 식별
# ============================================================

def identify_scan(scan_jpg, pdf_index, fast_ocr=False):
    """단일 스캔 식별. status, code, confidence, method 반환."""
    img = cv2.imread(scan_jpg)
    if img is None:
        return {'status': 'ERROR', 'code': None, 'confidence': 0.0,
                'method': 'LOAD_FAIL', 'message': 'cv2.imread 실패'}

    valid = set(pdf_index.keys())

    # Tier A
    code, conf, method = ocr_admin_code(img, valid_codes=valid, fast=fast_ocr)
    if code is not None:
        return {'status': 'OK', 'code': code, 'confidence': conf,
                'method': method, 'message': ''}

    # Tier B+C
    if not pdf_index:
        return {'status': 'FAIL', 'code': None, 'confidence': 0.0,
                'method': 'NO_PDF', 'message': 'PDF 인덱스 비어있음'}
    cands = identify_by_phash(img, pdf_index, top_k=5)
    code, inl = verify_by_sift(img, cands, pdf_index)
    if code is not None:
        return {'status': 'OK', 'code': code,
                'confidence': min(1.0, inl / 100.0),
                'method': 'PHASH_SIFT', 'message': f'inliers={inl}'}

    return {'status': 'FAIL', 'code': None, 'confidence': 0.0,
            'method': 'NO_MATCH',
            'message': f'OCR 실패, pHash top5도 SIFT 미통과 (best inl={inl})'}


# ============================================================
# CLI
# ============================================================

def main():
    ap = argparse.ArgumentParser(description='Stage 2: 스캔 식별')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='스캔 폴더 (재귀 .jpg 검색)')
    ap.add_argument('--pdf-main', required=True,
                    help='Stage 1 산출 폴더 (pdf_main_geo)')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--fast', action='store_true', help='OCR 1-variant만 (속도)')
    ap.add_argument('--copy-unmatched', action='store_true',
                    help='짝 못찾은 스캔을 _unmatched/에 복사')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    unmatched_dir = os.path.join(args.out_dir, '_unmatched')

    print(f'[Stage 2] PDF 인덱스 구축: {args.pdf_main}')
    pdf_index = build_pdf_index(args.pdf_main)
    print(f'  → {len(pdf_index)}개 행정코드')
    if not pdf_index:
        print('ERROR: PDF 인덱스 비어있음. Stage 1을 먼저 실행하세요.')
        sys.exit(1)

    scans = sorted(set(
        glob.glob(os.path.join(args.in_dir, '**/*.jpg'), recursive=True)
        + glob.glob(os.path.join(args.in_dir, '**/*.JPG'), recursive=True)
    ))
    scans = [s for s in scans if 'checkpoint' not in s]
    print(f'[Stage 2] 스캔 {len(scans)}장 식별 시작')

    csv_path = os.path.join(args.out_dir, '_identification.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scan_path', 'status', 'admin_code', 'confidence',
                    'method', 'message', 'elapsed_s'])

        n_ok = n_fail = n_err = 0
        t_total = time.time()
        for i, scan in enumerate(scans, 1):
            t0 = time.time()
            r = identify_scan(scan, pdf_index, fast_ocr=args.fast)
            dt = time.time() - t0
            w.writerow([scan, r['status'], r.get('code') or '',
                        f"{r['confidence']:.3f}", r['method'],
                        r['message'], f'{dt:.2f}'])
            if r['status'] == 'OK':
                n_ok += 1
            elif r['status'] == 'FAIL':
                n_fail += 1
                if args.copy_unmatched:
                    os.makedirs(unmatched_dir, exist_ok=True)
                    shutil.copy2(scan, os.path.join(
                        unmatched_dir, os.path.basename(scan)))
            else:
                n_err += 1
            if i % 10 == 0 or i == len(scans):
                print(f'  [{i}/{len(scans)}] OK={n_ok} FAIL={n_fail} ERR={n_err} '
                      f'({(time.time()-t_total)/i:.1f}s/장 평균)')

    print(f'\n[Stage 2] 완료: OK={n_ok}, FAIL={n_fail}, ERROR={n_err}')
    print(f'  결과: {csv_path}')


if __name__ == '__main__':
    main()
