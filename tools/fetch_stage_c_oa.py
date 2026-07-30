#!/usr/bin/env python3
"""Re-fetch the frozen OA corpus from authoritative HTTPS endpoints.

PDF payloads are local test inputs.  They are never copied into Git or a
source-only delivery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "realworld" / "oa_sources.json"
ALLOWED_HOSTS = {
    "journals.plos.org",
    "www.frontiersin.org",
    "elifesciences.org",
    "www.nature.com",
    "europepmc.org",
    "www.ebi.ac.uk",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_download_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"not an approved authoritative HTTPS host: {url}")
    if parsed.username or parsed.password:
        raise ValueError("credentials in URL are forbidden")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--paper", action="append", default=[])
    args = parser.parse_args()
    if args.output_root.exists():
        raise RuntimeError("output root already exists; refusing to overwrite")
    args.output_root.mkdir(parents=True)
    records = json.loads(SOURCES.read_text(encoding="utf-8"))["papers"]
    selected = [
        record
        for record in records
        if not args.paper or record["id"] in set(args.paper)
    ]
    if args.paper and set(args.paper) != {record["id"] for record in selected}:
        raise RuntimeError("unknown paper ID")
    attempts = []
    for record in selected:
        validate_download_url(record["pdf_url"])
        final_path = args.output_root / f"{record['id']}.pdf"
        part_path = final_path.with_suffix(".pdf.part")
        started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        request = urllib.request.Request(
            record["pdf_url"],
            headers={"User-Agent": "Paper2MD-v2-realworld-repro/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                final_url = response.geturl()
                validate_download_url(final_url)
                with part_path.open("xb") as stream:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
                status = response.status
                headers = {
                    "content_length": response.headers.get("Content-Length"),
                    "content_type": response.headers.get("Content-Type"),
                }
            actual_hash = sha256(part_path)
            actual_size = part_path.stat().st_size
            if status != 200:
                raise RuntimeError(f"unexpected HTTP status {status}")
            if (
                actual_hash != record["sha256"]
                or actual_size != record["size_bytes"]
            ):
                raise RuntimeError(
                    f"{record['id']} identity mismatch: "
                    f"{actual_size} bytes / {actual_hash}"
                )
            os.replace(part_path, final_path)
            attempts.append(
                {
                    "paper_id": record["id"],
                    "started_at_utc": started,
                    "ended_at_utc": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "requested_url": record["pdf_url"],
                    "final_url": final_url,
                    "http_status": status,
                    "headers": headers,
                    "size_bytes": actual_size,
                    "sha256": actual_hash,
                    "status": "verified",
                }
            )
        except Exception:
            # Keep a partial payload for diagnosis; never treat it as a paper.
            attempts.append(
                {
                    "paper_id": record["id"],
                    "started_at_utc": started,
                    "ended_at_utc": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "status": "failed",
                    "partial_exists": part_path.exists(),
                    "partial_size_bytes": (
                        part_path.stat().st_size if part_path.exists() else 0
                    ),
                }
            )
            (args.output_root / "fetch_attempts.json").write_text(
                json.dumps(attempts, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            raise
    (args.output_root / "fetch_attempts.json").write_text(
        json.dumps(attempts, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"verified": len(attempts)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
