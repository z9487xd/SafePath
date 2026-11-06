"""Builds the occupational-risk caches in data/opendata/ from Ministry of Labour open data.

    python -X utf8 tools/build_risk_cache.py

Three datasets, each published as one CSV per year on apiservice.mol.gov.tw:

    41513  災害類型按行業分          injuries by hazard type and industry
    41515  災害類型與媒介物之關係    injuries by hazard type and agent (all industries)
    41522  初查不合格情形按行業分    failed first inspections by industry and legal item

The dashboard reads the aggregated JSON, never the CSV. To move to a newer year,
change YEAR_LABEL and the three download ids below (the dataset pages list them),
then rerun this script.
"""

import csv
import io
import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../data/opendata"))
DOWNLOAD = "https://apiservice.mol.gov.tw/OdService/download/"
PUBLISHER = "勞動部職業安全衛生署"
YEAR_LABEL = "114年"

SOURCES = {
    "injury_by_industry": ("41513", "職業災害統計災害類型按行業分(年度)", "A17000000J-030013-T3C"),
    "injury_by_agent": ("41515", "職業災害統計災害類型與媒介物之關係按全產業分(年度)", "A17000000J-030015-Nnu"),
    "inspection_failures": ("41522", "職業安全衛生檢查初查不合格情形按行業分(年度)", "A17000000J-030022-QGu"),
}

# Failed-inspection items kept per industry; the rest is long tail nobody reads.
TOP_ITEMS = 10


def fetch_csv(file_id):
    # apiservice.mol.gov.tw serves a certificate with no Subject Key Identifier, which
    # Python 3.13+ rejects under VERIFY_X509_STRICT. Only that strictness is relaxed:
    # the chain and the hostname are still verified.
    context = ssl.create_default_context()
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    with urllib.request.urlopen(DOWNLOAD + file_id, timeout=30, context=context) as response:
        raw = response.read()
    for encoding in ("utf-8-sig", "big5"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"{file_id}: neither UTF-8 nor Big5")
    return list(csv.DictReader(io.StringIO(text)))


def count(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def by_industry(rows):
    out = {}
    for row in rows:
        out.setdefault(row["行業別"], {})[row["災害類型"]] = count(row["人數（數量）"])
    return out


def by_agent(rows):
    out = {}
    for row in rows:
        n = count(row["人次"])
        if n:
            out.setdefault(row["災害類型"], {})[f"{row['分類']}／{row['媒介物項目']}"] = n
    return out


def inspection(rows):
    out = {}
    for row in rows:
        entry = out.setdefault(row["行業別"], {"failed_sites": 0, "items": []})
        sites, items = count(row["不合格廠次（數量）"]), count(row["不合格項數（數量）"])
        name = row["違反項目名稱"]
        if name.endswith("計"):
            # The law-level subtotal rows. Only the first of them is the overall
            # figure for the Act itself; the rest are other statutes.
            if row["法條名稱"].startswith("職業安全衛生法第6條"):
                entry["failed_sites"] = sites
            continue
        if sites:
            entry["items"].append({"law": row["法條名稱"], "item": name, "failed_sites": sites, "failed_items": items})
    for entry in out.values():
        entry["items"].sort(key=lambda i: -i["failed_sites"])
        # Fire and explosion items stay even when they fall outside the top list: they
        # are the ones this project's alerts refer to.
        keep = entry["items"][:TOP_ITEMS]
        keep += [i for i in entry["items"][TOP_ITEMS:] if "火" in i["item"] or "爆" in i["item"]]
        entry["items"] = keep
    return out


BUILDERS = {"injury_by_industry": by_industry, "injury_by_agent": by_agent, "inspection_failures": inspection}


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for key, (dataset_id, title, file_id) in SOURCES.items():
        rows = fetch_csv(file_id)
        records = BUILDERS[key](rows)
        payload = {
            "dataset_name": title,
            "dataset_id": dataset_id,
            "publisher": PUBLISHER,
            "year": YEAR_LABEL,
            "source_url": "",
            "source_files": [DOWNLOAD + file_id],
            "retrieved_at": now,
            "note": f"{YEAR_LABEL} CSV 彙整；由 tools/build_risk_cache.py 產生",
            "records": records,
        }
        path = os.path.join(OUT_DIR, f"{key}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print(f"{key}: {len(rows)} csv rows -> {len(records)} groups, {os.path.getsize(path)} bytes")


if __name__ == "__main__":
    sys.exit(main())
