#!/usr/bin/env python3
"""Low-volume, keyless, GitHub-only page-reading watch.

Two known public URLs, one request at a time, one run per day. No public SERP
scraping, captcha handling, stealth, login or proxy rotation. This is a canary,
NOT the frozen 60/100-page acceptance benchmark or a search-availability score.
The output contains diagnostics/lengths only, never raw page content.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

CHECKS = (
    ("repo_html", "https://github.com/pritamduttachoudhury0-collab/IraLens", "IraLens"),
    ("repo_api", "https://api.github.com/repos/pritamduttachoudhury0-collab/IraLens", "IraLens"),
)


def run_watch(lens: Any, *, sleep=time.sleep, clock=time.monotonic) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for index, (name, url, phrase) in enumerate(CHECKS):
        if index:
            sleep(1.2)  # do not send multiple requests at once
        started = clock()
        record: dict[str, Any] = {"id": name, "url": url, "pass": False}
        try:
            artifact = lens.read(url, mode="static", max_chars=50_000)
            content = artifact.content or ""
            record.update({"pass": phrase.casefold() in content.casefold(),
                           "content_chars": len(content),
                           "method": artifact.retrieval_method})
            if not record["pass"]:
                record["reason"] = "required canary phrase absent"
        except Exception as exc:
            record["error_type"] = getattr(exc, "error_type", type(exc).__name__)
            record["reason"] = str(getattr(exc, "detail", "") or exc)[:200]
        record["latency_seconds"] = round(clock() - started, 3)
        results.append(record)
    return {"schema": "iralens-daily-watch/1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scope": "two GitHub page reads only; not search or 60-page acceptance",
            "checks": results,
            "overall": "pass" if all(r["pass"] for r in results) else "fail"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="per-run JSON path")
    args = parser.parse_args(argv)
    from iralens import IraLens

    # A watch must always make real reads; don't score cached pages.
    os.environ["IRALENS_CACHE_ENABLED"] = "false"
    with IraLens() as lens:
        result = run_watch(lens)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Daily watch {result['overall']}: {len(result['checks'])} GitHub-only reads; "
          f"results at {args.output}")
    return 0 if result["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
