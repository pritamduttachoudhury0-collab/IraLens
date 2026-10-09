# -*- coding: utf-8 -*-
"""The Half IraLens facade — the one and only AI-facing interface.

Everything the controlling AI can do with the Internet goes through this
class (directly, via the CLI, or via the MCP server). There is no other
entry point, and nothing below it is exposed: sources, the browser engine,
and their backends are internal implementation details.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from . import errors
from .config import Config
from .engine.native import BrowserEngine
from .model import Artifact
from .search.schema import SearchFilters, SearchOptions, SearchResponse

if TYPE_CHECKING:  # pragma: no cover
    from .research.schema import ResearchReport
from .security import normalize_public_http_url, public_message
from .session import Session
from .sources import ALL_SOURCES, get_source, route_url
from .sources.web import WebSource


class Context:
    """Shared services handed to sources (internal)."""

    def __init__(self, config: Config, session: Session, engine_factory) -> None:
        self.config = config
        self.session = session
        self._engine_factory = engine_factory

    def engine(self) -> BrowserEngine:
        return self._engine_factory()


class HalfIraLens:
    """One unified Internet-access system."""

    def __init__(
        self,
        config: Optional[Config] = None,
        session: Optional[Session] = None,
    ) -> None:
        self.config = config or Config()
        self.session = session or Session()
        self._engine: Optional[BrowserEngine] = None
        self._context = Context(self.config, self.session, self._engine_handle)

    # ------------------------------------------------------------------ core
    def _engine_handle(self) -> BrowserEngine:
        if self._engine is None:
            self._engine = BrowserEngine(config=self.config, session=self.session)
        return self._engine

    def engine(self) -> BrowserEngine:
        """The shared browser engine handle (advanced/internal use)."""
        return self._engine_handle()

    # ------------------------------------------------------------- discovery
    def search(self, query: str, limit: int = 8, backend: str = "") -> List[Artifact]:
        """Search the open web. Returns unified artifacts with provenance."""
        source = get_source("web-search")
        assert source is not None
        results = source.fetch(
            "query", {"query": query, "limit": limit, "backend": backend}, self._context
        )
        results = results if isinstance(results, list) else [results]
        self.session.last_query = query
        for artifact in results:
            self.session.record_discovery(
                artifact.url, title=artifact.title, source=artifact.source,
                found_via=f"search:{query}",
            )
        return results

    def search_api(
        self,
        query: str,
        filters: Optional[Union[Dict[str, Any], "SearchFilters"]] = None,
        options: Optional[Union[Dict[str, Any], "SearchOptions"]] = None,
    ) -> "SearchResponse":
        """Structured search: ranked results, per-engine outcomes, fallbacks,
        filter report, dedup log, cache status. `search()` stays the simple form.

        filters: date_from, date_to (yyyy-mm-dd), include_domains, exclude_domains,
                 file_type, language, region.
        options: max_results, engines, reformulate, cache (use|bypass|refresh).
        """
        from .search.schema import SearchFilters as _Filters, SearchOptions as _Options

        f = filters if isinstance(filters, _Filters) else _Filters.build(**(filters or {}))
        o = options if isinstance(options, _Options) else _Options(**{
            "max_results": 8, **(options or {}),
            "engines": tuple((options or {}).get("engines") or ()),
        })
        engine = self._search_engine()
        response = engine.search(query, filters=f, options=o, context=self._context)
        self.session.last_query = query
        from .search import to_artifacts as _to_artifacts
        for artifact in _to_artifacts(response, query):
            self.session.record_discovery(
                artifact.url, title=artifact.title, source=artifact.source,
                found_via=f"search:{query}",
            )
        return response

    def research(self, question: str, options: Optional[Dict[str, Any]] = None) -> "ResearchReport":
        """Multi-round research with a cited report.

        Plans queries, searches, reads the top sources (static reader, so each
        URL is sent to the reader service), evaluates evidence, flags possible
        contradictions, and returns statements that cite sources. Options:
        max_rounds, max_queries, min_sources, max_results, read_top_n.
        """
        from .research import ResearchPlanner
        from .research.schema import ResearchOptions
        from .settings import Settings

        settings = Settings.from_config(self.config)
        opts = ResearchOptions.build(
            options,
            max_rounds=settings.research_max_rounds,
            max_queries=settings.research_max_queries,
            min_sources=settings.research_min_sources,
            max_results=settings.search_max_results,
            read_top_n=settings.research_read_top_n,
        )
        engine = self._search_engine()

        def search(text: str) -> SearchResponse:
            return engine.search(text, options=SearchOptions(max_results=opts.max_results, reformulate=False),
                                 context=self._context)

        web = get_source("web")
        assert isinstance(web, WebSource)

        def reader(url: str) -> str:
            # Bound what the reader returns; the planner trims further for
            # the replay trace. This keeps a huge page out of memory.
            return web.read_url(url, self._context, mode="static",
                                max_chars=settings.research_page_chars * 4).content

        report = ResearchPlanner(settings, search, reader=reader).run(question, opts)
        self.session.last_query = question
        for src in report.sources:
            self.session.record_discovery(src["url"], title=src["title"], source="web-search",
                                          found_via=f"research:{question}")
        return report

    def _search_engine(self):
        source = get_source("web-search")
        assert source is not None
        return source.search_engine(self._context)

    def open(
        self,
        url: str,
        mode: str = "auto",
        max_chars: int = 20000,
        discovered_from: Optional[str] = None,
    ) -> Artifact:
        """Open any URL as one continuous operation.

        mode: auto    — specialized source if the URL matches one, else web
              browser — force full browser rendering
              static  — force the fast static reader
              source  — require the specialized source (error if none)
        """
        try:
            safe_url = normalize_public_http_url(url)
        except ValueError as exc:
            raise errors.SecurityBlockedError(
                "URL rejected by the security policy (only public http/https allowed)",
                detail=str(exc),
            ) from exc

        artifact: Optional[Artifact] = None
        source = route_url(safe_url)

        if mode in ("auto", "source") and source is not None:
            try:
                artifact = source.read_url(safe_url, self._context)
            except errors.OperationUnsupportedError:
                if mode == "source":
                    raise
            except (errors.AuthRequiredError, errors.SourceUnavailableError):
                if mode == "source":
                    raise
                # mode == "auto": fall through to the generic web path.
                artifact = None
        elif mode == "source":
            raise errors.OperationUnsupportedError(
                "no specialized source handles this URL", hint="use mode=auto or mode=browser"
            )

        if artifact is None:
            web = get_source("web")
            assert isinstance(web, WebSource)
            web_mode = "browser" if mode == "browser" else ("static" if mode == "static" else "auto")
            artifact = web.read_url(safe_url, self._context, mode=web_mode,
                                    max_chars=max(1, int(max_chars)))

        if discovered_from:
            artifact.discovered_from = discovered_from
        elif self.session.last_query and artifact.url in {
            d["url"] for d in self.session.discovered
        }:
            artifact.discovered_from = f"search:{self.session.last_query}"

        self.session.record_open(
            artifact.url, title=artifact.title, source=artifact.source,
            method=artifact.retrieval_method,
        )
        return artifact

    def read(self, url: str, mode: str = "auto", **kwargs: Any) -> Artifact:
        """Read a page (static-reader-first shorthand for `open`)."""
        if mode == "auto":
            mode = "static"
        return self.open(url, mode=mode, **kwargs)

    def scrape(self, urls: List[str], mode: str = "static") -> List[Any]:
        """Bulk-read many URLs; per-URL failures never abort the batch.

        Returns one entry per URL: an `Artifact` on success, or the unified
        error dict on failure.
        """
        results: List[Any] = []
        for url in urls:
            try:
                results.append(self.open(url, mode=mode))
            except errors.HalfIraLensError as exc:
                results.append({"url": url, **exc.to_dict()})
        return results

    # ------------------------------------------------------- source registry
    def fetch(self, source: str, op: str, **params: Any) -> Union[Artifact, List[Artifact], Any]:
        """Run a specialized source operation: fetch('github','search_repos',query=...)."""
        src = get_source(source)
        if src is None:
            raise errors.OperationUnsupportedError(
                f"unknown source '{source}'",
                hint=f"available: {', '.join(s.name for s in ALL_SOURCES)}",
            )
        result = src.fetch(op, params, self._context)
        items = result if isinstance(result, list) else [result] if isinstance(result, Artifact) else []
        for item in items:
            if isinstance(item, Artifact) and item.url:
                self.session.record_discovery(
                    item.url, title=item.title, source=item.source,
                    found_via=f"source:{source}:{op}",
                )
        return result

    def sources(self) -> List[Dict[str, Any]]:
        """Catalog of specialized sources with their operations."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "tier": s.tier,
                "operations": {
                    op: spec.get("description", "") for op, spec in s.operations.items()
                },
            }
            for s in ALL_SOURCES
        ]

    def source_status(self) -> Dict[str, Any]:
        """Health of every source (one doctor for the whole system)."""
        out: Dict[str, Any] = {}
        for s in ALL_SOURCES:
            try:
                health = s.health(self._context)
            except Exception as exc:
                out[s.name] = {"status": "error", "message": public_message(exc)}
                continue
            out[s.name] = health.to_dict()
        return out

    def doctor(self) -> Dict[str, Any]:
        """Unified capability report: engine + every source."""
        engine_health = self._engine_handle().health()
        return {"browser": engine_health, "sources": self.source_status()}

    # ---------------------------------------------------------------- browser
    def navigate(self, url: str, wait_until: str = "load") -> Any:
        try:
            safe_url = normalize_public_http_url(url)
        except ValueError as exc:
            raise errors.SecurityBlockedError("URL rejected by the security policy") from exc
        result = self.engine().navigate(safe_url, wait_until=wait_until)
        current = self.engine().current_url_title()
        self.session.record_open(
            current.get("url") or safe_url, title=current.get("title", ""),
            source="web", method="browser",
        )
        return result

    def back(self) -> Any:
        return self.engine().back()

    def forward(self) -> Any:
        return self.engine().forward()

    def reload(self) -> Any:
        return self.engine().reload()

    def snapshot(self, max_chars: int = 4000) -> Any:
        return self.engine().snapshot(max_chars)

    def page_markdown(self, max_chars: int = 20000) -> Any:
        return self.engine().markdown(max_chars)

    def links(self, limit: int = 100, internal_only: bool = False) -> Any:
        return self.engine().links(limit, internal_only)

    def click(self, selector: str) -> Any:
        return self.engine().click(selector)

    def fill(self, selector: str, value: str) -> Any:
        return self.engine().fill(selector, value)

    def type_text(self, text: str, selector: Optional[str] = None) -> Any:
        return self.engine().type_text(text, selector)

    def press_key(self, key: str, selector: Optional[str] = None) -> Any:
        return self.engine().press_key(key, selector)

    def select_option(self, selector: str, value: str) -> Any:
        return self.engine().select_option(selector, value)

    def scroll(self, direction: str = "down", amount: Optional[int] = None, selector: Optional[str] = None) -> Any:
        return self.engine().scroll(direction, amount, selector)

    def evaluate_js(self, expression: str) -> Any:
        return self.engine().evaluate_js(expression)

    def wait_for(self, selector: str, timeout: int = 30) -> Any:
        return self.engine().wait_for(selector, timeout)

    def wait_for_text(self, text: str, timeout: int = 30) -> Any:
        return self.engine().wait_for_text(text, timeout)

    def find_in_page(self, query: str, case_sensitive: bool = False) -> Any:
        return self.engine().find_in_page(query, case_sensitive)

    def forms_detect(self) -> Any:
        return self.engine().forms_detect()

    def forms_fill(self, values: Dict[str, str]) -> Any:
        return self.engine().forms_fill(values)

    def extract(self, schema: Dict[str, str]) -> Any:
        return self.engine().extract(schema)

    def count(self, selector: str) -> Any:
        return self.engine().count(selector)

    def attribute(self, selector: str, name: str) -> Any:
        return self.engine().attribute(selector, name)

    def interactive_elements(self) -> Any:
        return self.engine().interactive_elements()

    def screenshot(self, path: Optional[str] = None, width: Optional[int] = None, height: Optional[int] = None) -> Any:
        block = self.engine().screenshot(width, height)
        if path:
            saved = self.engine().save_media(block, Path(path).expanduser())
            return {"path": str(saved), "bytes": saved.stat().st_size}
        return block

    def pdf(self, path: Optional[str] = None, landscape: bool = False) -> Any:
        block = self.engine().pdf(landscape)
        if path:
            saved = self.engine().save_media(block, Path(path).expanduser())
            return {"path": str(saved), "bytes": saved.stat().st_size}
        return block

    # ------------------------------------------------------------ cookies etc
    def cookies_get(self) -> Any:
        return self.engine().cookies_get()

    def cookies_set(self, name: str, value: str, domain: str = "", path: str = "/") -> Any:
        return self.engine().cookies_set(name, value, domain, path)

    def cookies_clear(self) -> Any:
        return self.engine().cookies_clear()

    def storage_state(self) -> Any:
        return self.engine().storage_state()

    def set_storage_state(self, state: Dict[str, Any]) -> Any:
        return self.engine().set_storage_state(state)

    def network_log(self) -> Any:
        return self.engine().network_log()

    def console_log(self) -> Any:
        return self.engine().console_log()

    # ------------------------------------------------------------------- tabs
    def tab_new(self, url: Optional[str] = None) -> Any:
        if url:
            url = normalize_public_http_url(url)
        return self.engine().tab_new(url)

    def tab_list(self) -> Any:
        return self.engine().tab_list()

    def tab_switch(self, tab_id: str) -> Any:
        return self.engine().tab_switch(tab_id)

    def tab_close(self, tab_id: str) -> Any:
        return self.engine().tab_close(tab_id)

    # ----------------------------------------------------------------- system
    def session_state(self) -> Dict[str, Any]:
        return self.session.snapshot()

    def reset_session(self) -> None:
        self.session.reset()

    def configure(self, key: str, value: str) -> None:
        self.config.set(key, value)

    def close(self) -> None:
        if self._engine is not None:
            self._engine.close()
            self._engine = None

    def __enter__(self) -> "HalfIraLens":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
