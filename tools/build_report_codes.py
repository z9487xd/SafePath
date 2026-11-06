"""Builds the occupational-injury report code tables in data/opendata/.

    python -X utf8 tools/build_report_codes.py

Four Ministry of Labour datasets, the code lists of the 職業災害統計網路填報系統:

    41471  災害類型分類說明表
    41469  受傷部位代號表
    41470  媒介物分類表
    41467  失能傷害種類代號表

The dashboard's report-summary form offers exactly these codes, so what it prints uses
the vocabulary the national reporting system uses.
"""

import csv
import io
import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../data/opendata"))
DOWNLOAD = "https://apiservice.mol.gov.tw/OdService/download/"
PUBLISHER = "勞動部職業安全衛生署"

# key -> (dataset id, title, download id)
SOURCES = {
    "report_disaster_types": ("41471", "職業災害統計網路填報系統災害類型分類說明表", "A17000000J-030008-xLQ"),
    "report_injury_parts": ("41469", "職業災害統計網路填報系統受傷部位代號表", "A17000000J-030006-sNo"),
    "report_agents": ("41470", "職業災害統計網路填報系統媒介物分類表", "A17000000J-030007-syk"),
    "report_disability": ("41467", "職業災害統計網路填報系統失能傷害種類代號表", "A17000000J-030005-sJp"),
}


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
            return list(csv.DictReader(io.StringIO(raw.decode(encoding))))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{file_id}: neither UTF-8 nor Big5")


def clean_desc(text):
    # Some explanations in the source file run across several CSV lines, and the line
    # break leaves the next row's sequence number behind. Fold those fragments back
    # into one running text; the wording itself is left untouched.
    text = re.sub(r"[,，]?\s*\n\d+,", " ", text.strip())
    return text.rstrip(",，").strip()


def disaster_types(rows):
    return [{"code": r["分類號碼（代碼）"].strip(), "name": r["分類項目"].strip(), "desc": clean_desc(r["說明"])} for r in rows]


def injury_parts(rows):
    return [{"code": r["受傷部位代號（代碼）"].strip(), "name": r["受傷部位"].strip()} for r in rows]


def agents(rows):
    return [{"code": r["媒介物編號"].strip(), "group": r["分類主項目"].strip(),
             "subgroup": r["分類細項目"].strip(), "name": r["媒介物"].strip()} for r in rows]


def disability(rows):
    return [{"code": r["失能傷害種類代號"].strip(), "name": r["失能傷害種類"].strip()} for r in rows]


BUILDERS = {"report_disaster_types": disaster_types, "report_injury_parts": injury_parts,
            "report_agents": agents, "report_disability": disability}


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for key, (dataset_id, title, file_id) in SOURCES.items():
        rows = fetch_csv(file_id)
        records = [r for r in BUILDERS[key](rows) if r["code"] and r["name"]]
        payload = {
            "dataset_name": title,
            "dataset_id": dataset_id,
            "publisher": PUBLISHER,
            "source_url": "",
            "source_files": [DOWNLOAD + file_id],
            "retrieved_at": now,
            "note": "由 tools/build_report_codes.py 產生",
            "records": records,
        }
        path = os.path.join(OUT_DIR, f"{key}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print(f"{key}: {len(rows)} csv rows -> {len(records)} records")


if __name__ == "__main__":
    sys.exit(main())
