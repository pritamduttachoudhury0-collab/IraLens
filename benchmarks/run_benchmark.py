#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IraLens reliability benchmark — frozen corpus, frozen thresholds, one command.

    python benchmarks/run_benchmark.py --all                 # full run incl. the 30-minute load test
    python benchmarks/run_benchmark.py --all --skip-load     # everything except the load test
    python benchmarks/run_benchmark.py --self-test           # validate the harness itself (offline)

What it does, in order:

  preflight    environment + connectivity + which engines are configured
  search       200 labelled queries (cache bypassed, realistic pacing) -> availability,
               quality on dev and held-out splits, latency percentiles
  extraction   100 labelled URLs -> main-text correctness, browser fallback for
               JS-heavy pages, honest typed failures for walled pages
  redundancy   each engine alone on a sampled query set -> independent-engine rates
               and how often one engine was the only source of a query's results
  honesty      the D-079 exit-code contract, typed errors, SSRF refusal, and
               zero silent failures across everything observed above
  tests        the repository's own SSRF / content-guard / hardening suites
  load         10 queries/min for 30 minutes at 5 concurrent (--with-load)
  reference    the same queries through the reference search API when
               SEARCH_API_REFERENCE_KEY is set, and the result overlap with it

Nothing here is tuned to make a number look good: the corpus and the thresholds
are frozen files, every threshold is evaluated exactly as written, and anything
that could not be run is reported as `not_run` with its reason instead of being
counted as a pass. See benchmarks/README.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

BENCH_DIR = Path(__file__).resolve().parent
REPO = BENCH_DIR.parent

QUERIES_PATH = BENCH_DIR / "queries.json"
URLS_PATH = BENCH_DIR / "urls.json"
THRESHOLDS_PATH = BENCH_DIR / "thresholds.json"
FREEZE_PATH = BENCH_DIR / "FREEZE.sha256"
DEFAULT_OUT = BENCH_DIR / "results"

TIMESTAMP = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- io
def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze_report() -> Dict[str, Any]:
    """Detect any post-freeze edit of the corpus or the thresholds."""
    current = {p.name: sha256_of(p) for p in (QUERIES_PATH, URLS_PATH, THRESHOLDS_PATH)}
    expected: Dict[str, str] = {}
    if FREEZE_PATH.exists():
        for line in FREEZE_PATH.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2:
                expected[parts[1].lstrip("*")] = parts[0]
    changed = sorted(name for name, digest in current.items()
                     if name in expected and expected[name] != digest)
    return {"hashes": current, "frozen": expected or None, "changed_since_freeze": changed}


def ensure_iralens_importable() -> None:
    """Use the checkout in this repository when the package is not installed."""
    try:
        import iralens  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(REPO))


# ------------------------------------------------------------------ observations
@dataclass
class SearchObservation:
    query: str = ""
    latency: float = 0.0
    results: List[Dict[str, Any]] = field(default_factory=list)
    outcomes: List[Dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    error_type: Optional[str] = None
    error_detail: Optional[str] = None
    valid_shape: bool = True
    shape_problems: List[str] = field(default_factory=list)
    engines_configured: List[str] = field(default_factory=list)
    degraded: bool = False


@dataclass
class ReadObservation:
    url: str = ""
    latency: float = 0.0
    method: str = ""
    content: str = ""
    error_type: Optional[str] = None
    error_detail: Optional[str] = None
    truncated: bool = False


class System:
    """What the benchmark needs from the system under test."""

    name = "abstract"

    def engine_names(self) -> List[str]:
        return []

    def search(self, query: str, engines: Sequence[str] = ()) -> SearchObservation:
        raise NotImplementedError

    def read(self, url: str) -> ReadObservation:
        raise NotImplementedError

    def cli_command(self) -> Optional[List[str]]:
        return None

    def close(self) -> None:
        return None


class IraLensSystem(System):
    """The real thing: the public `IraLens` facade, one session per run."""

    name = "iralens"

    def __init__(self, *, max_results: int = 5) -> None:
        ensure_iralens_importable()
        from iralens import IraLens
        from iralens.errors import IraLensError
        from iralens.settings import Settings

        self._IraLensError = IraLensError
        self.max_results = max_results
        self.hil = IraLens()
        settings = Settings.from_config(self.hil.config)
        self._engines = [e for e in settings.search_engines if e != "semantic-search"]

    def engine_names(self) -> List[str]:
        return list(self._engines)

    def search(self, query: str, engines: Sequence[str] = ()) -> SearchObservation:
        observation = SearchObservation(query=query, engines_configured=self.engine_names())
        started = time.monotonic()
        try:
            response = self.hil.search_api(
                query,
                options={
                    "max_results": self.max_results,
                    "engines": tuple(engines),
                    "reformulate": True,
                    "cache": "bypass",          # never score a cached answer
                },
            )
        except Exception as exc:                     # typed by the source layer
            observation.latency = time.monotonic() - started
            observation.error_type = getattr(exc, "error_type", None) or type(exc).__name__
            observation.error_detail = str(getattr(exc, "detail", "") or "")[:200] or str(exc)[:200]
            observation.reason = "raised"
            return observation
        observation.latency = time.monotonic() - started
        payload = response.to_dict()
        observation.reason = payload.get("no_results_reason") or ""
        observation.results = [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "snippet": r.get("snippet", ""),
                "engines": r.get("engines", []),
                "published_at": r.get("published_at"),
                "score": r.get("score"),
            }
            for r in payload.get("results", [])
        ]
        observation.outcomes = [
            {
                "engine": o.get("engine", ""),
                "status": o.get("status", ""),
                "kind": o.get("kind", ""),
                "count": o.get("count", 0),
                "detail": (o.get("detail") or "")[:200],
            }
            for o in payload.get("outcomes", [])
        ]
        observation.degraded = bool(payload.get("degraded"))
        observation.valid_shape, observation.shape_problems = validate_response_shape(payload)
        return observation

    def read(self, url: str) -> ReadObservation:
        observation = ReadObservation(url=url)
        started = time.monotonic()
        try:
            artifact = self.hil.open(url, mode="auto")
        except Exception as exc:
            observation.latency = time.monotonic() - started
            observation.error_type = getattr(exc, "error_type", None) or type(exc).__name__
            observation.error_detail = str(getattr(exc, "detail", "") or "")[:200] or str(exc)[:200]
            return observation
        observation.latency = time.monotonic() - started
        observation.method = artifact.retrieval_method or ""
        observation.content = artifact.content or ""
        observation.truncated = bool(artifact.metadata.get("truncated"))
        return observation

    def cli_command(self) -> Optional[List[str]]:
        exe = Path(sys.executable)
        for candidate in (exe.parent / "iralens", exe.parent / "iralens.exe"):
            if candidate.is_file():
                return [str(candidate)]
        return [sys.executable, "-m", "iralens"]

    def close(self) -> None:
        try:
            self.hil.close()
        except Exception:
            pass


# ---------------------------------------------------------------------- scoring
def validate_response_shape(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Honesty check: an empty answer must say why, and every failure must be typed."""
    problems: List[str] = []
    results = payload.get("results") or []
    if not results and not (payload.get("no_results_reason") or "").strip():
        problems.append("empty results with no no_results_reason")
    for outcome in payload.get("outcomes") or []:
        if outcome.get("status") in ("failed", "skipped") and not (outcome.get("kind") or "").strip():
            problems.append(f"untyped failure outcome for engine {outcome.get('engine')!r}")
    for result in results:
        url = result.get("url") or ""
        if not url.startswith(("http://", "https://")):
            problems.append(f"non-public result url {url[:80]!r}")
        if not (result.get("title") or "").strip():
            problems.append(f"result without a title {url[:80]!r}")
    return (not problems), problems


_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    return _WHITESPACE.sub(" ", (value or "")).strip().casefold()


def phrase_present(content: str, phrase: str) -> bool:
    return normalize_text(phrase) in normalize_text(content)


def domain_matches(host: str, domain: str) -> bool:
    host = (host or "").lower().split(":")[0]
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


def host_of(url: str) -> str:
    match = re.match(r"^[a-z]+://([^/?#]+)", url or "", re.IGNORECASE)
    return (match.group(1).split("@")[-1].lower() if match else "")


def percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    """Nearest-rank percentile (no interpolation): the value below which at
    least `fraction` of the samples fall."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * len(ordered) + 0.5)) - 1))
    return round(ordered[index], 3)


def score_query(entry: Dict[str, Any], observation: SearchObservation) -> Dict[str, Any]:
    """A query is answered when a top-5 result is on an acceptable domain or
    contains an expected phrase."""
    top = observation.results[:5]
    matched_by = ""
    for result in top:
        host = host_of(result.get("url", ""))
        for domain in entry.get("acceptable_domains", []):
            if domain_matches(host, domain):
                matched_by = f"domain:{domain}"
                break
        if not matched_by:
            haystack = f"{result.get('title','')} {result.get('snippet','')}"
            for phrase in entry.get("expected_phrases", []) or []:
                if phrase_present(haystack, phrase):
                    matched_by = f"phrase:{phrase}"
                    break
        if matched_by:
            break
    fresh = None
    recency = entry.get("recency_days")
    if recency:
        now = datetime.now(timezone.utc).date()
        fresh = False
        for result in top:
            published = result.get("published_at")
            if not published:
                continue
            try:
                when = datetime.fromisoformat(str(published)[:10]).date()
            except ValueError:
                continue
            if (now - when).days <= int(recency):
                fresh = True
                break
    return {
        "id": entry["id"],
        "category": entry["category"],
        "split": entry["split"],
        "query": entry["query"],
        "latency": round(observation.latency, 3),
        "result_count": len(observation.results),
        "available": bool(observation.results),
        "quality_hit": bool(matched_by),
        "matched_by": matched_by,
        "top5": [{"url": r.get("url", ""), "host": host_of(r.get("url", ""))}
                 for r in top],
        "outcomes": observation.outcomes,
        "reason": observation.reason,
        "error_type": observation.error_type,
        "error_detail": observation.error_detail,
        "shape_ok": observation.valid_shape,
        "shape_problems": observation.shape_problems,
        "fresh": fresh,
        "blocked_kinds": sorted({o.get("kind", "") for o in observation.outcomes
                                 if o.get("status") in ("failed", "skipped")}),
    }


# ----------------------------------------------------------------------- phases
def preflight(system: System) -> Dict[str, Any]:
    report: Dict[str, Any] = {"engines": system.engine_names()}
    try:
        import urllib.request

        with urllib.request.urlopen("https://example.com/", timeout=8) as resp:
            report["internet"] = 200 <= resp.status < 400
    except Exception as exc:
        report["internet"] = False
        report["internet_error"] = f"{type(exc).__name__}: {exc}"[:200]
    report["engine_binary"] = None
    try:
        ensure_iralens_importable()
        from iralens.engine.locate import find_engine

        found = find_engine()
        report["engine_binary"] = str(found) if found else None
    except Exception as exc:
        report["engine_binary_error"] = f"{type(exc).__name__}: {exc}"[:200]
    proxy_vars = [v for v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                              "https_proxy", "all_proxy") if os.environ.get(v)]
    report["proxy_env"] = proxy_vars
    return report


def run_search_phase(system: System, entries: Sequence[Dict[str, Any]], *,
                     pace: float, max_results: int, log: Callable[[str], None]) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    started = time.monotonic()
    for index, entry in enumerate(entries, start=1):
        cycle_start = time.monotonic()
        observation = system.search(entry["query"])
        records.append(score_query(entry, observation))
        if pace > 0 and index < len(entries):
            time.sleep(max(0.0, pace - (time.monotonic() - cycle_start)))
        if index % 20 == 0 or index == len(entries):
            log(f"  search {index}/{len(entries)}")
    return {
        "queries": records,
        "wall_seconds": round(time.monotonic() - started, 1),
    }


def run_extraction_phase(system: System, entries: Sequence[Dict[str, Any]], *,
                         pace: float, log: Callable[[str], None]) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    started = time.monotonic()
    for index, entry in enumerate(entries, start=1):
        cycle_start = time.monotonic()
        observation = system.read(entry["url"])
        phrase_ok = phrase_present(observation.content, entry["required_phrase"])
        if phrase_ok:
            status = "extracted"
        elif observation.error_type:
            status = "typed_error"
        elif not (observation.content or "").strip():
            status = "silent_empty"          # the failure mode that must never happen
        else:
            status = "wrong_text"
        # A JS-heavy page whose useful text is client-rendered must end up on the
        # browser backend when the static fetch cannot produce the phrase.
        js_fallback_ok = True
        if entry.get("requires_js") and not phrase_ok:
            js_fallback_ok = observation.method == "browser"
        records.append({
            "id": entry["id"],
            "url": entry["url"],
            "category": entry["category"],
            "expect": entry["expect"],
            "requires_js": bool(entry.get("requires_js")),
            "latency": round(observation.latency, 3),
            "method": observation.method,
            "status": status,
            "phrase_ok": phrase_ok,
            "typed_error": bool(observation.error_type),
            "error_type": observation.error_type,
            "error_detail": observation.error_detail,
            "content_chars": len(observation.content or ""),
            "truncated": observation.truncated,
            "js_fallback_ok": js_fallback_ok,
            "counts_for_phrase_rate": entry["expect"] == "phrase",
        })
        if pace > 0 and index < len(entries):
            time.sleep(max(0.0, pace - (time.monotonic() - cycle_start)))
        if index % 20 == 0 or index == len(entries):
            log(f"  extraction {index}/{len(entries)}")
    return {"urls": records, "wall_seconds": round(time.monotonic() - started, 1)}


def sample_queries(entries: Sequence[Dict[str, Any]], size: int) -> List[Dict[str, Any]]:
    """Deterministic, category-balanced sample (by id hash — never by outcome)."""
    if size >= len(entries):
        return list(entries)
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        buckets.setdefault(entry["category"], []).append(entry)
    picked: List[Dict[str, Any]] = []
    for category in sorted(buckets):
        ordered = sorted(buckets[category], key=lambda e: hashlib.sha256(e["id"].encode()).hexdigest())
        quota = max(1, round(size * len(ordered) / len(entries)))
        picked.extend(ordered[:quota])
    ordered = sorted(picked, key=lambda e: hashlib.sha256(e["id"].encode()).hexdigest())
    return ordered[:size]


def run_redundancy_phase(system: System, sample: Sequence[Dict[str, Any]], *,
                         engines: Sequence[str], log: Callable[[str], None]) -> Dict[str, Any]:
    per_engine: Dict[str, Dict[str, Any]] = {}
    solo_saver = {engine: 0 for engine in engines}
    for engine in engines:
        successes = 0
        latencies: List[float] = []
        for index, entry in enumerate(sample, start=1):
            observation = system.search(entry["query"], engines=[engine])
            latencies.append(observation.latency)
            if observation.results:
                successes += 1
            if index % 15 == 0:
                log(f"  redundancy {engine} {index}/{len(sample)}")
        per_engine[engine] = {
            "queries": len(sample),
            "successes": successes,
            "success_rate": round(successes / max(1, len(sample)), 4),
            "p50_latency": percentile(latencies, 0.5),
            "p95_latency": percentile(latencies, 0.95),
        }
    return {"engines": per_engine, "solo_saver": solo_saver, "sample_size": len(sample)}


def run_honesty_phase(system: System, search_records: Sequence[Dict[str, Any]], *,
                      log: Callable[[str], None]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def record(name: str, expected: str, actual: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "expected": expected, "actual": actual,
                       "pass": bool(ok), "detail": detail[:300]})

    # -- silent failures seen anywhere in the search phase -------------------
    untyped = [r["id"] for r in search_records if not r["shape_ok"]]
    record("no_untyped_failures_in_search",
           "every empty answer names a reason; every failure is typed",
           f"{len(untyped)} responses broke the contract",
           not untyped, "; ".join(f"{r['id']}: {r['shape_problems']}" for r in search_records
                                  if not r["shape_ok"])[:300])
    silent_empty = [r["id"] for r in search_records
                    if not r["available"] and r["reason"] in ("", "raised") and not r["error_type"]]
    record("no_silent_empty_search", "0 results always carries a reason",
           f"{len(silent_empty)} silent empty answers", not silent_empty, ", ".join(silent_empty))

    # -- the D-079 exit-code contract through the CLI ------------------------
    command = system.cli_command()
    if not command:
        record("cli_exit_codes", "D-079 contract exercised", "no CLI available", False,
               "the synthetic system has no CLI")
        return {"checks": checks, "cli": None}

    home = Path(os.environ.get("IRALENS_HOME") or (BENCH_DIR / ".home"))
    base_env = dict(os.environ)
    base_env["IRALENS_HOME"] = str(home)
    base_env["IRALENS_CACHE_ENABLED"] = "0"

    def run_cli(args: Sequence[str], *, extra_env: Optional[Dict[str, str]] = None,
                timeout: int = 120) -> Tuple[int, str, str]:
        env = dict(base_env)
        env.update(extra_env or {})
        proc = subprocess.run(command + list(args), capture_output=True, text=True,
                              timeout=timeout, env=env)
        return proc.returncode, proc.stdout, proc.stderr

    scenarios = [
        ("empty query is a caller error", ["--json", "search-api", ""], {}, 2, "invalid_input"),
        ("bad date is a caller error",
         ["--json", "search-api", "web search", "--date-from", "2020-13-45"], {}, 2, "invalid_input"),
        ("unknown engine is a caller error",
         ["--json", "search-api", "web search", "--engine", "no-such-engine"], {}, 2, "invalid_input"),
        ("dead host read is typed",
         ["--json", "read", "https://this-host-does-not-exist-zzz123.example"], {}, 2, "page_unavailable"),
        ("SSRF target is refused",
         ["--json", "read", "http://127.0.0.1:8080/"], {}, 2, "blocked_by_security_policy"),
        ("file:// (and metadata) is refused",
         ["--json", "read", "file:///etc/passwd"], {}, 2, "blocked_by_security_policy"),
        # An unknown source is an unsupported operation: still a typed caller
        # error (exit 2), the D-079 contract does not fix which of the two
        # typed errors it must be.
        ("unknown source is typed",
         ["--json", "fetch", "no-such-source", "op"], {}, 2,
         ("source_unavailable", "operation_unsupported")),
        ("every engine failing exits 4",
         ["--json", "search-api", "reliability benchmark", "--engine", "searxng"],
         {"IRALENS_SEARXNG_INSTANCES": "http://127.0.0.1:9"}, 4, "engines_failed"),
    ]
    cli_log: List[Dict[str, Any]] = []
    for name, args, env, expected_code, expected_token in scenarios:
        tokens = (expected_token,) if isinstance(expected_token, str) else tuple(expected_token)
        try:
            code, out, err = run_cli(args, extra_env=env)
        except subprocess.TimeoutExpired:
            record(name, f"exit {expected_code}", "timeout", False)
            cli_log.append({"scenario": name, "code": None, "timeout": True})
            continue
        body = (out or "") + (err or "")
        typed = any(token in body for token in tokens)
        ok = code == expected_code and typed
        record(name, f"exit {expected_code} + {'|'.join(tokens)}",
               f"exit {code}" + ("" if typed else " (token missing)"), ok,
               (err or out).strip()[:200])
        cli_log.append({"scenario": name, "args": list(args), "code": code,
                        "token_found": typed, "stderr": (err or "")[:300]})

    # `every engine failing` must still print the structured outcomes for the caller
    exit4 = next((c for c in cli_log if c["scenario"] == "every engine failing exits 4"), None)
    record("exit_4_keeps_structured_output",
           "outcomes are printed even when every engine failed",
           "structured output present" if exit4 and exit4.get("token_found") else "missing",
           bool(exit4 and exit4.get("token_found")))
    record("cli_exit_codes", "D-079 contract exercised", f"{sum(1 for c in checks if c['pass'])}/"
           f"{len(checks)} checks passed", all(c["pass"] for c in checks))
    return {"checks": checks, "cli": cli_log}


def run_tests_phase(*, full: bool, log: Callable[[str], None]) -> Dict[str, Any]:
    suites = ["tests/test_ssrf_fetch.py", "tests/test_content_guard.py",
              "tests/test_search_hardening.py", "tests/test_security.py",
              "tests/test_search_parsers.py", "tests/test_cli_help_contract.py"]
    args = [sys.executable, "-m", "pytest", "-q", *(suites if not full else ["tests/"])]
    log("  running the repository test suite")
    proc = subprocess.run(args, cwd=str(REPO), capture_output=True, text=True, timeout=1800)
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    return {"command": " ".join(args), "returncode": proc.returncode,
            "passed": proc.returncode == 0, "tail": tail}


def run_load_phase(system: System, entries: Sequence[Dict[str, Any]], *,
                   minutes: float, rate_per_minute: float, concurrency: int,
                   log: Callable[[str], None]) -> Dict[str, Any]:
    total = max(1, int(minutes * rate_per_minute))
    interval = 60.0 / rate_per_minute
    rng = random.Random(20261011)
    pool = entries
    semaphore = threading.BoundedSemaphore(concurrency)
    results: List[Dict[str, Any]] = []
    lock = threading.Lock()
    saturated = 0
    started = time.monotonic()

    def worker(entry: Dict[str, Any], index: int) -> None:
        try:
            observation = system.search(entry["query"])
            record = score_query(entry, observation)
            record["index"] = index
            with lock:
                results.append(record)
        finally:
            semaphore.release()

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        for index in range(total):
            target = started + index * interval
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            if not semaphore.acquire(timeout=max(1.0, interval)):
                saturated += 1
                continue
            entry = pool[rng.randrange(len(pool))]
            executor.submit(worker, entry, index)
            if (index + 1) % 30 == 0:
                log(f"  load {index + 1}/{total}")
        executor.shutdown(wait=True)

    latencies = [r["latency"] for r in results]
    failures = [r for r in results if not r["available"]]
    blocked = [r for r in results if r["available"] and r["blocked_kinds"]]
    return {
        "target_queries": total,
        "attempted": len(results) + saturated,
        "completed": len(results),
        "saturated": saturated,
        "failures": len(failures),
        "failure_rate": round(len(failures) / max(1, len(results)), 4),
        "degraded_with_partial_engines": len(blocked),
        "p50_latency": percentile(latencies, 0.5),
        "p95_latency": percentile(latencies, 0.95),
        "wall_seconds": round(time.monotonic() - started, 1),
        "failure_ids": [r["id"] for r in failures][:40],
    }


# ---------------------------------------------------------------------- metrics
def compute_metrics(*, search: Dict[str, Any], extraction: Dict[str, Any],
                    redundancy: Dict[str, Any], honesty: Dict[str, Any],
                    load: Optional[Dict[str, Any]], tests: Optional[Dict[str, Any]],
                    reference: Optional[Dict[str, Any]],
                    thresholds: Dict[str, Any]) -> Dict[str, Any]:
    queries = search["queries"]
    dev = [q for q in queries if q["split"] == "dev"]
    heldout = [q for q in queries if q["split"] == "heldout"]

    def rate(records: Sequence[Dict[str, Any]], key: str) -> Optional[float]:
        if not records:
            return None
        return round(sum(1 for r in records if r[key]) / len(records), 4)

    urls = extraction["urls"]
    scorable = [u for u in urls if u["counts_for_phrase_rate"]]
    walled = [u for u in urls if not u["counts_for_phrase_rate"]]
    js_urls = [u for u in urls if u["requires_js"]]

    engine_rates = {name: data["success_rate"] for name, data in redundancy["engines"].items()}
    independent = sorted([name for name, value in engine_rates.items()
                          if value >= thresholds["redundancy_min_engine_success_rate"]])

    metrics: Dict[str, Any] = {
        "queries_total": len(queries),
        "availability": rate(queries, "available"),
        "availability_dev": rate(dev, "available"),
        "availability_heldout": rate(heldout, "available"),
        "quality_dev": rate(dev, "quality_hit"),
        "quality_heldout": rate(heldout, "quality_hit"),
        "quality_all": rate(queries, "quality_hit"),
        "quality_gap_dev_minus_heldout": (
            round((rate(dev, "quality_hit") or 0) - (rate(heldout, "quality_hit") or 0), 4)),
        "quality_by_category": {
            category: rate([q for q in queries if q["category"] == category], "quality_hit")
            for category in sorted({q["category"] for q in queries})
        },
        "availability_by_category": {
            category: rate([q for q in queries if q["category"] == category], "available")
            for category in sorted({q["category"] for q in queries})
        },
        "recent_news_freshness_rate": rate([q for q in queries if q.get("fresh") is not None], "fresh"),
        "search_p50_latency": percentile([q["latency"] for q in queries], 0.5),
        "search_p95_latency": percentile([q["latency"] for q in queries], 0.95),
        "search_max_latency": round(max((q["latency"] for q in queries), default=0), 3),
        "search_error_rate": rate(queries, "error_type"),
        "blocked_kinds": sorted({kind for q in queries for kind in q["blocked_kinds"] if kind}),
        "untyped_failures": sum(1 for q in queries if not q["shape_ok"]),
        "extraction_phrase_rate": rate(scorable, "phrase_ok"),
        "extraction_strict_rate_all_urls": rate(urls, "phrase_ok"),
        "extraction_typed_error_rate": rate(scorable, "typed_error"),
        "extraction_silent_empty": sum(1 for u in urls if u["status"] == "silent_empty"),
        "extraction_wrong_text": sum(1 for u in urls if u["status"] == "wrong_text"),
        "extraction_methods": {
            method: sum(1 for u in urls if u["method"] == method)
            for method in sorted({u["method"] for u in urls})
        },
        "read_p50_latency": percentile([u["latency"] for u in urls], 0.5),
        "read_p95_latency": percentile([u["latency"] for u in urls], 0.95),
        "walled_page_handled_rate": (
            rate(walled, "phrase_ok") if not walled else
            round(sum(1 for u in walled if u["phrase_ok"] or u["typed_error"]) / len(walled), 4)),
        "walled_silent_failures": sum(1 for u in walled if u["status"] == "silent_empty"),
        "js_heavy_urls": len(js_urls),
        "js_heavy_phrase_rate": rate(js_urls, "phrase_ok"),
        "js_heavy_browser_fallback_used": sum(1 for u in js_urls if u["method"] == "browser"),
        "js_heavy_fallback_failures": sum(1 for u in js_urls if not u["js_fallback_ok"]),
        "engine_solo_success": engine_rates,
        "independent_engines_at_threshold": independent,
        "honesty_checks_passed": (sum(1 for c in honesty["checks"] if c["pass"]),
                                  len(honesty["checks"])),
        "honesty_all_pass": all(c["pass"] for c in honesty["checks"]),
        "repo_tests_passed": (tests or {}).get("passed"),
    }
    if reference:
        metrics["reference_api"] = reference
    if load:
        metrics["load_failure_rate"] = load["failure_rate"]
        metrics["load_p95_latency"] = load["p95_latency"]
    return metrics


def evaluate(metrics: Dict[str, Any], *, load: Optional[Dict[str, Any]],
             reference: Optional[Dict[str, Any]], tests: Optional[Dict[str, Any]],
             thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every Step-2 threshold, evaluated exactly as written."""
    verdicts: List[Dict[str, Any]] = []

    def add(name: str, requirement: str, value: Any, ok: Optional[bool], note: str = "") -> None:
        verdicts.append({
            "threshold": name,
            "requirement": requirement,
            "value": value,
            "pass": ok,
            "status": "not_run" if ok is None else ("pass" if ok else "fail"),
            "note": note,
        })

    availability = metrics["availability"]
    add("availability", f">= {thresholds['availability_min']}",
        availability, None if availability is None else availability >= thresholds["availability_min"])

    quality_dev = metrics["quality_dev"]
    quality_heldout = metrics["quality_heldout"]
    gap = metrics["quality_gap_dev_minus_heldout"]
    add("quality_dev", f">= {thresholds['quality_min']}", quality_dev,
        None if quality_dev is None else quality_dev >= thresholds["quality_min"])
    add("quality_heldout", f">= {thresholds['quality_min']}", quality_heldout,
        None if quality_heldout is None else quality_heldout >= thresholds["quality_min"])
    add("quality_heldout_within_5pt_of_dev", f"<= {thresholds['quality_max_heldout_gap']}", gap,
        None if gap is None else gap <= thresholds["quality_max_heldout_gap"])

    independent = metrics["independent_engines_at_threshold"]
    add("engine_redundancy", f">= {thresholds['redundancy_min_engines']} engines with solo success "
        f">= {thresholds['redundancy_min_engine_success_rate']}",
        independent, len(independent) >= thresholds["redundancy_min_engines"],
        f"per-engine: {metrics['engine_solo_success']}")

    add("search_p50", f"<= {thresholds['search_p50_seconds']}s", metrics["search_p50_latency"],
        None if metrics["search_p50_latency"] is None
        else metrics["search_p50_latency"] <= thresholds["search_p50_seconds"])
    add("search_p95", f"<= {thresholds['search_p95_seconds']}s", metrics["search_p95_latency"],
        None if metrics["search_p95_latency"] is None
        else metrics["search_p95_latency"] <= thresholds["search_p95_seconds"])
    add("read_p95", f"<= {thresholds['read_p95_seconds']}s", metrics["read_p95_latency"],
        None if metrics["read_p95_latency"] is None
        else metrics["read_p95_latency"] <= thresholds["read_p95_seconds"])

    if load is None:
        add("load", f"< {thresholds['load_max_failure_rate']} failures over "
            f"{thresholds['load_minutes']} min at {thresholds['load_rate_per_minute']}/min "
            f"x{thresholds['load_concurrency']}", "not run", None,
            "run with --with-load (30 minutes)")
    else:
        add("load", f"< {thresholds['load_max_failure_rate']} failures",
            load["failure_rate"], load["failure_rate"] < thresholds["load_max_failure_rate"],
            f"{load['failures']}/{load['completed']} failed, {load['saturated']} saturated")

    phrase_rate = metrics["extraction_phrase_rate"]
    add("extraction_phrase_rate", f">= {thresholds['extraction_min_phrase_rate']}", phrase_rate,
        None if phrase_rate is None else phrase_rate >= thresholds["extraction_min_phrase_rate"],
        f"strict over all 100 URLs: {metrics['extraction_strict_rate_all_urls']}")
    add("extraction_no_silent_failures", "0 pages with empty content and no typed error",
        metrics["extraction_silent_empty"], metrics["extraction_silent_empty"] == 0)
    add("extraction_js_browser_fallback", "every JS-heavy page without static text ends on the browser",
        metrics["js_heavy_fallback_failures"], metrics["js_heavy_fallback_failures"] == 0,
        f"{metrics['js_heavy_browser_fallback_used']}/{metrics['js_heavy_urls']} used the browser")
    add("extraction_walled_pages_typed", "walled pages answer with text or a typed error",
        metrics["walled_silent_failures"], metrics["walled_silent_failures"] == 0,
        f"handled: {metrics['walled_page_handled_rate']}")

    honesty_ok = metrics["honesty_all_pass"] and metrics["untyped_failures"] == 0
    add("honesty", "zero silent failures, D-079 exit codes, SSRF + injection suites green",
        f"untyped={metrics['untyped_failures']}, "
        f"checks={metrics['honesty_checks_passed'][0]}/{metrics['honesty_checks_passed'][1]}, "
        f"repo_tests={'not run' if metrics['repo_tests_passed'] is None else metrics['repo_tests_passed']}",
        honesty_ok if tests is not None else None,
        "" if tests is not None else "run with --with-tests")

    if reference is None:
        add("reference_api_overlap", "informational",
            "not run", None, "set SEARCH_API_REFERENCE_KEY (see benchmarks/README.md)")
    else:
        add("reference_api_overlap", "informational", reference.get("domain_overlap_at_5"), True,
            "reported, not gated")
    return verdicts


# -------------------------------------------------------------------- rendering
def write_results(out_dir: Path, payload: Dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
                    encoding="utf-8")
    return path


def format_table(payload: Dict[str, Any]) -> str:
    metrics = payload["metrics"]
    lines = [
        "| metric | value | threshold | verdict |",
        "|---|---|---|---|",
    ]
    for verdict in payload["verdicts"]:
        value = verdict["value"]
        if isinstance(value, float):
            value = f"{value:.4f}"
        lines.append(f"| {verdict['threshold']} | {value} | {verdict['requirement']} | "
                     f"{verdict['status'].upper()} |")
    lines += [
        "",
        f"queries: {metrics['queries_total']} "
        f"(dev {metrics['quality_dev']}, held-out {metrics['quality_heldout']} quality) · "
        f"URLs: {len(payload['extraction']['urls'])} · "
        f"generated {payload['meta']['timestamp']} · {payload['meta']['network']} network",
    ]
    return "\n".join(lines)


def benchmarks_md_row(payload: Dict[str, Any]) -> str:
    m = payload["metrics"]
    verdict = payload["overall_status"].upper()
    failing = [v["threshold"] for v in payload["verdicts"] if v["status"] == "fail"]
    not_run = [v["threshold"] for v in payload["verdicts"] if v["status"] == "not_run"]

    def pct(value: Any) -> str:
        return "n/a" if value is None else f"{value * 100:.1f}%"

    def secs(value: Any) -> str:
        return "n/a" if value is None else f"{value:.2f}s"

    notes = []
    if failing:
        notes.append("FAIL: " + ", ".join(failing))
    if not_run:
        notes.append("not run: " + ", ".join(not_run))
    return ("| {ts} | {commit} | {network} | {avail} | {qdev} | {qheld} | {indep} | {p50} | {p95} | "
            "{rp95} | {load} | {ext} | {honest} | **{verdict}** | {notes} |").format(
        ts=payload["meta"]["timestamp"][:10],
        commit=payload["meta"]["commit"][:7],
        network=payload["meta"]["network"],
        avail=pct(m["availability"]),
        qdev=pct(m["quality_dev"]),
        qheld=pct(m["quality_heldout"]),
        indep=", ".join(m["independent_engines_at_threshold"]) or "none",
        p50=secs(m["search_p50_latency"]),
        p95=secs(m["search_p95_latency"]),
        rp95=secs(m["read_p95_latency"]),
        load="n/a" if not payload.get("load") else pct(payload["load"]["failure_rate"]),
        ext=pct(m["extraction_phrase_rate"]),
        honest="ok" if m["honesty_all_pass"] and m["untyped_failures"] == 0 else "FAILED",
        verdict=verdict,
        notes="; ".join(notes) or "-",
    )


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True,
                              text=True, timeout=20).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def append_benchmarks_md(payload: Dict[str, Any], log: Callable[[str], None]) -> None:
    path = REPO / "BENCHMARKS.md"
    row = benchmarks_md_row(payload)
    if not path.exists():
        log(f"  BENCHMARKS.md not found; row:\n{row}")
        return
    text = path.read_text(encoding="utf-8")
    marker = "<!-- benchmark-rows -->"
    if marker in text:
        head, _, tail = text.partition(marker)
        lines = [line for line in tail.splitlines() if line.strip()]
        table = "\n".join(lines)
        text = f"{head}{marker}\n{table}\n{row}\n"
    else:
        text = text.rstrip() + "\n\n" + row + "\n"
    path.write_text(text, encoding="utf-8")
    log(f"  appended a row to {path.relative_to(REPO)}")


# --------------------------------------------------------------------- self-test
class SyntheticSystem(System):
    """Deterministic stand-in used by --self-test to validate the harness.

    It fabricates observations at a *known* pass/fail rate, so the metric math,
    the threshold evaluation, the table and results.json can all be checked
    offline. It never touches the network and its results are never a claim
    about the real system.
    """

    name = "synthetic"

    def __init__(self, profile: str = "good") -> None:
        self.profile = profile
        self._engines = ["duckduckgo", "bing", "searxng"]
        self._by_query = {q["query"]: q for q in load_json(QUERIES_PATH)["queries"]}
        self._calls = 0
        self._reads = 0
        # profile -> (availability miss period, quality miss period, solo miss period)
        self._periods = {"good": (200, 10, 6), "bad": (33, 3, 3)}[profile]

    def engine_names(self) -> List[str]:
        return list(self._engines)

    def _tick(self, which: int) -> bool:
        """True on every `period`-th call (a deterministic synthetic failure)."""
        period = self._periods[which]
        return period > 0 and self._counter(which) % period == 0

    def _counter(self, which: int) -> int:
        return self._calls if which == 0 else self._calls + which

    def search(self, query: str, engines: Sequence[str] = ()) -> SearchObservation:
        self._calls += 1
        entry = self._by_query.get(query) or {"query": query, "acceptable_domains": [],
                                              "expected_phrases": []}
        observation = SearchObservation(query=query, engines_configured=self.engine_names())
        observation.latency = 0.6 + (int(hashlib.sha256(query.encode()).hexdigest()[:4], 16) % 200) / 100
        if engines:
            observation.latency *= 0.7
            if self._calls % self._periods[2] == 0:
                observation.outcomes = [{"engine": engines[0], "status": "empty", "kind": "empty",
                                         "count": 0, "detail": ""}]
                observation.reason = "no_results"
                return observation
            observation.results = [self._result(entry, engines[0], hit=True)]
            observation.outcomes = [{"engine": engines[0], "status": "results", "kind": "ok",
                                     "count": 1, "detail": ""}]
            return observation
        if self._tick(0):
            observation.outcomes = [{"engine": "duckduckgo", "status": "failed", "kind": "captcha",
                                     "count": 0, "detail": "blocked"}]
            observation.reason = "engines_failed"
            return observation
        hit = not self._tick(1)
        observation.results = [self._result(entry, "duckduckgo", hit=hit),
                               self._result(entry, "bing", hit=hit)]
        observation.outcomes = [
            {"engine": "duckduckgo", "status": "results", "kind": "ok", "count": 3, "detail": ""},
            {"engine": "bing", "status": "results", "kind": "ok", "count": 2, "detail": ""},
        ]
        return observation

    @staticmethod
    def _result(entry: Dict[str, Any], engine: str, *, hit: bool) -> Dict[str, Any]:
        domains = entry.get("acceptable_domains") or []
        phrases = entry.get("expected_phrases") or []
        if hit and domains:
            url = f"https://{domains[0]}/synthetic"
            title = entry["query"]
        elif hit and phrases:
            url = "https://synthetic.example/hit"
            title = f"{entry['query']} {phrases[0]}"
        else:
            url = "https://unrelated.example/miss"
            title = "unrelated page"
        return {"url": url, "title": title, "snippet": title, "engines": [engine],
                "published_at": datetime.now(timezone.utc).date().isoformat()}

    def read(self, url: str) -> ReadObservation:
        self._reads += 1
        observation = ReadObservation(url=url)
        observation.latency = 0.4 + (int(hashlib.sha256(url.encode()).hexdigest()[:4], 16) % 150) / 100
        walled = self._reads % 12 == 0 or any(host in url for host in
                                              ("wsj.com", "instagram.com", "x.com", "ft.com"))
        if self._tick_read(0.02 if self.profile == "good" else 0.1) and not walled:
            observation.error_type = "page_unavailable"
            observation.error_detail = "http_404"
            return observation
        if walled and self.profile == "good":
            observation.error_type = "page_unavailable"
            observation.error_detail = "http_403"
            return observation
        miss = self._tick_read(0.07 if self.profile == "good" else 0.25) and not walled
        observation.method = "browser" if "requires_js" in url or miss else "direct"
        observation.content = ("fabricated content with the required phrase" if not miss
                               else "fabricated content without it")
        return observation

    def _tick_read(self, rate: float) -> bool:
        period = int(round(1 / rate)) if rate else 0
        return period > 0 and self._reads % period == 0

    def cli_command(self) -> Optional[List[str]]:
        return None


SELF_TEST_EXPECTATIONS = {
    "good": {"availability": "pass", "quality_dev": "pass", "extraction_phrase_rate": "pass"},
    "bad": {"availability": "fail", "quality_dev": "fail", "extraction_phrase_rate": "pass"},
}


class SyntheticReadSystem(SyntheticSystem):
    """Synthetic system whose reads always contain the phrase, so the self-test
    asserts the extraction *metric* plumbing independently of read randomness."""

    def __init__(self, profile: str = "good") -> None:
        super().__init__(profile)
        self._phrase_by_url = {u["url"]: u["required_phrase"]
                               for u in load_json(URLS_PATH)["urls"]}

    def read(self, url: str) -> ReadObservation:
        phrase = self._phrase_by_url.get(url, "required phrase")
        return ReadObservation(url=url, latency=0.5, method="direct",
                               content=f"fabricated main text containing: {phrase}")


def run_self_test(log: Callable[[str], None]) -> int:
    """Validate the harness end to end against a synthetic system (offline)."""
    queries = load_json(QUERIES_PATH)["queries"]
    urls = load_json(URLS_PATH)["urls"]
    thresholds = load_json(THRESHOLDS_PATH)
    failures: List[str] = []
    # include the walled pages in the sample so their accounting is exercised
    url_sample = urls[:25] + [u for u in urls if u["expect"] == "phrase_or_walled"][:5]
    for profile in ("good", "bad"):
        system = SyntheticReadSystem(profile)
        search = run_search_phase(system, queries[:60], pace=0.0, max_results=5, log=log)
        extraction = run_extraction_phase(system, url_sample, pace=0.0, log=log)
        redundancy = run_redundancy_phase(system, sample_queries(queries[:60], 30),
                                          engines=system.engine_names(), log=log)
        honesty = run_honesty_phase(system, search["queries"], log=log)
        metrics = compute_metrics(search=search, extraction=extraction, redundancy=redundancy,
                                  honesty=honesty, load=None, tests=None, reference=None,
                                  thresholds=thresholds)
        verdicts = evaluate(metrics, load=None, reference=None, tests=None, thresholds=thresholds)
        statuses = {v["threshold"]: v["status"] for v in verdicts}
        for name, expected in SELF_TEST_EXPECTATIONS[profile].items():
            if statuses.get(name) != expected:
                failures.append(f"[{profile}] {name}: expected {expected}, got {statuses.get(name)}")
        # the harness must also produce the row and the JSON it promises
        payload = {"meta": {"timestamp": TIMESTAMP, "commit": git_commit(), "network": "self-test"},
                   "metrics": metrics, "verdicts": verdicts, "extraction": extraction,
                   "load": None, "overall_status": "self-test"}
        row = benchmarks_md_row(payload)
        if not row.startswith("| ") or row.count("|") < 10:
            failures.append(f"[{profile}] benchmark row malformed: {row!r}")
        if len(format_table(payload).splitlines()) < 8:
            failures.append(f"[{profile}] summary table too short")
        if metrics["walled_silent_failures"] != 0:
            failures.append(f"[{profile}] walled-page accounting broken")
        written = write_results(DEFAULT_OUT / f"self-test-{profile}", payload)
        reloaded = json.loads(written.read_text(encoding="utf-8"))
        if reloaded["verdicts"] != verdicts:
            failures.append(f"[{profile}] results.json did not round-trip")
        written.unlink()
        if freeze_report()["changed_since_freeze"]:
            failures.append(f"[{profile}] frozen files changed: "
                            f"{freeze_report()['changed_since_freeze']}")
        log(f"  self-test[{profile}] availability={metrics['availability']} "
            f"quality_dev={metrics['quality_dev']} extraction={metrics['extraction_phrase_rate']} "
            f"engines={metrics['independent_engines_at_threshold']}")
    if failures:
        print("SELF-TEST FAILED")
        for failure in failures:
            print("  -", failure)
        return 1
    print("SELF-TEST PASSED — harness metrics, verdicts, table and JSON behave as specified")
    print("(this validates the benchmark harness only; it is not a measurement of the real system)")
    out = DEFAULT_OUT / "self-test"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.self-test.json").write_text(
        json.dumps({"note": "harness self-test, NOT a benchmark measurement",
                    "timestamp": TIMESTAMP}, indent=2) + "\n", encoding="utf-8")
    return 0


# -------------------------------------------------------------------------- main
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    thresholds = load_json(THRESHOLDS_PATH) if THRESHOLDS_PATH.exists() else {}
    parser = argparse.ArgumentParser(
        prog="run_benchmark.py",
        description="IraLens reliability benchmark (frozen corpus, frozen thresholds).")
    parser.add_argument("--all", action="store_true",
                        help="run every phase that can run (search, extraction, redundancy, "
                             "honesty, tests; add --with-load for the 30-minute load test)")
    parser.add_argument("--search", action="store_true", help="run the query phase")
    parser.add_argument("--extraction", action="store_true", help="run the URL phase")
    parser.add_argument("--redundancy", action="store_true", help="run the per-engine phase")
    parser.add_argument("--honesty", action="store_true", help="run the honesty/exit-code phase")
    parser.add_argument("--with-load", action="store_true", help="include the 30-minute load test")
    parser.add_argument("--skip-load", action="store_true",
                        help="with --all: skip the load test (shorter run)")
    parser.add_argument("--with-tests", action="store_true", help="run the repository test suites")
    parser.add_argument("--with-reference", action="store_true",
                        help="run the reference API comparison when SEARCH_API_REFERENCE_KEY is set")
    parser.add_argument("--split", choices=["all", "dev", "heldout"], default="all",
                        help="which query split to run (default: both; the held-out split is "
                             "scored separately and must never be tuned against)")
    parser.add_argument("--pace", type=float, default=2.0,
                        help="seconds between queries (realistic pacing; default 2.0)")
    parser.add_argument("--read-pace", type=float, default=1.0, help="seconds between URL reads")
    parser.add_argument("--limit", type=int, default=0,
                        help="only the first N corpus entries (smoke runs; recorded in the output)")
    parser.add_argument("--max-results", type=int, default=5, help="results per query (default 5)")
    parser.add_argument("--redundancy-sample", type=int, default=60,
                        help="queries per engine in the redundancy phase (default 60)")
    parser.add_argument("--engines", default="",
                        help="comma-separated engines for the redundancy phase (default: configured)")
    parser.add_argument("--load-minutes", type=float, default=float(thresholds.get("load_minutes", 30)))
    parser.add_argument("--load-rate", type=float,
                        default=float(thresholds.get("load_rate_per_minute", 10)))
    parser.add_argument("--load-concurrency", type=int,
                        default=int(thresholds.get("load_concurrency", 5)))
    parser.add_argument("--network", choices=["home", "datacenter", "proxy", "unknown", "self-test"],
                        default="unknown", help="where this run happens (recorded in results.json)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output directory for results.json")
    parser.add_argument("--no-benchmarks-md", action="store_true",
                        help="do not append a row to BENCHMARKS.md")
    parser.add_argument("--self-test", action="store_true",
                        help="validate the harness offline against a synthetic system")
    parser.add_argument("--allow-cache", action="store_true",
                        help="allow the response cache (OFF by default: scores must not be cached)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    def log(message: str) -> None:
        print(message, flush=True)

    if args.self_test:
        return run_self_test(log)

    if not (args.all or args.search or args.extraction or args.redundancy or args.honesty
            or args.with_load or args.with_tests):
        args.all = True
    if args.all:
        args.search = args.extraction = args.redundancy = args.honesty = True
        args.with_tests = True
        if not args.skip_load:
            args.with_load = True

    if not args.allow_cache:
        os.environ["IRALENS_CACHE_ENABLED"] = "0"      # never score a cached answer
    os.environ.setdefault("IRALENS_HOME", str(BENCH_DIR / ".home"))

    queries = load_json(QUERIES_PATH)["queries"]
    urls = load_json(URLS_PATH)["urls"]
    thresholds = load_json(THRESHOLDS_PATH)
    if args.split != "all":
        queries = [q for q in queries if q["split"] == args.split]
    if args.limit:
        queries = queries[: args.limit]
        urls = urls[: args.limit]

    system = IraLensSystem(max_results=args.max_results)
    log(f"IraLens benchmark {TIMESTAMP} · {len(queries)} queries · {len(urls)} URLs · "
        f"engines={system.engine_names()}")
    try:
        environment = preflight(system)
        log(f"preflight: internet={environment['internet']} "
            f"engine={environment['engine_binary']} proxy={environment['proxy_env'] or 'none'}")
        if not environment["internet"]:
            log("WARNING: no general Internet access from this host; search and extraction "
                "results below describe a blocked network, not the system's reliability.")

        search: Dict[str, Any] = {"queries": [], "wall_seconds": 0.0}
        if args.search:
            log("search phase")
            search = run_search_phase(system, queries, pace=args.pace, max_results=args.max_results,
                                      log=log)
        extraction: Dict[str, Any] = {"urls": [], "wall_seconds": 0.0}
        if args.extraction:
            log("extraction phase")
            extraction = run_extraction_phase(system, urls, pace=args.read_pace, log=log)
        redundancy: Dict[str, Any] = {"engines": {}, "solo_saver": {}, "sample_size": 0}
        if args.redundancy:
            log("redundancy phase")
            engines = [e.strip() for e in args.engines.split(",") if e.strip()] or system.engine_names()
            redundancy = run_redundancy_phase(system, sample_queries(queries, args.redundancy_sample),
                                              engines=engines, log=log)
        honesty: Dict[str, Any] = {"checks": [], "cli": None}
        if args.honesty:
            log("honesty phase")
            honesty = run_honesty_phase(system, search["queries"], log=log)
        tests = run_tests_phase(full=False, log=log) if args.with_tests else None
        load = None
        if args.with_load:
            log(f"load phase ({args.load_minutes:.0f} min, {args.load_rate:.0f}/min, "
                f"{args.load_concurrency} concurrent)")
            load = run_load_phase(system, queries, minutes=args.load_minutes,
                                  rate_per_minute=args.load_rate,
                                  concurrency=args.load_concurrency, log=log)
        reference = None
        if args.with_reference or os.environ.get("SEARCH_API_REFERENCE_KEY"):
            log("reference API phase")
            from reference_api import run_reference_phase   # type: ignore # noqa: PLC0415

            reference = run_reference_phase(queries, search["queries"], log=log)
    finally:
        system.close()

    metrics = compute_metrics(search=search, extraction=extraction, redundancy=redundancy,
                              honesty=honesty, load=load, tests=tests, reference=reference,
                              thresholds=thresholds)
    verdicts = evaluate(metrics, load=load, reference=reference, tests=tests, thresholds=thresholds)
    failing = [v["threshold"] for v in verdicts if v["status"] == "fail"]
    not_run = [v["threshold"] for v in verdicts if v["status"] == "not_run"]
    overall = "fail" if failing else ("incomplete" if not_run else "pass")

    payload = {
        "meta": {
            "timestamp": TIMESTAMP,
            "commit": git_commit(),
            "network": args.network,
            "host": os.uname().nodename if hasattr(os, "uname") else "unknown",
            "python": sys.version.split()[0],
            "args": {k: v for k, v in vars(args).items()},
            "freeze": freeze_report(),
            "thresholds": thresholds,
            "environment": environment,
            "cache_bypass": not args.allow_cache,
        },
        "metrics": metrics,
        "verdicts": verdicts,
        "search": search,
        "extraction": extraction,
        "redundancy": redundancy,
        "honesty": honesty,
        "load": load,
        "tests": tests,
        "reference": reference,
        "overall_status": overall,
    }
    path = write_results(Path(args.out), payload)
    table = format_table(payload)
    print()
    print(table)
    print(f"\nresults: {path}")
    if payload["meta"]["freeze"]["changed_since_freeze"]:
        print("WARNING: frozen files changed since FREEZE.sha256: "
              + ", ".join(payload["meta"]["freeze"]["changed_since_freeze"]))
    if overall == "pass" and not args.no_benchmarks_md:
        append_benchmarks_md(payload, log)
    elif overall != "pass":
        print(f"overall: {overall.upper()} (failing: {', '.join(failing) or '-'}; "
              f"not run: {', '.join(not_run) or '-'}) — no row appended to BENCHMARKS.md")
    return 0 if overall == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
