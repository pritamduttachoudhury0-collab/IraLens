# -*- coding: utf-8 -*-
"""Optional reference-API comparison for the IraLens benchmark.

When `SEARCH_API_REFERENCE_KEY` is set, the benchmark replays the same queries
through a commercial search API and reports how much the two overlap. This is
diagnostic only — no threshold depends on it, and the run never fails because
of it.

Configuration (environment):

    SEARCH_API_REFERENCE_KEY        required; the API key
    SEARCH_API_REFERENCE_PROVIDER   brave (default) | serper | google-cse | tavily | searxng
    SEARCH_API_REFERENCE_CX         Google Programmable Search engine id (google-cse)
    SEARCH_API_REFERENCE_URL        override the endpoint URL entirely

The point of the comparison is honesty, not parity: a public-engine chain that
finds different pages than a paid API is expected; it must not find *fewer*
pages, silently fail, or return block pages as results.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Sequence, Tuple

DEFAULT_PROVIDERS = {
    "brave": "https://api.search.brave.com/res/v1/web/search",
    "serper": "https://google.serper.dev/search",
    "google-cse": "https://www.googleapis.com/customsearch/v1",
    "tavily": "https://api.tavily.com/search",
}


def _request(provider: str, url: str, key: str, query: str, count: int,
             cx: str = "") -> Tuple[int, Any]:
    if provider == "brave":
        request = urllib.request.Request(
            f"{url}?{urllib.parse.urlencode({'q': query, 'count': count})}",
            headers={"Accept": "application/json", "X-Subscription-Token": key,
                     "User-Agent": "iralens-benchmark/1"})
    elif provider == "serper":
        request = urllib.request.Request(
            url, data=json.dumps({"q": query, "num": count}).encode(),
            headers={"Content-Type": "application/json", "X-API-KEY": key,
                     "User-Agent": "iralens-benchmark/1"})
    elif provider == "google-cse":
        request = urllib.request.Request(
            f"{url}?{urllib.parse.urlencode({'q': query, 'num': count, 'key': key, 'cx': cx})}",
            headers={"Accept": "application/json", "User-Agent": "iralens-benchmark/1"})
    elif provider == "tavily":
        request = urllib.request.Request(
            url, data=json.dumps({"query": query, "max_results": count}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                     "User-Agent": "iralens-benchmark/1"})
    else:
        request = urllib.request.Request(
            f"{url}?{urllib.parse.urlencode({'q': query, 'format': 'json', 'pageno': 1})}",
            headers={"Accept": "application/json", "User-Agent": "iralens-benchmark/1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, json.loads(response.read().decode("utf-8", errors="replace"))


def _extract_urls(provider: str, payload: Any) -> List[str]:
    """Provider-specific result extraction (documented, pure, easy to extend)."""
    try:
        if provider == "brave":
            return [item.get("url", "") for item in payload.get("web", {}).get("results", [])]
        if provider == "serper":
            return [item.get("link", "") for item in payload.get("organic", [])]
        if provider == "google-cse":
            return [item.get("link", "") for item in payload.get("items", [])]
        if provider == "tavily":
            return [item.get("url", "") for item in payload.get("results", [])]
        return [item.get("url", "") for item in payload.get("results", [])]
    except AttributeError:
        return []


def _host(url: str) -> str:
    match = urllib.parse.urlsplit(url if "//" in url else "//" + url)
    host = (match.netloc or "").split("@")[-1].lower()
    return host[4:] if host.startswith("www.") else host


def run_reference_phase(queries: Sequence[Dict[str, Any]],
                        ira_records: Sequence[Dict[str, Any]], *,
                        log: Callable[[str], None]) -> Dict[str, Any]:
    key = os.environ.get("SEARCH_API_REFERENCE_KEY", "").strip()
    if not key:
        return {"ran": False,
                "reason": "SEARCH_API_REFERENCE_KEY is not set",
                "how_to": "export SEARCH_API_REFERENCE_KEY=... [SEARCH_API_REFERENCE_PROVIDER=brave]"}
    provider = os.environ.get("SEARCH_API_REFERENCE_PROVIDER", "brave").strip().lower()
    url = os.environ.get("SEARCH_API_REFERENCE_URL", "").strip() or DEFAULT_PROVIDERS.get(provider, "")
    cx = os.environ.get("SEARCH_API_REFERENCE_CX", "").strip()
    if not url:
        return {"ran": False, "reason": f"unknown provider {provider!r} and no "
                                        "SEARCH_API_REFERENCE_URL"}
    if provider == "google-cse" and not cx:
        return {"ran": False, "reason": "google-cse needs SEARCH_API_REFERENCE_CX"}

    by_id = {record["id"]: record for record in ira_records}
    latencies: List[float] = []
    overlap_hits = 0
    jaccards: List[float] = []
    reference_hits = 0
    errors: List[str] = []
    consulted = 0
    for index, entry in enumerate(queries, start=1):
        started = time.monotonic()
        try:
            status, payload = _request(provider, url, key, entry["query"], 5, cx)
        except urllib.error.HTTPError as exc:
            errors.append(f"{entry['id']}: HTTP {exc.code}")
            continue
        except Exception as exc:                      # network/DNS/JSON
            errors.append(f"{entry['id']}: {type(exc).__name__}")
            continue
        latencies.append(time.monotonic() - started)
        consulted += 1
        reference_urls = [u for u in _extract_urls(provider, payload) if u]
        if reference_urls:
            reference_hits += 1
        reference_domains = {_host(u) for u in reference_urls[:5]}
        ira_record = by_id.get(entry["id"]) or {}
        ira_domains = {item.get("host", "") for item in ira_record.get("top5", [])}
        ira_domains.discard("")
        if reference_domains & ira_domains:
            overlap_hits += 1
        union = reference_domains | ira_domains
        if union:
            jaccards.append(len(reference_domains & ira_domains) / len(union))
        if index % 25 == 0:
            log(f"  reference {index}/{len(queries)}")
        time.sleep(1.0)                              # stay well inside API rate limits

    report = {
        "ran": True,
        "provider": provider,
        "queries_consulted": consulted,
        "errors": errors[:20],
        "error_count": len(errors),
        "reference_availability": round(reference_hits / max(1, consulted), 4) if consulted else None,
        "domain_overlap_at_5": round(overlap_hits / max(1, consulted), 4) if consulted else None,
        "mean_domain_jaccard_at_5": (round(sum(jaccards) / len(jaccards), 4) if jaccards else None),
        "reference_p50_latency": (sorted(latencies)[len(latencies) // 2] if latencies else None),
        "note": "diagnostic only — no threshold depends on this comparison",
    }
    log(f"  reference: overlap@5={report['domain_overlap_at_5']} "
        f"availability={report['reference_availability']}")
    return report


__all__ = ["run_reference_phase"]
