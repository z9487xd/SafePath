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
}

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
        out[name] = {
            "dataset_name": payload.get("dataset_name", ""),
            "dataset_id": payload.get("dataset_id", ""),
            "publisher": payload.get("publisher", ""),
            "source_url": url,
            "retrieved_at": retrieved,
            "verified": bool(url and retrieved),
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
