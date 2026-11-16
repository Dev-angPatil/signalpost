#!/usr/bin/env python3
"""Download Brønnøysund's official sub-unit bulk file and keep the universe's sub-units.

Source: https://data.brreg.no/enhetsregisteret/api/underenheter/lastned/csv
(official, open data, NLOD licence). Output: data/cache-work/brreg-subunits.jsonl.gz
with one row per sub-unit whose parent is in data/signalpost-universe.jsonl.gz.
Used to map NAV ads posted on a sub-unit's org number to the parent company,
and later as a source of website candidates (sub-unit homepages, email domains, names).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "data" / "cache-work"
URL = "https://data.brreg.no/enhetsregisteret/api/underenheter/lastned/csv"
RAW = WORK / "underenheter.csv.gz"
OUT = WORK / "brreg-subunits.jsonl.gz"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true", help="download again even if the raw file exists")
    args = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    if args.refresh or not RAW.exists():
        req = urllib.request.Request(URL, headers={"Accept": "application/vnd.brreg.enhetsregisteret.underenhet.v2+gzip;charset=UTF-8", "User-Agent": "builderr-signalpost-poc/0.1 (+https://builderr.ai)"})
        with urllib.request.urlopen(req, timeout=600) as resp, RAW.open("wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
    universe = set()
    with gzip.open(ROOT / "data" / "signalpost-universe.jsonl.gz", "rt", encoding="utf-8") as fh:
        for line in fh:
            universe.add(json.loads(line)["organisation_number"])
    kept = total = 0
    with gzip.open(RAW, "rt", encoding="utf-8", newline="") as src, gzip.GzipFile(OUT, "wb", mtime=0) as gz:
        out = io.TextIOWrapper(gz, encoding="utf-8")
        for row in csv.DictReader(src):
            total += 1
            parent = row.get("overordnetEnhet") or ""
            if parent not in universe or row.get("nedleggelsesdato"):
                continue
            kept += 1
            out.write(json.dumps({
                "org": row["organisasjonsnummer"],
                "parent": parent,
                "name": row.get("navn") or None,
                "homepage": row.get("hjemmeside") or None,
                "email": row.get("epostadresse") or None,
                "address": row.get("beliggenhetsadresse.adresse") or None,
                "postcode": row.get("beliggenhetsadresse.postnummer") or None,
                "town": row.get("beliggenhetsadresse.poststed") or None,
                "employees": int(row["antallAnsatte"]) if (row.get("antallAnsatte") or "").isdigit() else None,
            }, ensure_ascii=False) + "\n")
        out.flush()
        out.detach()
    print(f"sub-units: {total} in bulk file, {kept} active ones under universe parents -> {OUT}")


if __name__ == "__main__":
    main()
