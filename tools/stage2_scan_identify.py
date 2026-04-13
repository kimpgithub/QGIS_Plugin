"""Stage 2: 스캔 식별 (행정코드 + 시트번호)

각 스캔의 (admin_code, sheet_id)를 자동 식별.

Tier A: 헤더 OCR로 admin_code 추출
Tier B: pHash + SIFT로 admin_code 식별 (Tier A 실패 시)
시트 식별: 그 admin의 분할 PDF들과 빠른 SIFT 매칭 → best inlier sheet 선택
   동시에 분할 PDF ↔ 메인 PDF 아핀 매칭으로 sheet world bbox 계산 (캐시)

CLI:
  python -m gis_scan_tools.tools.stage2_scan_identify \\
      --in scan/ --pdf-input pdf/ --pdf-main pdf_main_geo/ --out scan_identified/

산출:
  scan_identified/
    _identification.csv  (scan_path, status, admin_code, sheet_id, ...)
    sheet_bboxes.json    ({admin_code: {sheet_id: [minx,miny,maxx,maxy]}})
    _unmatched/          (짝 못찾은 스캔 격리)
"""
import argparse
import csv
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter

import cv2
import fitz
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from gis_scan_tools.tools._legacy.common import (
        parse_jgw, extract_map_region,
    )
except ImportError:
    from .common import parse_jgw, extract_map_region

SHEET_PATTERN = re.compile(r'^(\d{8})_(\d+)-(\d+)\.pdf$', re.IGNORECASE)


# ============================================================
# Tier A: 헤더 OCR (admin_code)
# ============================================================

def crop_header(img, ratio_h=0.18, ratio_w=0.40):
    h, w = img.shape[:2]
    return img[:int(h * ratio_h), :int(w * ratio_w)]


def _tesseract(img, config='--psm 6', lang='kor+eng'):
    cv2.imwrite('/tmp/_ocr.png', img)
    try:
        r = subprocess.run(
            ['tesseract', '/tmp/_ocr.png', '-', '-l', lang] + config.split(),
            capture_output=True, text=True, timeout=30)
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ''


def ocr_admin_code(scan_img, valid_codes=None, fast=False):
    hdr = crop_header(scan_img)
    g = cv2.cvtColor(hdr, cv2.COLOR_BGR2GRAY)

    variants = [('raw', hdr, '--psm 6', 'kor+eng')]
    if not fast:
        _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(('otsu', bw,
                         '--psm 6 -c tessedit_char_whitelist=0123456789()', 'eng'))
        bw2 = cv2.adaptiveThreshold(
            g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 31, 10)
        variants.append(('adaptive', bw2,
                         '--psm 11 -c tessedit_char_whitelist=0123456789()', 'eng'))
        big = cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, bw3 = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(('upscale', bw3, '--psm 6', 'kor+eng'))

    all_codes = []
    for _, im, cfg, lang in variants:
        text = _tesseract(im, cfg, lang)
        codes = re.findall(r'\d{8}', text)
        if valid_codes is not None:
            codes = [c for c in codes if c in valid_codes]
        all_codes.extend(codes)

    if not all_codes:
        return None, 0.0
    most, votes = Counter(all_codes).most_common(1)[0]
    return most, votes / max(1, len(variants))


# ============================================================
# pHash 폴백
# ============================================================

def compute_phash(img, hash_size=16):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = cv2.resize(g, (hash_size * 4, hash_size * 4),
                   interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(g)
    bits = (dct[:hash_size, :hash_size] >
            np.median(dct[:hash_size, :hash_size].flatten()[1:])).flatten()
    return np.packbits(bits.astype(np.uint8))


def hamming(a, b):
    return int(np.unpackbits(a ^ b).sum())


# ============================================================
# 시트 PDF 캐시 + sheet bbox 계산
# ============================================================

class SheetCache:
    """분할 PDF 렌더 + SIFT keypoint + 메인 정합 bbox 캐시."""

    def __init__(self, pdf_input_dir, pdf_main_dir,
                 sheet_match_scale=0.25):
        self.pdf_input_dir = pdf_input_dir
        self.pdf_main_dir = pdf_main_dir
        self.scale = sheet_match_scale
        self._sheet_meta = {}      # admin_code → {sheet_id: pdf_path}
        self._sheet_sift = {}      # pdf_path → (kp, des) at low-res
        self._main_sift = {}       # admin_code → (g_main_map, kp, des, main_bbox, main_jgw)
        self._sheet_world_bbox = {}  # admin_code → {sheet_id: (minx,miny,maxx,maxy)}
        self._scan_index_pdfs()

    def _scan_index_pdfs(self):
        for f in sorted(os.listdir(self.pdf_input_dir)):
            m = SHEET_PATTERN.match(f)
            if not m:
                continue
            admin = m.group(1)
            sid = f'{m.group(2)}-{m.group(3)}'
            self._sheet_meta.setdefault(admin, {})[sid] = os.path.join(
                self.pdf_input_dir, f)

    def admins_with_sheets(self):
        return list(self._sheet_meta.keys())

    def get_sheets(self, admin_code):
        return list(self._sheet_meta.get(admin_code, {}).items())

    def _render_pdf(self, pdf_path, dpi=300):
        cache_jpg = os.path.join('/tmp/_sheet_cache',
                                 os.path.basename(pdf_path) + '.jpg')
        os.makedirs('/tmp/_sheet_cache', exist_ok=True)
        if os.path.exists(cache_jpg):
            return cv2.imread(cache_jpg)
        doc = fitz.open(pdf_path)
        pix = doc[0].get_pixmap(dpi=dpi)
        pix.save(cache_jpg)
        doc.close()
        return cv2.imread(cache_jpg)

    def _preprocess(self, img, scale=1.0):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16, 16))
        g = clahe.apply(g)
        if scale != 1.0:
            g = cv2.resize(g, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(g, (0, 0), 0.8)

    def get_sheet_sift_lowres(self, pdf_path):
        """분할 PDF 저해상도(0.25x) SIFT keypoints — 스캔 매칭용."""
        if pdf_path in self._sheet_sift:
            return self._sheet_sift[pdf_path]
        img = self._render_pdf(pdf_path)
        sheet_map, _ = extract_map_region(img)
        g = self._preprocess(sheet_map, scale=self.scale)
        sift = cv2.SIFT_create(nfeatures=10000, contrastThreshold=0.03)
        kp, des = sift.detectAndCompute(g, None)
        self._sheet_sift[pdf_path] = (kp, des, g.shape)
        return self._sheet_sift[pdf_path]

    def get_main_sift_for_sheet_align(self, admin_code):
        """메인 PDF의 지도영역 SIFT (분할 PDF↔메인 정합용, 풀해상도)."""
        if admin_code in self._main_sift:
            return self._main_sift[admin_code]
        main_jpg = os.path.join(self.pdf_main_dir, f'{admin_code}.jpg')
        main_jgw = parse_jgw(os.path.join(self.pdf_main_dir,
                                          f'{admin_code}.jgw'))
        main_img = cv2.imread(main_jpg)
        main_map, main_bbox = extract_map_region(main_img)
        g = self._preprocess(main_map, scale=self.scale)
        sift = cv2.SIFT_create(nfeatures=20000, contrastThreshold=0.03)
        kp, des = sift.detectAndCompute(g, None)
        self._main_sift[admin_code] = (g, kp, des, main_bbox, main_jgw)
        return self._main_sift[admin_code]

    def compute_sheet_world_bbox(self, admin_code, sheet_id):
        """sheet PDF ↔ main PDF 아핀으로 sheet의 world bbox 계산. 캐시."""
        cached = self._sheet_world_bbox.get(admin_code, {}).get(sheet_id)
        if cached:
            return cached

        pdf_path = self._sheet_meta[admin_code][sheet_id]
        sheet_img = self._render_pdf(pdf_path)
        sheet_map, sheet_bbox = extract_map_region(sheet_img)

        g_sheet = self._preprocess(sheet_map, scale=self.scale)
        sift = cv2.SIFT_create(nfeatures=10000, contrastThreshold=0.03)
        kp_s, des_s = sift.detectAndCompute(g_sheet, None)

        g_main, kp_m, des_m, main_bbox, main_jgw = self.get_main_sift_for_sheet_align(
            admin_code)
        if des_s is None or des_m is None:
            return None

        matcher = cv2.FlannBasedMatcher(
            {'algorithm': 1, 'trees': 5}, {'checks': 50})
        pairs = matcher.knnMatch(des_s, des_m, k=2)
        good = [m for m, n in pairs if m.distance < 0.75 * n.distance]
        if len(good) < 50:
            return None

        src = np.float32([kp_s[m.queryIdx].pt for m in good]) / self.scale
        dst = np.float32([kp_m[m.trainIdx].pt for m in good]) / self.scale
        A, _ = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
        if A is None:
            return None

        # sheet_map 4 corners → main_map → main 전체 → world
        sh, sw = sheet_map.shape[:2]
        corners = np.float32([[0, 0], [sw, 0], [sw, sh], [0, sh]])
        m_corners = (A[:, :2] @ corners.T).T + A[:, 2]
        # main_map → main 전체
        mbx, mby = main_bbox[0], main_bbox[1]
        m_corners[:, 0] += mbx
        m_corners[:, 1] += mby
        # main → world
        wx = main_jgw.top_left_x + m_corners[:, 0] * main_jgw.pixel_size_x
        wy = main_jgw.top_left_y + m_corners[:, 1] * main_jgw.pixel_size_y
        bbox = (float(wx.min()), float(wy.min()),
                float(wx.max()), float(wy.max()))
        self._sheet_world_bbox.setdefault(admin_code, {})[sheet_id] = bbox
        return bbox


# ============================================================
# 스캔 → 시트 매칭
# ============================================================

def identify_sheet(scan_img, admin_code, sheet_cache, min_inliers=30):
    """스캔이 admin_code의 어느 분할 시트와 가장 잘 맞는지.

    Returns: (sheet_id, n_inliers) or (None, best_inl)
    """
    sheets = sheet_cache.get_sheets(admin_code)
    if not sheets:
        return None, 0

    # 스캔 저해상도 SIFT
    g = scan_img
    g = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY) if g.ndim == 3 else g
    g = cv2.resize(g, None, fx=sheet_cache.scale, fy=sheet_cache.scale,
                   interpolation=cv2.INTER_AREA)
    g = cv2.GaussianBlur(g, (0, 0), 0.8)
    sift = cv2.SIFT_create(nfeatures=10000, contrastThreshold=0.03)
    kp_s, des_s = sift.detectAndCompute(g, None)
    if des_s is None or len(kp_s) < 50:
        return None, 0

    matcher = cv2.FlannBasedMatcher(
        {'algorithm': 1, 'trees': 5}, {'checks': 50})

    best_id, best_inl = None, 0
    for sid, pdf_path in sheets:
        kp_p, des_p, _ = sheet_cache.get_sheet_sift_lowres(pdf_path)
        if des_p is None:
            continue
        try:
            pairs = matcher.knnMatch(des_s, des_p, k=2)
        except cv2.error:
            continue
        good = [m for m, n in pairs if m.distance < 0.75 * n.distance]
        if len(good) < 8:
            continue
        src = np.float32([kp_s[m.queryIdx].pt for m in good])
        dst = np.float32([kp_p[m.trainIdx].pt for m in good])
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if mask is None:
            continue
        inl = int(mask.sum())
        if inl > best_inl:
            best_inl, best_id = inl, sid

    return (best_id, best_inl) if best_inl >= min_inliers else (None, best_inl)


# ============================================================
# 통합 식별
# ============================================================

def identify_scan(scan_jpg, sheet_cache, valid_codes, fast_ocr=False):
    img = cv2.imread(scan_jpg)
    if img is None:
        return {'status': 'ERROR', 'admin_code': None, 'sheet_id': None,
                'method': 'LOAD_FAIL', 'message': 'cv2.imread 실패'}

    # admin_code 식별 (OCR)
    code, conf = ocr_admin_code(img, valid_codes=valid_codes, fast=fast_ocr)
    if code is None:
        return {'status': 'FAIL', 'admin_code': None, 'sheet_id': None,
                'method': 'OCR_FAIL',
                'message': 'OCR로 admin_code 추출 실패'}

    # sheet_id 식별 (SIFT)
    sid, inliers = identify_sheet(img, code, sheet_cache)
    if sid is None:
        return {'status': 'FAIL', 'admin_code': code, 'sheet_id': None,
                'method': 'OCR+SIFT',
                'message': f'admin OK but sheet 미식별 (best inl={inliers})',
                'confidence': conf}

    # sheet world bbox 계산 (캐시)
    bbox = sheet_cache.compute_sheet_world_bbox(code, sid)
    if bbox is None:
        return {'status': 'FAIL', 'admin_code': code, 'sheet_id': sid,
                'method': 'OCR+SIFT',
                'message': f'sheet bbox 계산 실패',
                'confidence': conf}

    return {'status': 'OK', 'admin_code': code, 'sheet_id': sid,
            'method': 'OCR+SIFT', 'confidence': conf,
            'sheet_inliers': inliers, 'message': ''}


# ============================================================
# CLI
# ============================================================

def main():
    ap = argparse.ArgumentParser(description='Stage 2: 스캔 식별 (admin+sheet)')
    ap.add_argument('--in', dest='in_dir', required=True,
                    help='스캔 폴더 (재귀)')
    ap.add_argument('--pdf-input', required=True,
                    help='원본 PDF 폴더 (메인+분할 함께)')
    ap.add_argument('--pdf-main', required=True,
                    help='Stage 1 산출 폴더 (pdf_main_geo)')
    ap.add_argument('--out', dest='out_dir', required=True)
    ap.add_argument('--fast', action='store_true', help='OCR 1-variant만')
    ap.add_argument('--copy-unmatched', action='store_true')
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    print(f'[Stage 2] 시트 캐시 초기화')
    cache = SheetCache(args.pdf_input, args.pdf_main)
    valid_codes = set(cache.admins_with_sheets())
    print(f'  → {len(valid_codes)}개 admin 코드 (분할 PDF 보유)')
    if not valid_codes:
        print('ERROR: 분할 PDF가 없음 (filename: {8자리}_{N}-{i}.pdf)')
        sys.exit(1)

    scans = sorted(set(
        glob.glob(os.path.join(args.in_dir, '**/*.jpg'), recursive=True)
        + glob.glob(os.path.join(args.in_dir, '**/*.JPG'), recursive=True)
    ))
    scans = [s for s in scans if 'checkpoint' not in s]
    print(f'[Stage 2] 스캔 {len(scans)}장 식별 시작')

    csv_path = os.path.join(args.out_dir, '_identification.csv')
    unmatched_dir = os.path.join(args.out_dir, '_unmatched')

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scan_path', 'status', 'admin_code', 'sheet_id',
                    'confidence', 'sheet_inliers', 'method',
                    'message', 'elapsed_s'])
        n_ok = n_fail = n_err = 0
        t0 = time.time()
        for i, scan in enumerate(scans, 1):
            ti = time.time()
            r = identify_scan(scan, cache, valid_codes, fast_ocr=args.fast)
            dt = time.time() - ti
            w.writerow([scan, r['status'], r.get('admin_code') or '',
                        r.get('sheet_id') or '',
                        f"{r.get('confidence', 0):.3f}",
                        r.get('sheet_inliers', ''), r['method'],
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
            if i % 5 == 0 or i == len(scans):
                print(f'  [{i}/{len(scans)}] OK={n_ok} FAIL={n_fail} '
                      f'ERR={n_err} ({(time.time()-t0)/i:.1f}s/장)')

    # sheet_bboxes.json 저장
    bbox_path = os.path.join(args.out_dir, 'sheet_bboxes.json')
    with open(bbox_path, 'w') as f:
        json.dump(cache._sheet_world_bbox, f, indent=2)

    print(f'\n[Stage 2] 완료: OK={n_ok}, FAIL={n_fail}, ERROR={n_err}')
    print(f'  CSV: {csv_path}')
    print(f'  bbox: {bbox_path}')


if __name__ == '__main__':
    main()
