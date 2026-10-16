# -*- coding: utf-8 -*-
"""Optional, measured comparison against a paid search API.

The benchmark replays the exact same labelled queries through one configured
reference provider, then reports search availability, gold-set hit rate,
latency, failures and domain overlap. It is diagnostic rather than an
acceptance gate until IraLens has been tested on an open network and the
provider/corpus evaluation method has been reviewed.

Environment:
    SEARCH_API_REFERENCE_KEY        provider key (required except for SearXNG)
    SEARCH_API_REFERENCE_PROVIDER   brave (default) | serper | google-cse | tavily | searxng
    SEARCH_API_REFERENCE_CX         Google Programmable Search engine id
    SEARCH_API_REFERENCE_URL        endpoint override (required for SearXNG)

API keys are never copied to output. The benchmark saves query IDs, domains,
counts and aggregate metrics only, not provider response bodies.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Sequence, Tuple, cast

from iralens.security import safe_urlopen

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
    with safe_urlopen(
        request.full_url,
        timeout=20,
        headers=dict(request.header_items()),
        data=cast(bytes | None, request.data),
        same_origin_redirects=True,
    ) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("reference response exceeded 2 MiB")
        return response.status, json.loads(raw.decode("utf-8", errors="replace"))


class _ReferenceStatusError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"reference API returned HTTP {status}")
        self.status = status


def _extract_results(provider: str, payload: Any) -> List[Dict[str, str]]:
    """Provider-specific result extraction (pure and fixture-testable)."""
    if not isinstance(payload, dict):
        return []
    try:
        if provider == "brave":
            items = payload.get("web", {}).get("results", [])
            url_key, snippet_keys = "url", ("description", "snippet")
        elif provider == "serper":
            items = payload.get("organic", [])
            url_key, snippet_keys = "link", ("snippet", "description")
        elif provider == "google-cse":
            items = payload.get("items", [])
            url_key, snippet_keys = "link", ("snippet", "description")
        elif provider == "tavily":
            items = payload.get("results", [])
            url_key, snippet_keys = "url", ("content", "snippet")
        else:
            items = payload.get("results", [])
            url_key, snippet_keys = "url", ("content", "snippet")
    except AttributeError:
        return []
    if not isinstance(items, list):
        return []
    output: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get(url_key) or "").strip()
        if not url:
            continue
        snippet = next((str(item.get(key) or "") for key in snippet_keys if item.get(key)), "")
        output.append({"url": url, "title": str(item.get("title") or ""), "snippet": snippet})
    return output


def _extract_urls(provider: str, payload: Any) -> List[str]:
    """Backward-compatible URL-only view of `_extract_results`."""
    return [item["url"] for item in _extract_results(provider, payload)]


def _host(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url if "://" in url else "//" + url)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _domain_match(host: str, domain: str) -> bool:
    allowed = domain.lower().lstrip(".").rstrip(".")
    return host == allowed or host.endswith("." + allowed)


def _gold_match(entry: Dict[str, Any], results: Sequence[Dict[str, str]]) -> str:
    for result in results[:5]:
        host = _host(result.get("url", ""))
        for domain in entry.get("acceptable_domains", []) or []:
            if _domain_match(host, str(domain)):
                return f"domain:{domain}"
        haystack = " ".join((result.get("title", ""), result.get("snippet", ""))).casefold()
        for phrase in entry.get("expected_phrases", []) or []:
            if str(phrase).casefold() in haystack:
                return f"phrase:{phrase}"
    return ""


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def run_reference_phase(queries: Sequence[Dict[str, Any]],
                        ira_records: Sequence[Dict[str, Any]], *,
                        log: Callable[[str], None]) -> Dict[str, Any]:
    provider = os.environ.get("SEARCH_API_REFERENCE_PROVIDER", "brave").strip().lower()
    url = os.environ.get("SEARCH_API_REFERENCE_URL", "").strip() or DEFAULT_PROVIDERS.get(provider, "")
    if provider not in {*DEFAULT_PROVIDERS, "searxng"}:
        return {"ran": False, "reason": f"unknown reference provider {provider!r}"}
    if not url:
        reason = ("searxng requires SEARCH_API_REFERENCE_URL" if provider == "searxng"
                  else f"no default endpoint for provider {provider!r}; set SEARCH_API_REFERENCE_URL")
        return {"ran": False, "reason": reason}
    key = os.environ.get("SEARCH_API_REFERENCE_KEY", "").strip()
    if provider != "searxng" and not key:
        return {"ran": False,
                "reason": "SEARCH_API_REFERENCE_KEY is not set",
                "how_to": "export SEARCH_API_REFERENCE_KEY=... [SEARCH_API_REFERENCE_PROVIDER=brave]"}
    cx = os.environ.get("SEARCH_API_REFERENCE_CX", "").strip()
    endpoint = urllib.parse.urlsplit(url)
    if endpoint.scheme.lower() != "https" or not endpoint.hostname:
        return {"ran": False, "reason": "reference endpoint must be an HTTPS URL"}
    if provider == "google-cse" and not cx:
        return {"ran": False, "reason": "google-cse needs SEARCH_API_REFERENCE_CX"}

    by_id = {record["id"]: record for record in ira_records}
    latencies: List[float] = []
    overlap_queries = 0
    jaccards: List[float] = []
    reference_available = 0
    reference_nonempty = 0
    reference_quality_hits = 0
    errors: List[Dict[str, str]] = []
    per_query: List[Dict[str, Any]] = []

    for index, entry in enumerate(queries, start=1):
        started = time.monotonic()
        error_kind = ""
        records: List[Dict[str, str]] = []
        elapsed: float | None = None
        try:
            status, payload = _request(provider, url, key, entry["query"], 5, cx)
            elapsed = time.monotonic() - started
            if not (200 <= status < 300):
                raise _ReferenceStatusError(status)
            reference_available += 1
            records = _extract_results(provider, payload)[:5]
        except _ReferenceStatusError as exc:
            error_kind = f"http_{exc.status}"
            errors.append({"id": str(entry.get("id", index)), "kind": error_kind})
        except urllib.error.HTTPError as exc:
            error_kind = f"http_{exc.code}"
            errors.append({"id": str(entry.get("id", index)), "kind": error_kind})
        except Exception as exc:  # network/DNS/JSON; never serialize exception text
            error_kind = type(exc).__name__
            errors.append({"id": str(entry.get("id", index)), "kind": error_kind})
        if elapsed is None:
            elapsed = time.monotonic() - started
        latencies.append(elapsed)
        if records:
            reference_nonempty += 1
        matched_by = _gold_match(entry, records)
        if matched_by:
            reference_quality_hits += 1

        reference_domains = {_host(item["url"]) for item in records}
        ira_record = by_id.get(str(entry.get("id"))) or {}
        ira_domains = {
            _host(item.get("url") or item.get("host", ""))
            for item in ira_record.get("top5", []) if isinstance(item, dict)
        }
        ira_domains.discard("")
        intersection = reference_domains & ira_domains
        union = reference_domains | ira_domains
        if intersection:
            overlap_queries += 1
        if union:
            jaccards.append(len(intersection) / len(union))
        per_query.append({
            "id": entry.get("id"),
            "reference_available": not error_kind,
            "reference_returned_results": bool(records),
            "reference_quality_hit": bool(matched_by),
            "reference_matched_by": matched_by,
            "reference_latency_seconds": round(elapsed, 4),
            "reference_error": error_kind or None,
            "reference_domains_top5": sorted(reference_domains),
            "iralens_available": bool(ira_record.get("available")),
            "iralens_quality_hit": bool(ira_record.get("quality_hit")),
            "domain_overlap": bool(intersection),
            "domain_jaccard": round(len(intersection) / len(union), 4) if union else None,
        })
        if index % 25 == 0:
            log(f"  reference {index}/{len(queries)}")
        if index < len(queries):
            time.sleep(1.0)  # stay within a typical commercial API's published quota

    total = len(queries)
    valid_ira = [by_id.get(str(entry.get("id"))) or {} for entry in queries]
    ira_quality = sum(bool(record.get("quality_hit")) for record in valid_ira) / total if total else None
    ira_availability = sum(bool(record.get("available")) for record in valid_ira) / total if total else None
    reference_quality = reference_quality_hits / total if total else None
    report = {
        "ran": True,
        "provider": provider,
        "queries_requested": total,
        "queries_consulted": total - len(errors),
        "errors": errors[:50],
        "error_count": len(errors),
        "failure_rate": round(len(errors) / total, 4) if total else None,
        "reference_availability": round(reference_available / total, 4) if total else None,
        "reference_nonempty_result_rate": round(reference_nonempty / total, 4) if total else None,
        "iralens_availability": round(ira_availability, 4) if ira_availability is not None else None,
        "reference_gold_quality_at_5": round(reference_quality, 4) if reference_quality is not None else None,
        "iralens_gold_quality_at_5": round(ira_quality, 4) if ira_quality is not None else None,
        "gold_quality_delta_reference_minus_iralens": (
            round(reference_quality - ira_quality, 4)
            if reference_quality is not None and ira_quality is not None else None),
        "domain_overlap_at_5": round(overlap_queries / total, 4) if total else None,
        "mean_domain_jaccard_at_5": round(sum(jaccards) / len(jaccards), 4) if jaccards else None,
        "reference_p50_latency_seconds": _percentile(latencies, 0.5),
        "reference_p95_latency_seconds": _percentile(latencies, 0.95),
        "per_query": per_query,
        "quality_definition": "top-5 labelled-domain match or expected phrase in title/snippet; not answer truth",
        "note": "diagnostic only; no parity or quality claim is made by this comparison",
    }
    log(f"  reference: gold@5={report['reference_gold_quality_at_5']} "
        f"availability={report['reference_availability']} "
        f"overlap@5={report['domain_overlap_at_5']} "
        f"p50={report['reference_p50_latency_seconds']}")
    return report


__all__ = ["run_reference_phase", "_extract_results", "_extract_urls"]
