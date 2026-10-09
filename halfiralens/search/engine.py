# -*- coding: utf-8 -*-
"""Unified search orchestrator.

Pipeline for one request:
  validate -> cache lookup -> reformulate -> per query: fallback chain
  -> post-filter -> sanitize + security scan -> dedup -> rank -> cache store

`SearchEngine` holds no per-request state, so one instance can serve many
requests. It is shared through `HalfIraLens`. Concurrency is capped by
`ConcurrencyGate` (Settings.search_max_concurrent).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence

from .. import content_guard
from ..cache import ResponseCache
from ..reliability import CircuitBreaker, ConcurrencyGate, RetryPolicy
from ..security import normalize_public_http_url
from ..settings import Settings
from .dedup import dedup
from .engines import LEGACY_ALIASES, build_backends
from .engines.base import SearchBackend
from .fallback import run_chain
from .querytext import sanitize_query
from .ranking import authority, combine, freshness, relevance, today_utc
from ..model import Artifact
from .reformulate import DEFAULT_SYNONYMS
from .reformulate import reformulate as _reformulate
from .schema import FilterError, RankedResult, SearchFilters, SearchHit, SearchOptions, SearchResponse
from .urls import canonical_url, domain_matches, domain_of

#: Demotion applied to the relevance factor of groups whose hits carry a
#: prompt-injection security flag. Flagged results are still returned —
#: never silently dropped — but they must not outrank clean content.
_FLAGGED_RELEVANCE_FACTOR = 0.5

_CACHE_NS = "search.v1"


class SearchEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        backends: Optional[Dict[str, SearchBackend]] = None,
        cache: Optional[ResponseCache] = None,
        gate: Optional[ConcurrencyGate] = None,
        breaker: Optional[CircuitBreaker] = None,
        synonyms: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self.settings = settings
        self.backends = backends or build_backends()
        self.cache = cache or ResponseCache(enabled=settings.cache_enabled)
        self.gate = gate or ConcurrencyGate(settings.search_max_concurrent, settings.search_acquire_timeout_seconds)
        self.synonyms = synonyms
        self.policy = RetryPolicy(settings.retry_max_attempts, settings.retry_base_delay_seconds,
                                  settings.retry_max_delay_seconds)
        self.breaker = breaker or CircuitBreaker(settings.breaker_failure_threshold,
                                                 settings.breaker_cooldown_seconds)

    # ------------------------------------------------------------ public
    def search(self, question: str, *, filters: Optional[SearchFilters] = None,
               options: Optional[SearchOptions] = None, context: Any = None) -> SearchResponse:
        # Query hygiene: strip control/invisible characters (bidi overrides can
        # make a query display differently from what is sent), normalize
        # whitespace, and enforce the length limit (D-068).
        try:
            question = sanitize_query(question, self.settings.search_max_query_chars)
        except ValueError as exc:
            raise FilterError(str(exc)) from exc
        filters = filters or SearchFilters()
        options = options or SearchOptions(max_results=self.settings.search_max_results)
        chain = self.resolve_chain(options.engines)

        payload = {
            "q": question, "filters": filters.to_dict(), "max": options.max_results,
            "engines": chain, "reformulate": options.reformulate, "min": self.settings.search_min_engines,
        }
        ttl = self.settings.cache_search_ttl_seconds
        if options.cache == "use":
            cached = self.cache.get(_CACHE_NS, payload, ttl)
            if cached is not None:
                value, age = cached
                response = SearchResponse.from_dict(value)
                response.cache = {"status": "hit", "age_seconds": round(age, 1), "fresh": True}
                return response

        with self.gate:
            response = self._run(question, filters, options, chain, context)

        if options.cache != "bypass":
            cacheable = bool(response.results) and not response.degraded
            stored = self.cache.put(_CACHE_NS, payload, response.to_dict(), cacheable=cacheable)
            response.cache = {"status": "miss" if options.cache == "use" else "refresh",
                              "stored": stored, "fresh": True}
        else:
            response.cache = {"status": "bypass", "stored": False, "fresh": True}
        return response

    def resolve_chain(self, requested: Sequence[str] = ()) -> List[str]:
        names = list(requested) or list(self.settings.search_engines)
        expanded: List[str] = []
        for name in names:
            for real in LEGACY_ALIASES.get(name, (name,)):
                if real not in self.backends:
                    raise FilterError(f"unknown search engine {name!r}; known: {sorted(self.backends)}")
                if real not in expanded:
                    expanded.append(real)
        return expanded

    # ----------------------------------------------------------- pipeline
    def _run(self, question: str, filters: SearchFilters, options: SearchOptions,
             chain: List[str], context: Any) -> SearchResponse:
        if options.reformulate:
            queries = _reformulate(question, self.settings.reformulate_max_queries,
                                   self.synonyms if self.synonyms is not None else _default_synonyms())
        else:
            queries = [{"text": question, "strategy": "original"}]

        all_hits: List[SearchHit] = []
        outcomes = []
        fallbacks: List[Dict[str, str]] = []
        used_queries: List[Dict[str, str]] = list(queries)
        unique_urls = set()

        def _one(q: Dict[str, str]):
            return run_chain(
                q["text"], filters, options.max_results, chain, self.backends, context,
                min_engines=self.settings.search_min_engines, policy=self.policy,
                breaker=self.breaker,
            )

        # Query variants run concurrently when allowed (D-069). Browser-backed
        # engines serialize their navigations on the shared engine inside
        # fetch_rendered_html; independent backends (CLI bridges) run truly in
        # parallel. Results are merged in query order, so output is
        # deterministic either way.
        if self.settings.search_parallel_queries and len(queries) > 1:
            workers = min(len(queries), max(1, self.settings.search_max_concurrent))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                chain_results = list(pool.map(_one, queries))
        else:
            chain_results = []
            for q in queries:
                chain_results.append(_one(q))
                for h in chain_results[-1].hits:
                    key = canonical_url(h.url)
                    if key:
                        unique_urls.add(key)
                if len(unique_urls) >= options.max_results:
                    break
            used_queries = used_queries[: len(chain_results)]
        for chain_result in chain_results:
            all_hits.extend(chain_result.hits)
            outcomes.extend(chain_result.outcomes)
            fallbacks.extend(chain_result.fallbacks)

        public_hits, rejected = self._public_only(all_hits)
        filtered, filter_report = self._post_filter(public_hits, filters)
        filter_report["non_public_dropped"] = rejected
        for hit in filtered:
            # Scan the raw text first: sanitizing strips invisible characters,
            # which is itself a signal worth reporting.
            hit.security_flags = content_guard.scan(f"{hit.title} {hit.snippet}")
            hit.title = content_guard.sanitize(hit.title, 200)
            hit.snippet = content_guard.sanitize(hit.snippet, 400)

        groups, dedup_log = dedup(filtered, self.settings.dedup_title_similarity)
        results = self._rank(question, groups, options.max_results)

        flags = sorted({f for r in results for f in r.security_flags})
        engine_answered = any(o.status in ("results", "empty") for o in outcomes)
        any_failed = any(o.status in ("failed", "skipped") for o in outcomes)
        if results:
            reason = "none"
        elif not engine_answered and any_failed:
            reason = "engines_failed"
        elif all_hits:
            # Hits existed but none survived the public-URL guard or the
            # filters: say so instead of claiming the web has no results.
            reason = "filtered_out"
        else:
            reason = "no_results"
        return SearchResponse(
            query=question,
            results=results,
            queries=used_queries,
            outcomes=outcomes,
            fallbacks=fallbacks,
            filters={"requested": filters.to_dict(), "report": filter_report},
            cache={},
            dedup_log=dedup_log,
            no_results_reason=reason,
            security_flags=flags,
            summary=_summarize(question, results, outcomes, reason, filter_report),
        )

    # --------------------------------------------------------- filtering
    @staticmethod
    def _public_only(hits: List[SearchHit]):
        """Drop hits whose URL is not a public http(s) target (localhost, private
        IPs, credentials, odd schemes). A SERP can be hostile; these must never
        reach the agent's next step as 'results'."""
        kept: List[SearchHit] = []
        rejected = 0
        for hit in hits:
            try:
                hit.url = normalize_public_http_url(hit.url)
            except ValueError:
                rejected += 1
                continue
            kept.append(hit)
        return kept, rejected

    @staticmethod
    def _post_filter(hits: List[SearchHit], f: SearchFilters):
        report: Dict[str, Any] = {"applied_post": [], "date_unknown_kept": 0, "dropped": 0}
        if f.include_domains:
            report["applied_post"].append("include_domains")
        if f.exclude_domains:
            report["applied_post"].append("exclude_domains")
        if f.file_type:
            report["applied_post"].append("file_type")
        if f.date_from or f.date_to:
            report["applied_post"].append("date")
        kept: List[SearchHit] = []
        for hit in hits:
            host = domain_of(hit.url)
            if f.include_domains and not any(domain_matches(host, d) for d in f.include_domains):
                report["dropped"] += 1
                continue
            if f.exclude_domains and any(domain_matches(host, d) for d in f.exclude_domains):
                report["dropped"] += 1
                continue
            if f.file_type and not hit.url.lower().split("?")[0].endswith("." + f.file_type):
                report["dropped"] += 1
                continue
            if f.date_from or f.date_to:
                if not hit.published_at:
                    report["date_unknown_kept"] += 1
                else:
                    day = hit.published_at[:10]
                    if (f.date_from and day < f.date_from) or (f.date_to and day > f.date_to):
                        report["dropped"] += 1
                        continue
            kept.append(hit)
        return kept, report

    # ----------------------------------------------------------- ranking
    def _rank(self, question: str, groups, max_results: int) -> List[RankedResult]:
        s = self.settings
        weights = {"relevance": s.weight_relevance, "authority": s.weight_authority,
                   "freshness": s.weight_freshness, "agreement": s.weight_agreement}
        engines_with_results = {h.engine for g in groups for h in g.hits}
        n_engines = max(1, len(engines_with_results))
        today = today_utc()
        ranked: List[RankedResult] = []
        for group in groups:
            hits = group.hits
            engines = sorted({h.engine for h in hits})
            ranks: Dict[str, int] = {}
            for h in hits:
                ranks[h.engine] = min(h.position, ranks.get(h.engine, h.position))
            best_rank = min(ranks.values())
            title = max((h.title for h in hits), key=len)
            snippet = max((h.snippet for h in hits), key=len)
            dates = sorted(h.published_at for h in hits if h.published_at)
            published = dates[0] if dates else None
            flags = sorted({f for h in hits for f in h.security_flags})
            rel = relevance(best_rank, title, snippet, question)
            # Low-quality handling: injection-flagged groups are demoted, not
            # dropped. The flag itself still travels with the result.
            if any(f.startswith("prompt_injection:") for f in flags):
                rel = round(rel * _FLAGGED_RELEVANCE_FACTOR, 4)
            factors = {
                "relevance": rel,
                "authority": authority(group.hits[0].url, s.authority_weights, s.default_authority),
                "freshness": freshness(published, today, s.freshness_half_life_days, s.neutral_freshness),
                "agreement": round(len(engines) / n_engines, 4),
            }
            queries = sorted({h.query for h in hits if h.query})
            ranked.append(RankedResult(
                title=title,
                url=group.hits[0].url,
                canonical_url=canonical_url(group.hits[0].url) or group.hits[0].url,
                snippet=snippet,
                score=combine(factors, weights),
                score_breakdown=factors,
                engines=engines,
                engine_ranks=ranks,
                published_at=published,
                date_status="known" if published else "unknown",
                queries=queries,
                security_flags=flags,
                merged_from=list(group.merged_urls),
            ))
        ranked.sort(key=lambda r: (-r.score, r.url))
        return ranked[:max_results]


def _default_synonyms() -> Dict[str, List[str]]:
    return DEFAULT_SYNONYMS


def _summarize(question: str, results: List[RankedResult], outcomes: List[Any],
               reason: str, filter_report: Dict[str, Any]) -> str:
    """One honest sentence about what this search produced.

    Agents can parse `outcomes` for detail; the summary exists so a caller
    that only reads one field still learns whether results are complete,
    degraded, filtered, or absent — and why.
    """
    failed = sorted({o.engine for o in outcomes if o.status in ("failed", "skipped")})
    served = sorted({o.engine for o in outcomes if o.status == "results"})
    if reason == "none":
        text = f"{len(results)} results from {', '.join(served) or 'search engines'}"
        if failed:
            text += f"; degraded: {', '.join(failed)} failed or were skipped"
        return text
    if reason == "filtered_out":
        dropped = filter_report.get("dropped", 0)
        non_public = filter_report.get("non_public_dropped", 0)
        return (f"no results left after filtering ({dropped} dropped by filters, "
                f"{non_public} non-public URLs removed)")
    if reason == "engines_failed":
        detail = ", ".join(f"{o.engine}: {o.kind}" for o in outcomes
                           if o.status in ("failed", "skipped"))
        return f"search failed: no engine produced results ({detail})"
    return "no results: every engine answered with zero hits"


def to_artifacts(response: SearchResponse, question: str) -> List[Artifact]:
    """Legacy shape: one `Artifact` per ranked result, provenance attached."""
    backend_label = "search:" + "+".join(sorted({o.engine for o in response.outcomes if o.status == "results"})
                                        or ["none"])
    out: List[Artifact] = []
    for rank, r in enumerate(response.results, start=1):
        out.append(Artifact(
            title=r.title,
            url=r.url,
            source="web-search",
            kind="search_result",
            content=r.snippet,
            metadata={"query": question, "rank": rank, "score": r.score,
                      "published_at": r.published_at, **({"snippet": r.snippet} if r.snippet else {})},
            retrieval_method=backend_label,
            discovered_from=f"search:{question}",
            provenance={
                "engines": r.engines,
                "engine_ranks": r.engine_ranks,
                "canonical_url": r.canonical_url,
                "score_breakdown": r.score_breakdown,
                "queries": r.queries,
                "merged_from": r.merged_from,
                "security_flags": r.security_flags,
                "date_status": r.date_status,
            },
        ))
    return out


__all__ = ["SearchEngine", "to_artifacts"]
