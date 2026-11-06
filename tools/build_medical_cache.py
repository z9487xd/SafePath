"""Builds the occupational-injury medical facility caches in data/opendata/.

    python -X utf8 tools/build_medical_cache.py

Four Ministry of Labour datasets, each one CSV on apiservice.mol.gov.tw:

    5957   職業傷病診治專責醫院名單
    31780  職業傷病診治網絡醫院名單
    44745  職災職能復健專責醫院名單
    6624   勞工體格及健康檢查認可醫療機構

The dashboard reads the normalised JSON, never the CSV, so it keeps working offline.
Contact-person names are dropped on purpose: the dashboard needs a number to call, and
nothing about this project needs an individual's name.
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

COUNTIES = [
    "臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市", "基隆市", "新竹市", "嘉義市",
    "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣",
    "臺東縣", "澎湖縣", "金門縣", "連江縣",
]

# key -> (dataset id, title, download id, category label shown on the dashboard)
SOURCES = {
    "occ_injury_clinics": ("5957", "職業傷病診治專責醫院名單", "A17000000J-000003-U9H", "診治專責"),
    "occ_injury_network": ("31780", "職業傷病診治網絡醫院名單", "A17040000J-000029-zYY", "診治網絡"),
    "rehab_hospitals": ("44745", "職災職能復健專責醫院名單", "A17000000J-030081-SrV", "復健專責"),
    "health_exam_clinics": ("6624", "勞工體格及健康檢查認可醫療機構", "A17000000J-020057-FyT", "健康檢查"),
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


def normalise(text):
    # Full-width digits and letters appear in the health-exam addresses.
    text = (text or "").strip()
    out = []
    for ch in text:
        code = ord(ch)
        out.append(chr(code - 0xFEE0) if 0xFF01 <= code <= 0xFF5E else ch)
    return "".join(out).replace("台", "臺")


def county_of(*candidates):
    for text in candidates:
        text = normalise(text)
        for county in COUNTIES:
            if text.startswith(county) or text.startswith(county[:2] + county[-1]):
                return county
        match = re.match(r"^(.{2}[縣市])", text)
        if match and match.group(1) in COUNTIES:
            return match.group(1)
    return ""


def phone(number, extension=""):
    number, extension = (number or "").strip(), (extension or "").strip()
    if extension and extension != "0" and "ext" not in number.lower():
        return f"{number} 分機 {extension}"
    return number


def record(name, county, address, tel, url=""):
    out = {"name": name.strip(), "county": county, "address": normalise(address), "phone": tel}
    if url and url.strip().startswith("http"):
        out["url"] = url.strip()
    return out


def clinics(rows):
    return [record(r["單位名稱"], county_of(r["地址"]), r["地址"], phone(r["聯絡電話"]), r.get("網址", "")) for r in rows]


def network(rows):
    return [record(r["單位名稱"], county_of(r["地址"]), r["地址"], phone(r["聯絡電話"]), r.get("網址", "")) for r in rows]


def rehab(rows):
    return [record(r["醫療機構名稱"], county_of(r["直轄市或省轄縣市"], r["地址"]), r["地址"], phone(r["市話"], r["分機"])) for r in rows]


def exam(rows):
    out = []
    for r in rows:
        item = record(r["醫療機構名稱"], county_of(r["縣市別"], r["醫療機構地址"]), r["醫療機構地址"],
                      phone(r["連絡電話"], r["分機號碼"]))
        item["approval"] = (r.get("認可類別及有效期限") or "").strip()
        out.append(item)
    return out


BUILDERS = {"occ_injury_clinics": clinics, "occ_injury_network": network, "rehab_hospitals": rehab, "health_exam_clinics": exam}


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for key, (dataset_id, title, file_id, label) in SOURCES.items():
        rows = fetch_csv(file_id)
        records = [r for r in BUILDERS[key](rows) if r["name"]]
        unplaced = [r["name"] for r in records if not r["county"]]
        payload = {
            "dataset_name": title,
            "dataset_id": dataset_id,
            "publisher": PUBLISHER,
            "category": label,
            "source_url": "",
            "source_files": [DOWNLOAD + file_id],
            "retrieved_at": now,
            "note": "由 tools/build_medical_cache.py 產生；已移除聯絡人姓名",
            "records": records,
        }
        path = os.path.join(OUT_DIR, f"{key}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print(f"{key}: {len(rows)} csv rows -> {len(records)} records, {len(unplaced)} without a county {unplaced[:3]}")


if __name__ == "__main__":
    sys.exit(main())
