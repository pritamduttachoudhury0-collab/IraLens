# -*- coding: utf-8 -*-
"""Research planner: an evidence-driven loop with recorded stop reasons.

Each round:
  1. the Expander proposes queries (round 1: the question and its reformulations;
     later rounds: queries for contested subjects)
  2. queries run through `search` until the budget is spent
  3. the top unread sources are read through `reader` (page text, not just snippets)
  4. claims, contradictions, and statements are recomputed from all evidence
  5. stop when coverage (distinct domains / min_sources) reaches the threshold,
     the budget is spent, no new sources appear, or max_rounds is reached

The report records every step and page in `trace`. `ResearchPlanner.replay()`
rebuilds the same report from that trace with no network access.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import content_guard
from ..errors import PageUnavailableError, SourceUnavailableError
from ..search.schema import FilterError, SearchResponse
from ..search.urls import domain_of
from ..search.ranking import today_utc
from ..search.reformulate import reformulate
from ..settings import Settings
from .contradictions import detect
from .evidence import extract_claims, score_source
from .provenance import ProvenanceGraph
from .schema import Claim, Contradiction, EvidenceSource, ResearchOptions, ResearchReport, Statement
from .synthesis import build_statements

SearchFn = Callable[[str], SearchResponse]
ReadFn = Callable[[str], str]

STOP_COVERAGE = "coverage_reached"
STOP_BUDGET = "budget_exhausted"
STOP_NO_NEW = "no_new_sources"
STOP_FAILED = "search_failed"
STOP_ROUNDS = "max_rounds"


class DeterministicExpander:
    """Default query expander: reformulations first, then contested-subject queries."""

    def __init__(self, settings: Settings) -> None:
        self.n = max(1, settings.reformulate_max_queries)

    def expand(self, question: str, round_no: int, used, contested_terms) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        if round_no == 1:
            out.extend(reformulate(question, max_queries=self.n))
            return out
        for terms in list(contested_terms)[:3]:
            if terms:
                out.append({"text": " ".join(list(terms)[:4]) + " evidence", "strategy": "contested"})
        out.extend(reformulate(question, max_queries=self.n * 2))
        return out


def source_id(canonical_url: str) -> str:
    return "s" + hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:10]


class ResearchPlanner:
    def __init__(
        self,
        settings: Settings,
        search: SearchFn,
        *,
        reader: Optional[ReadFn] = None,
        expander=None,
        synthesizer=None,
        today_fn: Callable[[], date] = today_utc,
    ) -> None:
        self.settings = settings
        self.search = search
        self.reader = reader
        self.expander = expander or DeterministicExpander(settings)
        self.synthesizer = synthesizer
        self.today_fn = today_fn

    # ----------------------------------------------------------------- run
    def run(self, question: str, options: ResearchOptions) -> ResearchReport:
        question = " ".join((question or "").split())
        if not question:
            raise FilterError("research requires a non-empty question")
        s = self.settings
        budget = min(options.max_queries, s.research_max_queries)
        today = self.today_fn()
        sources: Dict[str, EvidenceSource] = {}
        steps: List[Dict[str, Any]] = []
        rounds_log: List[Dict[str, Any]] = []
        pages: Dict[str, str] = {}
        used: List[str] = []
        contested: List[List[str]] = []
        stop = STOP_ROUNDS
        rounds_run = 0

        for round_no in range(1, options.max_rounds + 1):
            rounds_run = round_no
            new_sources = 0
            exhausted = False
            round_failures = 0
            candidates = [dict(c) for c in self.expander.expand(question, round_no, used, contested)]
            # Record what the expander proposed: replay must reuse these decisions,
            # not re-derive them with whatever expander the replaying caller has.
            rounds_log.append({"round": round_no, "candidates": candidates})
            for cand in candidates:
                text = (cand.get("text") or "").strip()
                if not text or text.lower() in {u.lower() for u in used}:
                    continue
                if len(used) >= budget:
                    exhausted = True
                    break
                used.append(text)
                try:
                    response = self.search(text)
                except Exception as exc:  # recorded, never silently dropped
                    steps.append({"round": round_no, "query": text, "strategy": cand.get("strategy", ""),
                                  "error": str(exc)[:200]})
                    round_failures += 1
                    continue
                steps.append({"round": round_no, "query": text, "strategy": cand.get("strategy", ""),
                              "response": response.to_dict()})
                round_failures += sum(1 for o in response.outcomes if o.status in ("failed", "skipped"))
                for r in response.results:
                    sid = source_id(r.canonical_url)
                    src = sources.get(sid)
                    if src is None:
                        src = EvidenceSource(
                            id=sid, url=r.url, canonical_url=r.canonical_url, title=r.title,
                            domain=domain_of(r.url), snippet=r.snippet, published_at=r.published_at,
                            engines=list(r.engines), security_flags=list(r.security_flags),
                        )
                        sources[sid] = src
                        new_sources += 1
                    else:
                        src.engines = sorted(set(src.engines) | set(r.engines))
                        src.security_flags = sorted(set(src.security_flags) | set(r.security_flags))
                        if len(r.snippet) > len(src.snippet):
                            src.snippet = r.snippet
                    if round_no not in src.rounds:
                        src.rounds.append(round_no)
                    if text not in src.queries:
                        src.queries.append(text)

            self._read_top(question, sources, options.read_top_n, pages, today)
            analysis = self.analyze(question, sources, today)
            contested = [c.subject_terms for c in analysis[1]]
            coverage = self._coverage(sources, options.min_sources)
            if coverage >= s.research_coverage_threshold:
                stop = STOP_COVERAGE
                break
            if exhausted:
                stop = STOP_BUDGET
                break
            if new_sources == 0:
                # Distinguish "nothing new to find" from "every search failed".
                stop = STOP_FAILED if (round_failures and not sources) else STOP_NO_NEW
                break

        claims, contradictions, statements = self.analyze(question, sources, today)
        coverage = self._coverage(sources, options.min_sources)
        graph = self._graph(question, steps, sources, claims, contradictions, statements)
        flags = sorted({f for s_ in sources.values() for f in s_.security_flags}
                       | {f for c in claims for f in c.security_flags})
        trace = {"steps": steps, "pages": pages, "rounds": rounds_log, "today": today.isoformat()}
        return ResearchReport(
            question=question,
            stop_reason=stop,
            rounds_run=rounds_run,
            coverage=round(coverage, 4),
            queries=list(used),
            sources=[self._source_dict(s_) for s_ in sources.values()],
            claims=[c.to_dict() for c in claims],
            contradictions=[c.to_dict() for c in contradictions],
            statements=[st.to_dict() for st in statements],
            limitations=self._limitations(steps, sources, contradictions, stop, coverage, options),
            security_flags=flags,
            provenance=graph.to_dict(),
            options={"max_rounds": options.max_rounds, "max_queries": budget,
                     "min_sources": options.min_sources, "read_top_n": options.read_top_n},
            trace=trace,
        )

    # ------------------------------------------------------------ replay
    @classmethod
    def replay(cls, report: Dict[str, Any], settings: Settings) -> "ResearchReport":
        """Rebuild a report from its trace without touching the network."""
        trace = report["trace"]
        by_query: Dict[str, Dict[str, Any]] = {}
        for step in trace["steps"]:
            if "response" in step and step["query"] not in by_query:
                by_query[step["query"]] = step["response"]

        def search(text: str) -> SearchResponse:
            if text in by_query:
                return SearchResponse.from_dict(by_query[text])
            raise SourceUnavailableError("replay: query was not recorded", detail=text)

        def read(url: str) -> str:
            if url in trace["pages"]:
                return trace["pages"][url]
            raise PageUnavailableError("replay: page was not recorded", detail=url)

        rounds = {r["round"]: r["candidates"] for r in trace.get("rounds", [])}

        class Recorded:
            def expand(self, question, round_no, used, contested):
                return rounds.get(round_no, [])

        day = date.fromisoformat(trace["today"])
        planner = cls(settings, search, reader=read, expander=Recorded(), today_fn=lambda: day)
        question = report["question"]
        return planner.run(question, ResearchOptions(**report["options"]))

    # -------------------------------------------------------------- pieces
    def _read_top(self, question: str, sources: Dict[str, EvidenceSource], top_n: int,
                  pages: Dict[str, str], today: date) -> None:
        for src in sources.values():
            src.score, src.score_breakdown = score_source(
                question, url=src.url, title=src.title, snippet=src.snippet,
                content=src.content, published_at=src.published_at, flags=src.security_flags,
                settings=self.settings, today=today,
            )
        if not self.reader or top_n <= 0:
            return
        candidates = sorted((s_ for s_ in sources.values() if s_.read_status == "snippet_only"),
                            key=lambda s_: (-s_.score, s_.url))[:top_n]
        for src in candidates:
            try:
                content = self.reader(src.url) or ""
            except Exception:
                content = ""
            if content.strip():
                # Truncate once, at read time, so the analysis and the replay
                # trace see exactly the same text.
                src.content = content[: self.settings.research_page_chars]
                src.read_status = "read"
                pages[src.url] = src.content
            else:
                src.read_status = "read_failed"

    def analyze(self, question: str, sources: Dict[str, EvidenceSource], today: date
                ) -> Tuple[List[Claim], List[Contradiction], List[Statement]]:
        for src in sources.values():  # re-score: content may have changed
            src.score, src.score_breakdown = score_source(
                question, url=src.url, title=src.title, snippet=src.snippet, content=src.content,
                published_at=src.published_at, flags=src.security_flags, settings=self.settings, today=today,
            )
        claims: List[Claim] = []
        for src in sources.values():
            for item in extract_claims(question, src.content or src.snippet):  # type: Dict[str, Any]
                text = str(item["text"])
                claims.append(Claim(
                    id=f"c{len(claims) + 1}",
                    text=content_guard.sanitize(text, 400),
                    source_id=src.id,
                    subject_terms=list(item["subject_terms"]),
                    numbers=list(item["numbers"]),
                    negated=bool(item["negated"]),
                    security_flags=content_guard.scan(text),
                ))
        domains = {sid: s_.domain for sid, s_ in sources.items()}
        contradictions = detect(claims, domains, self.settings.contradiction_similarity)
        for i, c in enumerate(contradictions, start=1):
            c.id = f"x{i}"
        statements = build_statements(claims, contradictions, sources, self.settings)
        if self.synthesizer is not None:
            statements = self._llm_statements(question, claims, contradictions, sources) or statements
        return claims, contradictions, statements

    def _llm_statements(self, question, claims, contradictions, sources) -> Optional[List[Statement]]:
        try:
            proposed = self.synthesizer.synthesize(question, claims, contradictions, sources)
        except Exception:
            return None
        valid_ids = {c.id for c in claims}
        checked = [st for st in proposed if st.claim_ids and set(st.claim_ids) <= valid_ids]
        if not checked or len(checked) != len(proposed):
            return None  # any invented claim id rejects the whole proposal
        for i, st in enumerate(checked, start=1):
            st.id = f"st{i}"
            st.source_ids = list(dict.fromkeys(c.source_id for c in claims if c.id in st.claim_ids))
            st.distinct_domains = len({sources[s].domain for s in st.source_ids if s in sources})
        return checked

    @staticmethod
    def _coverage(sources: Dict[str, EvidenceSource], min_sources: int) -> float:
        domains = {s_.domain for s_ in sources.values() if s_.score_breakdown.get("relevance", 0) > 0}
        return min(1.0, len(domains) / max(1, min_sources))

    @staticmethod
    def _source_dict(src: EvidenceSource) -> Dict[str, Any]:
        return src.to_dict()

    def _limitations(self, steps, sources, contradictions, stop, coverage, options) -> List[str]:
        out = ["No language model was used: claims are sentence-level heuristics and statements are "
               "template-based. Treat the findings as leads to verify, not conclusions."]
        failed = [st for st in steps if "error" in st]
        bad_outcomes = sum(1 for st in steps for o in st.get("response", {}).get("outcomes", [])
                           if o.get("status") in ("failed", "skipped"))
        if failed or bad_outcomes:
            out.append(f"{len(failed) + bad_outcomes} search attempt(s) failed or were skipped; see trace.")
        snippet_only = sum(1 for s_ in sources.values() if s_.read_status == "snippet_only")
        if snippet_only:
            out.append(f"{snippet_only} source(s) were used from search snippets only (page not read).")
        read_failed = sum(1 for s_ in sources.values() if s_.read_status == "read_failed")
        if read_failed:
            out.append(f"{read_failed} page(s) could not be read.")
        if contradictions:
            out.append("Contradictions are heuristic candidates, not verified conflicts.")
        if not sources:
            out.append("No sources were found for this question.")
        if stop != STOP_COVERAGE:
            out.append(f"Stopped on '{stop}' with coverage {coverage:.2f} "
                       f"(target {self.settings.research_coverage_threshold}).")
        return out

    @staticmethod
    def _graph(question, steps, sources, claims, contradictions, statements) -> ProvenanceGraph:
        g = ProvenanceGraph()
        g.node("question", "question", text=question)
        query_nodes: Dict[str, str] = {}
        for i, st in enumerate(steps, start=1):
            qid = f"q:{i}"
            query_nodes.setdefault(st["query"], qid)
            g.node(qid, "query", text=st["query"], strategy=st.get("strategy", ""), round=st["round"])
            g.edge("question", qid, "issued")
            for r in st.get("response", {}).get("results", []):
                sid = source_id(r["canonical_url"])
                if sid in sources:
                    g.edge(qid, f"src:{sid}", "returned")
        for sid, src in sources.items():
            g.node(f"src:{sid}", "source", url=src.url, title=src.title, read_status=src.read_status)
        for c in claims:
            g.node(f"claim:{c.id}", "claim", text=c.text)
            g.edge(f"src:{c.source_id}", f"claim:{c.id}", "states")
        for st in statements:
            g.node(f"stmt:{st.id}", "statement", text=st.text, status=st.status, confidence=st.confidence)
            for cid in st.claim_ids:
                g.edge(f"claim:{cid}", f"stmt:{st.id}", "supports")
        for x in contradictions:
            g.node(f"x:{x.id}", "contradiction", contradiction_kind=x.kind, explanation=x.explanation)
            for cid in x.claim_ids:
                g.edge(f"claim:{cid}", f"x:{x.id}", "conflicts_with")
        return g
