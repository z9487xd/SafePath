"""Labour Ministry open data, cached on disk so the site works offline.

The three datasets here change on the order of once a year - an inspection office
moves, a phone number changes - and the one moment this project needs them most is
a fire, when the network is the first thing to go. So the cache is not a fallback
bolted onto a live query: the cache is the source the dashboard reads, and the
network is only how it gets refreshed.

    data/opendata/*.json          the cache, committed to the repo
    source_url in each file       where to refresh from, filled in by hand

`source_url` is deliberately empty. Nobody has verified the endpoint for these
dataset ids yet, and a guessed URL that 404s in front of a judge is worse than an
honest offline cache - so refresh() does nothing until a real URL is written in,
and freshness() says plainly that the data is unverified until then.

    from opendata_client import resources_payload
    payload = resources_payload()          # what /api/resources serves
"""

import os
import json
from typing import Dict, Any, List

base_dir = os.path.dirname(os.path.abspath(__file__))
cache_dir = os.path.abspath(os.path.join(base_dir, "../data/opendata"))

# Dashboard key -> cache file. The dashboard never sees file names.
DATASETS = {
    "inspection_hotlines": "inspection_hotlines.json",
    "case_service_contacts": "case_service_contacts.json",
    "elearning_courses": "elearning_courses.json",
    # Built by tools/build_risk_cache.py from the yearly CSVs, not refreshed from a URL.
    "injury_by_industry": "injury_by_industry.json",
    "injury_by_agent": "injury_by_agent.json",
    "inspection_failures": "inspection_failures.json",
    # Built by tools/build_medical_cache.py: lists of facilities, each tagged with its county.
    "occ_injury_clinics": "occ_injury_clinics.json",
    "occ_injury_network": "occ_injury_network.json",
    "rehab_hospitals": "rehab_hospitals.json",
    "health_exam_clinics": "health_exam_clinics.json",
    # Built by tools/build_report_codes.py: the code lists of the national injury-report system.
    "report_disaster_types": "report_disaster_types.json",
    "report_injury_parts": "report_injury_parts.json",
    "report_agents": "report_agents.json",
    "report_disability": "report_disability.json",
}
# Datasets whose records are aggregates; refresh() must never overwrite them with raw rows.
BUILT_DATASETS = {"injury_by_industry", "injury_by_agent", "inspection_failures",
                  "occ_injury_clinics", "occ_injury_network", "rehab_hospitals", "health_exam_clinics",
                  "report_disaster_types", "report_injury_parts", "report_agents", "report_disability"}
REPORT_CODE_DATASETS = ("report_disaster_types", "report_injury_parts", "report_agents", "report_disability")
MEDICAL_DATASETS = ("occ_injury_clinics", "occ_injury_network", "rehab_hospitals", "health_exam_clinics")

DEFAULT_INDUSTRY = "食品及飼品製造業"
FIRE_TYPE, EXPLOSION_TYPE, HEAT_TYPE = "火災", "爆炸", "與高溫、低溫之接觸"


REQUEST_TIMEOUT = 10.0

_cache: Dict[str, Dict[str, Any]] = {}
_mtimes: Dict[str, float] = {}


def _read(name: str) -> Dict[str, Any]:
    path = os.path.join(cache_dir, DATASETS[name])
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload.get("records"), (dict, list)):
        raise ValueError(f"{DATASETS[name]} has no records")
    return payload


# Re-read only what changed on disk, so editing a cache file takes effect without
# a restart. A file that fails to parse keeps the copy already in memory rather
# than emptying a panel mid-run.
def load_cache() -> Dict[str, Dict[str, Any]]:
    for name, filename in DATASETS.items():
        path = os.path.join(cache_dir, filename)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            if name not in _cache:
                print(f"[OpenData] missing {filename}, panel will be empty", flush=True)
            continue
        if _mtimes.get(name) == mtime:
            continue
        try:
            _cache[name] = _read(name)
            _mtimes[name] = mtime
            count = len(_cache[name]["records"])
            print(f"[OpenData] {filename}: {count} records", flush=True)
        except Exception as e:
            print(f"[OpenData Error] {filename} kept unchanged, parse failed: {e}", flush=True)
    return _cache


# Overwrites a cache file from its source_url, and leaves it alone on any failure.
# Returns what happened so a caller can report it instead of guessing.
def refresh(name: str) -> str:
    if name not in DATASETS:
        return f"unknown dataset {name}"

    if name in BUILT_DATASETS:
        return f"{name}: rebuilt with tools/build_risk_cache.py, not refreshed here"

    load_cache()
    payload = _cache.get(name)
    if payload is None:
        return f"{name}: no cache to refresh"

    url = (payload.get("source_url") or "").strip()
    if not url:
        return f"{name}: no source_url set, keeping offline cache"

    try:
        import requests
        from datetime import datetime, timezone

        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        records = response.json()
    except Exception as e:
        # The existing cache stays exactly as it was. Stale data that was once
        # correct beats an empty panel, and beats a half-written file.
        return f"{name}: refresh failed, keeping cache ({e})"

    if not isinstance(records, (dict, list)) or not records:
        return f"{name}: refresh returned nothing usable, keeping cache"

    payload["records"] = records
    payload["retrieved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    path = os.path.join(cache_dir, DATASETS[name])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)          # atomic, so a crash cannot truncate the cache
    _mtimes.pop(name, None)
    return f"{name}: refreshed, {len(records)} records"


def refresh_all() -> List[str]:
    return [refresh(name) for name in DATASETS]


# Per dataset: where it came from and whether that has been verified. The
# dashboard shows this, because "we cached it" and "we cannot cite it" are very
# different claims and only one of them is currently true.
def freshness() -> Dict[str, Dict[str, Any]]:
    load_cache()
    out = {}
    for name in DATASETS:
        payload = _cache.get(name, {})
        url = (payload.get("source_url") or "").strip()
        retrieved = (payload.get("retrieved_at") or "").strip()
        files = payload.get("source_files") or []
        out[name] = {
            "dataset_name": payload.get("dataset_name", ""),
            "dataset_id": payload.get("dataset_id", ""),
            "publisher": payload.get("publisher", ""),
            "source_url": url,
            "retrieved_at": retrieved,
            "source_files": files,
            "year": payload.get("year", ""),
            "verified": bool((url or files) and retrieved),
            "record_count": len(payload.get("records", [])),
            "note": payload.get("note", ""),
        }
    return out


def resources_payload() -> Dict[str, Any]:
    load_cache()
    return {
        "datasets": freshness(),
        "inspection_hotlines": _cache.get("inspection_hotlines", {}).get("records", {}),
        "case_service_contacts": _cache.get("case_service_contacts", {}).get("records", {}),
        "elearning_courses": _cache.get("elearning_courses", {}).get("records", []),
        "report_codes": {
            "disaster_types": _cache.get("report_disaster_types", {}).get("records", []),
            "injury_parts": _cache.get("report_injury_parts", {}).get("records", []),
            "agents": _cache.get("report_agents", {}).get("records", []),
            "disability": _cache.get("report_disability", {}).get("records", []),
        },
    }


# Facilities in one county, grouped by what they do. A county with none in a group is
# reported as an empty list, never omitted, so the dashboard can say "none listed here"
# instead of leaving a reader wondering whether the data failed to load.
def medical_for(county: str) -> Dict[str, Any]:
    load_cache()
    groups = []
    for key in MEDICAL_DATASETS:
        payload = _cache.get(key, {})
        items = [r for r in payload.get("records", []) if r.get("county") == county]
        groups.append({
            "key": key,
            "category": payload.get("category", key),
            "dataset_id": payload.get("dataset_id", ""),
            "dataset_name": payload.get("dataset_name", ""),
            "total": len(payload.get("records", [])),
            "facilities": items,
        })
    meta = freshness()
    return {"county": county, "groups": groups,
            "datasets": {k: meta[k] for k in MEDICAL_DATASETS if k in meta}}


# Which inspection items most often fail first-time inspection, for one industry or for
# all of them added together. Pure sums over the cached rows: the dataset counts sites
# that failed an item, so a share here means "of the failed sites, this many failed
# this item", not a rate over every inspected site.
def inspection_summary(industry: str = "", limit: int = 10) -> Dict[str, Any]:
    load_cache()
    payload = _cache.get("inspection_failures", {})
    records = payload.get("records", {})
    if industry and industry in records:
        failed_sites = records[industry]["failed_sites"]
        items = [dict(i) for i in records[industry]["items"]]
    else:
        industry = ""
        failed_sites = sum(r["failed_sites"] for r in records.values())
        merged: Dict[tuple, Dict[str, Any]] = {}
        for r in records.values():
            for i in r["items"]:
                slot = merged.setdefault((i["law"], i["item"]),
                                         {"law": i["law"], "item": i["item"], "failed_sites": 0, "failed_items": 0})
                slot["failed_sites"] += i["failed_sites"]
                slot["failed_items"] += i["failed_items"]
        items = list(merged.values())
    items.sort(key=lambda i: -i["failed_sites"])
    for i in items:
        i["share"] = round(100.0 * i["failed_sites"] / failed_sites, 1) if failed_sites else 0.0
    meta = freshness().get("inspection_failures", {})
    return {
        "industry": industry,
        "industries": sorted(records),
        "year": payload.get("year", ""),
        "failed_sites": failed_sites,
        "items": items[:limit],
        "dataset": meta,
    }


def _top(counts: Dict[str, int], n: int) -> List[Dict[str, Any]]:
    ranked = sorted(((k, v) for k, v in counts.items() if v), key=lambda kv: -kv[1])
    return [{"name": k, "count": v} for k, v in ranked[:n]]


# What the three labour datasets say about one industry, shaped for the dashboard.
# Everything here is a lookup or a sum over the cached rows. Nothing is estimated, and
# a hazard type with no recorded casualties comes back as 0 rather than being hidden:
# "this industry recorded no fires last year" is itself something the operator should see.
def risk_context(industry: str = DEFAULT_INDUSTRY) -> Dict[str, Any]:
    load_cache()
    by_industry = _cache.get("injury_by_industry", {}).get("records", {})
    by_agent = _cache.get("injury_by_agent", {}).get("records", {})
    inspections = _cache.get("inspection_failures", {}).get("records", {})
    if industry not in by_industry:
        industry = DEFAULT_INDUSTRY if DEFAULT_INDUSTRY in by_industry else next(iter(by_industry), "")

    mine = by_industry.get(industry, {})
    everyone: Dict[str, int] = {}
    for counts in by_industry.values():
        for kind, n in counts.items():
            everyone[kind] = everyone.get(kind, 0) + n
    my_total, all_total = sum(mine.values()), sum(everyone.values())
    year = _cache.get("injury_by_industry", {}).get("year", "")

    ranked = [k for k, _ in sorted(mine.items(), key=lambda kv: -kv[1])]
    inspection = inspections.get(industry, {"failed_sites": 0, "items": []})
    fire_items = [i for i in inspection["items"] if "火" in i["item"] or "爆" in i["item"]]

    def hazard(kind: str) -> Dict[str, Any]:
        return {
            "type": kind,
            "industry": mine.get(kind, 0),
            "all": everyone.get(kind, 0),
            "rank": ranked.index(kind) + 1 if mine.get(kind) else None,
            "share": round(100.0 * mine.get(kind, 0) / my_total, 1) if my_total else 0.0,
            "agents": _top(by_agent.get(kind, {}), 3),
        }

    hazards = {k: hazard(k) for k in (FIRE_TYPE, EXPLOSION_TYPE, HEAT_TYPE)}

    return {
        "industry": industry,
        "industries": sorted(by_industry),
        "year": _cache.get("injury_by_industry", {}).get("year", ""),
        "injuries": {
            "industry_total": my_total,
            "all_total": all_total,
            "top": [
                {"name": k, "count": mine[k], "share": round(100.0 * mine[k] / my_total, 1) if my_total else 0.0}
                for k in ranked[:4] if mine[k]
            ],
            "fire": hazards[FIRE_TYPE],
            "explosion": hazards[EXPLOSION_TYPE],
            "heat": hazards[HEAT_TYPE],
        },
        "inspection": {
            "failed_sites": inspection["failed_sites"],
            "top_items": inspection["items"][:3],
            "fire_items": fire_items,
        },
        "datasets": {k: v for k, v in freshness().items() if k in BUILT_DATASETS},
    }


if __name__ == "__main__":
    p = resources_payload()
    for name, info in p["datasets"].items():
        mark = "verified" if info["verified"] else "UNVERIFIED offline cache"
        print(f"{name:<24} {info['record_count']:>3} records   {mark}")
        print(f"{'':<24} {info['dataset_name']} ({info['dataset_id']})")
    print("\nrefresh_all():")
    for line in refresh_all():
        print("  " + line)
