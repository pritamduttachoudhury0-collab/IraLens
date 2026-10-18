# -*- coding: utf-8 -*-
"""Unified search orchestrator.

Pipeline for one request:
  validate -> cache lookup -> reformulate -> per query: fallback chain
  -> post-filter -> sanitize + security scan -> dedup -> rank -> cache store

`SearchEngine` holds no per-request state, so one instance can serve many
requests. It is shared through `IraLens`. Concurrency is capped by
`ConcurrencyGate` (Settings.search_max_concurrent).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

from .. import content_guard
from ..cache import ResponseCache
from ..config import state_dir
from ..reliability import CircuitBreaker, ConcurrencyGate, PerSiteConcurrencyGate, PersistentCircuitBreaker, RetryPolicy
from ..security import normalize_public_http_url
from ..settings import Settings
from .coverage import assess_coverage
from .dedup import dedup
from .engines import LEGACY_ALIASES, build_backends
from .engines.base import SearchBackend
from .fallback import run_chain
from .intent import classify_query, intent_multiplier
from .querytext import parse_site_scopes, sanitize_query
from .ranking import authority, combine, freshness, relevance, today_utc
from ..model import Artifact
from .reformulate import DEFAULT_SYNONYMS
from .reformulate import reformulate as _reformulate
from .schema import FilterError, RankedResult, SearchFilters, SearchHit, SearchOptions, SearchResponse
from .freshness_extract import extract_date
from .urls import (canonical_url, domain_matches, domain_of, is_test_host,
                    scope_matches, unwrap_wrapper)

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
        site_gate: Optional[PerSiteConcurrencyGate] = None,
        breaker: Optional[CircuitBreaker] = None,
        synonyms: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self.settings = settings
        self.backends = backends or build_backends()
        self.cache = cache or ResponseCache(enabled=settings.cache_enabled)
        self.gate = gate or ConcurrencyGate(settings.search_max_concurrent, settings.search_acquire_timeout_seconds)
        self.site_gate = site_gate or PerSiteConcurrencyGate(settings.search_per_site_concurrent, settings.search_acquire_timeout_seconds)
        self.synonyms = synonyms
        self.policy = RetryPolicy(settings.retry_max_attempts, settings.retry_base_delay_seconds,
                                  settings.retry_max_delay_seconds)
        self.breaker = breaker or PersistentCircuitBreaker(
            settings.breaker_failure_threshold,
            settings.breaker_cooldown_seconds,
            blocked_cooldown=settings.breaker_block_cooldown_seconds,
            state_path=state_dir() / "search-health.sqlite3",
        )

    # ------------------------------------------------------------ public
    def search(self, question: str, *, filters: Optional[SearchFilters] = None,
               options: Optional[SearchOptions] = None, context: Any = None) -> SearchResponse:
        # Query hygiene: strip control/invisible characters (bidi overrides can
        # make a query display differently from what is sent), normalize
        # whitespace, and enforce the length limit.
        try:
            question = sanitize_query(question, self.settings.search_max_query_chars)
        except ValueError as exc:
            raise FilterError(str(exc)) from exc
        filters = filters or SearchFilters()
        options = options or SearchOptions(max_results=self.settings.search_max_results)
        chain, readiness = self.resolve_chain_with_readiness(options.engines)

        payload = {
            "q": question, "filters": filters.to_dict(), "max": options.max_results,
            "engines": chain, "reformulate": options.reformulate, "min": self.settings.search_min_engines,
            "offset": options.offset,
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
            response = self._run(question, filters, options, chain, context,
                                 readiness=readiness)

        if options.cache != "bypass":
            cacheable = bool(response.results) and not response.degraded
            stored = self.cache.put(_CACHE_NS, payload, response.to_dict(), cacheable=cacheable)
            response.cache = {"status": "miss" if options.cache == "use" else "refresh",
                              "stored": stored, "fresh": True}
        else:
            response.cache = {"status": "bypass", "stored": False, "fresh": True}
        return response

    def resolve_chain(self, requested: Sequence[str] = ()) -> List[str]:
        """The effective engine chain (kept for callers that want only it)."""
        return self.resolve_chain_with_readiness(requested)[0]

    def resolve_chain_with_readiness(self, requested: Sequence[str] = ()) -> tuple:
        """(chain, readiness) — configured engines that can actually serve.

        An engine whose optional prerequisite is missing is *not configured*,
        not *failing*. It is dropped from the effective chain and reported
        separately, so the default chain is not permanently degraded by an
        optional bridge nobody installed.
        """
        explicit = bool(requested)
        names = list(requested) or list(self.settings.search_engines)
        if not explicit and self.settings.searxng_instances and "searxng" not in names:
            # A private metasearch instance joins the free-source chain only
            # when the operator explicitly configured one.
            insert_at = names.index("bing") if "bing" in names else len(names)
            names.insert(insert_at, "searxng")
        if (not explicit and self.settings.backup_search_provider.strip()
                and self.settings.backup_search_api_key.strip()
                and "paid-backup" not in names):
            names.append("paid-backup")
        expanded: List[str] = []
        for name in names:
            for real in LEGACY_ALIASES.get(name, (name,)):
                if real not in self.backends:
                    raise FilterError(f"unknown search engine {name!r}; known: {sorted(self.backends)}")
                if real not in expanded:
                    expanded.append(real)
        readiness: Dict[str, Any] = {}
        ready: List[str] = []
        for name in expanded:
            backend = self.backends[name]
            try:
                available, reason = backend.availability()
            except Exception:  # pragma: no cover - defensive
                available, reason = True, ""
            readiness[name] = {"available": bool(available),
                               "reason": reason,
                               "paginated": bool(getattr(backend, "paginated", False))}
            if available:
                ready.append(name)
            elif explicit:
                # An explicitly requested engine that cannot serve is still
                # reported, so the caller is not silently given less than
                # they asked for.
                readiness[name]["requested_explicitly"] = True
        return ready, readiness

    # ----------------------------------------------------------- pipeline
    def _run(self, question: str, filters: SearchFilters, options: SearchOptions,
             chain: List[str], context: Any,
             readiness: Optional[Dict[str, Any]] = None) -> SearchResponse:
        # A `site:` operator in the query is an explicit scope. It becomes a
        # structured filter (so engines that can scope natively do) *and* it
        # is enforced on the results (so engines that cannot, like Wikipedia,
        # do not leak off-scope hits).
        scope_scopes, _cleaned = parse_site_scopes(question)
        # The scope keeps its path: "site:github.com/scipy/scipy" is a
        # repository, not a website. The domain filter stays host-level,
        # because that is what a domain filter means.
        scope_domains = [scope.split("/", 1)[0] for scope in scope_scopes]
        if scope_domains and not filters.include_domains:
            filters = replace(filters, include_domains=tuple(scope_domains))
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
                breaker=self.breaker, site_gate=self.site_gate,
                offset=options.offset,
            )

        # Query variants run concurrently when allowed. Browser-backed
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
        filtered, filter_report = self._post_filter(public_hits, filters, scope_scopes)

        # Cross-query de-duplication: the caller supplies the URLs it has
        # already collected, and they are dropped here. Dedup inside one
        # response cannot stop a sweep from returning (and re-paying for)
        # the same pages over and over.
        if options.exclude_urls:
            seen = {str(u) for u in options.exclude_urls}
            canonical_seen = {canonical_url(u) or str(u) for u in options.exclude_urls}
            before = len(filtered)
            filtered = [h for h in filtered
                        if h.url not in seen and (canonical_url(h.url) or h.url) not in canonical_seen]
            dropped = before - len(filtered)
            if dropped:
                filter_report["excluded_seen"] = dropped
                filter_report.setdefault("applied_post", []).append("exclude_urls")
        filter_report["non_public_dropped"] = rejected
        for hit in filtered:
            # Scan the raw text first: sanitizing strips invisible characters,
            # which is itself a signal worth reporting.
            hit.security_flags = content_guard.scan(f"{hit.title} {hit.snippet}")
            if is_test_host(hit.url) and "test_host" not in hit.security_flags:
                hit.security_flags = sorted({*hit.security_flags, "test_host"})
            if not hit.published_at:
                found, source = extract_date(hit.snippet, hit.url)
                if found:
                    hit.published_at = found
                    hit.raw["date_evidence"] = source
                    # A date recovered from the page content is a date *about
                    # the content*; it is a publication-ish date, not the
                    # source's revision timestamp.
                    hit.date_kind = "publication"
            hit.title = content_guard.sanitize(hit.title, 200)
            hit.snippet = content_guard.sanitize(hit.snippet, 400)

        groups, dedup_log = dedup(filtered, self.settings.dedup_title_similarity)
        results = self._rank(question, groups, options.max_results)

        flags = sorted({f for r in results for f in r.security_flags})
        engine_answered = any(o.status in ("results", "empty") for o in outcomes)
        any_failed = any(o.status in ("failed", "skipped") for o in outcomes)
        coverage = assess_coverage(question, results)
        if results:
            # Results are not the same as an answer. When nothing in the
            # result set addresses the question, the response must not
            # claim a clean "none".
            reason = "none" if coverage.get("status") != "low" else "low_coverage"
        elif not engine_answered and any_failed:
            reason = "engines_failed"
        elif all_hits:
            # Hits existed but none survived the public-URL guard or the
            # filters: say so instead of claiming the web has no results.
            reason = "filtered_out"
        else:
            reason = "no_results"
        shortfall = ""
        if 0 < len(results) < options.max_results:
            merges = sum(1 for e in dedup_log if e.get("action") == "merge")
            intent = classify_query(question)
            wiki_cap_applies = intent.answer_type in (
                "procedure", "statistic", "current_event", "comparison", "error")
            wiki_groups = sum(1 for g in groups if domain_of(g.hits[0].url).endswith("wikipedia.org"))
            if wiki_cap_applies and wiki_groups > 1:
                shortfall = "capped"
            elif merges >= (options.max_results - len(results)) and merges:
                shortfall = "deduped"
            elif filter_report.get("dropped", 0):
                shortfall = "filtered"
            else:
                shortfall = "exhausted"
        summary = _summarize(question, results, outcomes, reason, filter_report)
        if shortfall:
            summary += f"; shortfall: {shortfall} ({len(results)}/{options.max_results})"

        # Answer coverage: retrieval completed is not the same as the question
        # being addressed. The verdict travels with the results.
        if coverage.get("status") == "low" and results:
            summary += f"; coverage: {coverage.get('note')}"
        not_configured = sorted(
            name for name, info in (readiness or {}).items()
            if not info.get("available"))
        if not_configured:
            summary += (f"; not configured (skipped, not failed): {', '.join(not_configured)}")
        return SearchResponse(
            query=question,
            results=results,
            shortfall_reason=shortfall,
            queries=used_queries,
            outcomes=outcomes,
            fallbacks=fallbacks,
            filters={"requested": filters.to_dict(), "report": filter_report},
            cache={},
            dedup_log=dedup_log,
            no_results_reason=reason,
            security_flags=flags,
            summary=summary,
            engine_readiness=readiness or {},
            coverage=coverage,
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
                hit.url = normalize_public_http_url(unwrap_wrapper(hit.url))
            except ValueError:
                rejected += 1
                continue
            kept.append(hit)
        return kept, rejected

    @staticmethod
    def _post_filter(hits: List[SearchHit], f: SearchFilters,
                     scope_domains: Sequence[str] = ()):
        report: Dict[str, Any] = {"applied_post": [], "date_unknown_kept": 0, "dropped": 0}
        if f.include_domains:
            report["applied_post"].append("include_domains")
        if scope_domains:
            report["applied_post"].append("site_operator")
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
            # The `site:` operator is an explicit scope: a result outside it
            # is not a result for this query, however well it ranked.
            if scope_domains and not any(scope_matches(hit.url, d)
                                         for d in scope_domains):
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
                    # A recency window cannot be enforced by keeping results
                    # whose date is unknown — unless the caller says to keep
                    # them (the default, so the filter never hides results
                    # silently).
                    if f.exclude_unknown_dates:
                        report["dropped"] += 1
                        report["date_unknown_dropped"] = report.get("date_unknown_dropped", 0) + 1
                        continue
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
        intent = classify_query(question)
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
            # A revision time is reported separately: it must never be
            # promoted to a publication date, but it is still useful
            # provenance ("this article was last edited then").
            modified = next((h.modified_at for h in hits if h.modified_at), None)
            # A real publication date always wins over a revision stamp.
            date_kind = ("publication" if any(h.date_kind == "publication" for h in hits)
                         else next((h.date_kind for h in hits if h.date_kind), ""))
            flags = sorted({f for h in hits for f in h.security_flags})
            rel = relevance(best_rank, title, snippet, question, url=group.hits[0].url,
                            answer_type=intent.answer_type)
            rel = round(min(1.0, rel * intent_multiplier(group.hits[0].url, intent)), 4)
            # Low-quality handling: injection-flagged groups are demoted, not
            # dropped. The flag itself still travels with the result.
            if any(f.startswith("prompt_injection:") for f in flags):
                rel = round(rel * _FLAGGED_RELEVANCE_FACTOR, 4)
            if "test_host" in flags:
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
                date_evidence=next((str(h.raw.get("date_evidence")) for h in hits
                                    if h.raw.get("date_evidence")), ""),
                modified_at=modified,
                date_kind=date_kind,
            ))
        ranked.sort(key=lambda r: (-r.score, r.url))
        # D5: Wikipedia is an entity/definition source. For procedure,
        # statistic, current-event, comparison and error questions it must not
        # fill the top slots with hub articles. Definitional/entity queries
        # keep full Wikipedia access.
        if intent.answer_type in ("procedure", "statistic", "current_event", "comparison", "error"):
            wiki_cap = 1
        elif intent.answer_type in ("definition", "entity_lookup"):
            wiki_cap = max_results
        else:
            wiki_cap = 2
        capped: List[RankedResult] = []
        wiki_seen = 0
        for r in ranked:
            if domain_of(r.url).endswith("wikipedia.org"):
                wiki_seen += 1
                if wiki_seen > wiki_cap:
                    continue
            capped.append(r)
        # No backfill beyond the cap: a short, honest list (reported via the
        # D6 shortfall reason) beats filling a procedure slot with hubs.
        return capped[:max_results]


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
    if reason == "low_coverage":
        return (f"{len(results)} results, but they do not address the question; "
                f"treat them as generic filler, not evidence")
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
        # `published_at` alone is not auditable: a caller cannot tell whether
        # the date came from a provider field, was parsed out of a snippet, or
        # was guessed from the URL path. The evidence and the kind travel with
        # it, so every non-null date that affects freshness is traceable.
        metadata: Dict[str, Any] = {
            "query": question, "rank": rank, "score": r.score,
            "published_at": r.published_at,
            "date_status": r.date_status,
        }
        if r.snippet:
            metadata["snippet"] = r.snippet
        if r.date_evidence:
            metadata["date_evidence"] = r.date_evidence
        if r.date_kind:
            metadata["date_kind"] = r.date_kind
        if r.modified_at:
            metadata["modified_at"] = r.modified_at
        provenance: Dict[str, Any] = {
            "engines": r.engines,
            "engine_ranks": r.engine_ranks,
            "canonical_url": r.canonical_url,
            "score_breakdown": r.score_breakdown,
            "queries": r.queries,
            "merged_from": r.merged_from,
            "security_flags": r.security_flags,
            "date_status": r.date_status,
            "date_evidence": r.date_evidence,
            "date_kind": r.date_kind,
            # A revision stamp is reported separately and is never promoted to
            # a publication date; keeping both lets a consumer see the
            # difference instead of inferring it.
            "modified_at": r.modified_at,
        }
        out.append(Artifact(
            title=r.title,
            url=r.url,
            source="web-search",
            kind="search_result",
            content=r.snippet,
            metadata=metadata,
            retrieval_method=backend_label,
            discovered_from=f"search:{question}",
            provenance=provenance,
        ))
    return out


__all__ = ["SearchEngine", "to_artifacts"]
