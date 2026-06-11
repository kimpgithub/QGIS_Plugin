#!/usr/bin/env python3
"""(2025농총)행정리현황 xlsx → ri_roster 적재용 CSV. stdlib 만 사용(openpyxl 불필요).

사용:
  python3 roster_xlsx_to_csv.py "data/(2025농총)행정리수, 행정리현황.xlsx" /tmp/ri_roster.csv

출력 CSV 컬럼(헤더 없음, ri_roster 순서):
  ctpv_cd,ctpv_nm,sgg_cd,sgg_nm,emd_cd,emd_nm,ri_nm,li_nm,li_cd,work_yn,remark
"""
import csv
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def col_idx(ref: str) -> int:
    s = re.match(r"[A-Z]+", ref).group()
    n = 0
    for c in s:
        n = n * 26 + (ord(c) - 64)
    return n - 1


def main():
    src, dst = sys.argv[1], sys.argv[2]
    z = zipfile.ZipFile(src)

    sst = []
    for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("a:si", NS):
        sst.append("".join(t.text or "" for t in si.iter(f"{M}t")))

    # '행정리현황' 시트의 실제 경로를 workbook + rels 로 해석
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rid = None
    for s in wb.iter(f"{M}sheet"):
        if s.get("name") == "행정리현황":
            rid = s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = next(r.get("Target") for r in rels if r.get("Id") == rid)
    sheet_path = "xl/" + target.lstrip("/")

    root = ET.fromstring(z.read(sheet_path))
    rows = []
    for r in root.findall(".//a:sheetData/a:row", NS):
        cells = {}
        for c in r.findall("a:c", NS):
            ref, t, v = c.get("r"), c.get("t"), c.find("a:v", NS)
            val = None
            if v is not None:
                val = v.text
                if t == "s":
                    val = sst[int(val)]
            cells[col_idx(ref)] = val
        maxc = max(cells) if cells else -1
        rows.append([cells.get(i) for i in range(maxc + 1)])

    header = rows[0]
    expected = ["CTPV_CD", "CTPV_NM", "SGG_CD", "SGG_NM", "EMD_CD", "EMD_NM",
                "RI_NM", "LI_NM", "LI_CD", "WORK_YN", "REMARK"]
    if [h.strip() if h else h for h in header[:11]] != expected:
        raise SystemExit(f"예상치 못한 헤더: {header}")

    out = 0
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in rows[1:]:
            r = (r + [None] * 11)[:11]
            if all(x in (None, "") for x in r):
                continue
            # 공백 트림, 빈문자열은 None(빈칸)으로
            r = [(x.strip() if isinstance(x, str) else x) or None for x in r]
            w.writerow(["" if x is None else x for x in r])
            out += 1
    print(f"wrote {out} rows -> {dst}")


if __name__ == "__main__":
    main()
