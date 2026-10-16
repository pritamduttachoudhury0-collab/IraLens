# -*- coding: utf-8 -*-
"""Small, dependency-free HTTP API for self-hosted IraLens.

The native `/v1/search` route returns the complete structured SearchResponse.
`POST /search` also speaks a Serper-shaped basic web-search request/response
(`q`/`num` -> `organic`) so many agents need only change their base URL and
replace the provider credential with their self-hosted service token.

This is a lightweight single-node server, not a managed service. It provides
bounded request concurrency, optional bearer/X-API-KEY authentication,
health checks and process-local Prometheus metrics. Put it behind TLS and a
reverse proxy before exposing it to the public Internet.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import logging
import os
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .errors import IraLensError
from .security import public_message

_LOG = logging.getLogger("iralens.api")
_MAX_BODY_BYTES = 1024 * 1024
_HISTOGRAM_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
_METRIC_ROUTES = {
    "/health", "/healthz", "/v1/health", "/v1/search", "/search",
    "/v1/read", "/v1/health/search", "/metrics",
}


def _metric_route(route: str) -> str:
    """Collapse arbitrary paths to a fixed, low-cardinality metric label."""
    return route if route in _METRIC_ROUTES else "other"


def _metric_method(method: str) -> str:
    return method if method in ("GET", "POST") else "OTHER"


def _integer_input(value: Any, name: str) -> int:
    """Accept integer JSON values and decimal query-string values only."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        digits = text[1:] if text[:1] in ("+", "-") else text
        if digits and digits.isdecimal():
            return int(text)
    raise ValueError(f"{name} must be an integer")


class APIMetrics:
    """Low-cardinality, query-free operational counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: Counter = Counter()
        self._searches: Counter = Counter()
        self._search_buckets = [0] * len(_HISTOGRAM_BUCKETS)
        self._search_count = 0
        self._search_sum = 0.0

    def request(self, method: str, route: str, status: int) -> None:
        with self._lock:
            self._requests[(_metric_method(method), _metric_route(route), str(status))] += 1

    def search(self, duration: float, outcome: str) -> None:
        with self._lock:
            self._searches[outcome] += 1
            self._search_count += 1
            self._search_sum += duration
            for index, bound in enumerate(_HISTOGRAM_BUCKETS):
                if duration <= bound:
                    self._search_buckets[index] += 1

    def render(self) -> str:
        with self._lock:
            requests = list(self._requests.items())
            searches = dict(self._searches)
            buckets = list(self._search_buckets)
            count, total = self._search_count, self._search_sum
        lines = [
            "# HELP iralens_http_requests_total HTTP requests served by this process.",
            "# TYPE iralens_http_requests_total counter",
        ]
        for (method, route, status), value in sorted(requests):
            lines.append(
                f'iralens_http_requests_total{{method="{method}",route="{route}",status="{status}"}} {value}'
            )
        lines += [
            "# HELP iralens_search_requests_total Search requests by outcome.",
            "# TYPE iralens_search_requests_total counter",
        ]
        for outcome, value in sorted(searches.items()):
            lines.append(f'iralens_search_requests_total{{outcome="{outcome}"}} {value}')
        lines += [
            "# HELP iralens_search_duration_seconds Search latency histogram.",
            "# TYPE iralens_search_duration_seconds histogram",
        ]
        for bound, value in zip(_HISTOGRAM_BUCKETS, buckets, strict=True):
            lines.append(f'iralens_search_duration_seconds_bucket{{le="{bound:g}"}} {value}')
        lines.extend([
            f'iralens_search_duration_seconds_bucket{{le="+Inf"}} {count}',
            f"iralens_search_duration_seconds_sum {total:.6f}",
            f"iralens_search_duration_seconds_count {count}",
        ])
        return "\n".join(lines) + "\n"


class APIService:
    """One shared IraLens facade plus bounded, low-cardinality metrics."""

    def __init__(self, lens: Any = None) -> None:
        if lens is None:
            from .core import IraLens
            lens = IraLens()
        self.lens = lens
        self.metrics = APIMetrics()

    def search(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any], float]:
        query = body.get("query", body.get("q"))
        if not isinstance(query, str) or not query.strip():
            raise ValueError("request requires a non-empty 'query' (or 'q') string")
        raw_options = body.get("options", {})
        if raw_options is None:
            raw_options = {}
        if not isinstance(raw_options, dict):
            raise ValueError("'options' must be a JSON object")
        options = dict(raw_options)
        unknown_options = sorted(set(options) - {"max_results", "engines", "reformulate", "cache"})
        if unknown_options:
            raise ValueError(f"unknown option key(s): {', '.join(unknown_options)}")

        raw_limit = body.get("max_results", body.get("limit", body.get(
            "count", body.get("num", options.get("max_results", 8)))))
        limit = _integer_input(raw_limit, "result limit")

        raw_filters = body.get("filters", {})
        if raw_filters is None:
            raw_filters = {}
        if not isinstance(raw_filters, dict):
            raise ValueError("'filters' must be a JSON object")
        filters = dict(raw_filters)
        filter_keys = {"date_from", "date_to", "include_domains", "exclude_domains",
                       "file_type", "language", "region"}
        for name in filter_keys:
            if name in body and name not in filters:
                filters[name] = body[name]
        unknown_filters = sorted(set(filters) - filter_keys)
        if unknown_filters:
            raise ValueError(f"unknown filter key(s): {', '.join(unknown_filters)}")
        for name in ("date_from", "date_to", "file_type", "language", "region"):
            if filters.get(name) is not None and not isinstance(filters[name], str):
                raise ValueError(f"filter '{name}' must be a string")
        for name in ("include_domains", "exclude_domains"):
            domains = filters.get(name, [])
            if domains is None:
                filters[name] = []
            elif not isinstance(domains, (list, tuple)) or any(
                    not isinstance(domain, str) for domain in domains):
                raise ValueError(f"filter '{name}' must be a list of strings")
        if "reformulate" in options and not isinstance(options["reformulate"], bool):
            raise ValueError("option 'reformulate' must be a boolean")
        engines = body.get("engines", body.get("engine", options.get("engines", ())))
        if isinstance(engines, str):
            engines = [engines]
        if not isinstance(engines, (list, tuple)):
            raise ValueError("'engines' must be a string or a list of strings")
        if any(not isinstance(engine, str) for engine in engines):
            raise ValueError("every engine name must be a string")

        call_options = dict(options)
        call_options["max_results"] = limit
        call_options["engines"] = tuple(engines)
        started = time.monotonic()
        response = self.lens.search_api(query.strip(), filters=filters, options=call_options)
        duration = time.monotonic() - started
        if response.no_results_reason == "engines_failed":
            status = 503
            outcome = "failed"
        elif response.results:
            status = 200
            outcome = "success"
        else:
            status = 200
            outcome = "empty"
        self.metrics.search(duration, outcome)
        return status, response.to_dict(), duration

    def search_compat(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Serper-like basic web response; full diagnostics stay under `iralens`."""
        raw_options = body.get("options")
        options: Dict[str, Any] = raw_options if isinstance(raw_options, dict) else {}
        requested = body.get("num", body.get("count", body.get(
            "limit", body.get("max_results", options.get("max_results", 8)))))
        count = _integer_input(requested, "result limit")
        normalized_body = dict(body)
        normalized_body["max_results"] = count
        status, response, _duration = self.search(normalized_body)
        query = response.get("query", body.get("q", body.get("query", "")))
        organic = []
        for index, result in enumerate(response.get("results", []), start=1):
            organic.append({
                "title": result.get("title", ""),
                "link": result.get("url", ""),
                "snippet": result.get("snippet", ""),
                "position": index,
                **({"date": result["published_at"]} if result.get("published_at") else {}),
            })
        return status, {
            "searchParameters": {"q": query, "num": count, "type": "search"},
            "organic": organic,
            "iralens": {
                "summary": response.get("summary", ""),
                "no_results_reason": response.get("no_results_reason", ""),
                "degraded": response.get("degraded", False),
                "outcomes": response.get("outcomes", []),
            },
        }

    def search_health(self) -> Dict[str, Any]:
        """Report durable engine circuits without issuing an upstream request."""
        try:
            engine = self.lens._search_engine()
            breaker = getattr(engine, "breaker", None)
            if breaker is None:
                return {"status": "unknown", "storage": "unknown", "circuits": {}}
            circuits = breaker.snapshot()
            storage_check = getattr(breaker, "storage_available", None)
            storage_available = storage_check() if callable(storage_check) else None
        except Exception as exc:
            return {"status": "unknown", "reason": type(exc).__name__, "circuits": {}}
        if storage_available is False:
            return {"status": "unknown", "storage": "unavailable", "circuits": circuits}
        return {
            "status": "degraded" if any(row.get("state") == "open" for row in circuits.values()) else "ok",
            "storage": "available" if storage_available else "unknown",
            "circuits": circuits,
        }

    def read(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        unknown = sorted(set(body) - {"url", "mode", "max_chars"})
        if unknown:
            raise ValueError(f"unknown read option(s): {', '.join(unknown)}")
        url = body.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("request requires a non-empty 'url' string")
        mode = body.get("mode", "auto")
        if not isinstance(mode, str) or mode not in ("auto", "static", "browser", "source"):
            raise ValueError("mode must be auto, static, browser, or source")
        max_chars = _integer_input(body.get("max_chars", 20000), "max_chars")
        if not 1 <= max_chars <= 1_000_000:
            raise ValueError("max_chars must be between 1 and 1000000")
        artifact = self.lens.open(url.strip(), mode=mode, max_chars=max_chars)
        return 200, artifact.to_dict()

    def close(self) -> None:
        try:
            self.lens.close()
        except Exception:
            pass


class IraLensHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with a hard cap on active request workers."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, address: Tuple[str, int], *, service: APIService,
                 api_key: str, max_workers: int) -> None:
        self.service = service
        self.api_key = api_key
        self._workers = threading.BoundedSemaphore(max(1, max_workers))
        super().__init__(address, IraLensRequestHandler)

    def process_request(self, request, client_address) -> None:
        if not self._workers.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._workers.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._workers.release()


class IraLensRequestHandler(BaseHTTPRequestHandler):
    server: IraLensHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "IraLens"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Never log query strings, request bodies, authorization headers, or
        # upstream exception details. The route set is fixed and low-cardinality.
        route = _metric_route(urlsplit(self.path).path)
        _LOG.info("request method=%s route=%s client=%s", self.command, route,
                  self.client_address[0] if self.client_address else "unknown")

    def _route(self) -> str:
        return urlsplit(self.path).path

    def _authorized(self) -> bool:
        expected = self.server.api_key
        if not expected:
            return True
        authorization = self.headers.get("Authorization", "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if not token:
            token = self.headers.get("X-API-Key", "")
        return bool(token) and hmac.compare_digest(token, expected)

    def _send(self, status: int, payload: Any, *, content_type: str = "application/json; charset=utf-8") -> None:
        if content_type.startswith("application/json"):
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        else:
            body = str(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == 401:
                self.send_header("WWW-Authenticate", "Bearer")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        self.server.service.metrics.request(self.command, self._route(), status)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send(status, {"error": code, "message": message})

    def _lens_error(self, exc: IraLensError) -> None:
        status = 400 if exc.error_type == "blocked_by_security_policy" else 502
        self._send(status, exc.to_dict())

    def _body(self) -> Dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise _HTTPProblem(415, "unsupported_media_type", "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError as exc:
            raise _HTTPProblem(400, "invalid_content_length", "Content-Length must be an integer") from exc
        if length < 0:
            raise _HTTPProblem(411, "length_required", "Content-Length is required")
        if length > _MAX_BODY_BYTES:
            raise _HTTPProblem(413, "request_too_large", "JSON request body exceeds 1 MiB")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise _HTTPProblem(400, "truncated_body", "request body ended before Content-Length")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _HTTPProblem(400, "invalid_json", "request body must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise _HTTPProblem(400, "invalid_json_shape", "request body must be a JSON object")
        return payload

    def _dispatch_search(self, body: Dict[str, Any], compat: bool) -> None:
        try:
            if compat:
                status, payload = self.server.service.search_compat(body)
            else:
                status, payload, _duration = self.server.service.search(body)
            self._send(status, payload)
        except ValueError as exc:
            self._error(400, "invalid_input", public_message(exc)[:300])
        except IraLensError as exc:
            self._lens_error(exc)
        except Exception as exc:
            _LOG.error("search request failed (%s)", type(exc).__name__)
            self._error(500, "internal_error", public_message(exc)[:300])

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP handler contract
        route = self._route()
        if route in ("/health", "/healthz", "/v1/health"):
            self._send(200, {"status": "ok", "service": "iralens", "version": __version__})
            return
        if not self._authorized():
            self._error(401, "unauthorized", "valid service credentials are required")
            return
        if route == "/metrics":
            self._send(200, self.server.service.metrics.render(),
                       content_type="text/plain; version=0.0.4; charset=utf-8")
            return
        if route == "/v1/health/search":
            self._send(200, self.server.service.search_health())
            return
        if route not in ("/v1/search", "/search"):
            self._error(404, "not_found", "unknown API route")
            return
        params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        query = (params.get("query") or params.get("q") or [""])[0]
        body: Dict[str, Any] = {"query": query}
        raw_limit = (params.get("count") or params.get("limit") or params.get("num") or ["8"])[0]
        body["max_results"] = raw_limit
        if "engine" in params:
            body["engines"] = params["engine"]
        filters: Dict[str, Any] = {}
        for key in ("language", "region", "file_type", "date_from", "date_to"):
            if key in params:
                filters[key] = params[key][0]
        if "include_domain" in params:
            filters["include_domains"] = params["include_domain"]
        if "exclude_domain" in params:
            filters["exclude_domains"] = params["exclude_domain"]
        if filters:
            body["filters"] = filters
        self._dispatch_search(body, compat=(route == "/search"))

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler contract
        route = self._route()
        if not self._authorized():
            self._error(401, "unauthorized", "valid service credentials are required")
            return
        try:
            body = self._body()
        except _HTTPProblem as exc:
            self._error(exc.status, exc.code, exc.message)
            return
        if route in ("/v1/search", "/search"):
            self._dispatch_search(body, compat=(route == "/search"))
            return
        if route == "/v1/read":
            try:
                status, payload = self.server.service.read(body)
                self._send(status, payload)
            except ValueError as exc:
                self._error(400, "invalid_input", public_message(exc)[:300])
            except IraLensError as exc:
                self._lens_error(exc)
            except Exception as exc:
                _LOG.error("read request failed (%s)", type(exc).__name__)
                self._error(500, "internal_error", public_message(exc)[:300])
            return
        self._error(404, "not_found", "unknown API route")


class _HTTPProblem(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_server(host: str = "127.0.0.1", port: int = 8000, *,
                  api_key: Optional[str] = None, max_workers: int = 32,
                  service: Optional[APIService] = None) -> IraLensHTTPServer:
    key = api_key if api_key is not None else os.environ.get("IRALENS_API_KEY", "")
    if not _loopback_host(host) and not key:
        raise ValueError("IRALENS_API_KEY is required when binding to a non-loopback address")
    if not _loopback_host(host) and len(key) < 32:
        raise ValueError("IRALENS_API_KEY must be at least 32 characters for a non-loopback bind")
    if not 0 <= int(port) <= 65535:
        raise ValueError("port must be between 0 and 65535")
    if not 1 <= int(max_workers) <= 256:
        raise ValueError("max_workers must be between 1 and 256")
    return IraLensHTTPServer((host, int(port)), service=service or APIService(),
                             api_key=key, max_workers=int(max_workers))


def serve(host: Optional[str] = None, port: Optional[int] = None,
          max_workers: Optional[int] = None) -> None:
    host = host or os.environ.get("IRALENS_API_HOST", "127.0.0.1")
    port = int(port if port is not None else os.environ.get("IRALENS_API_PORT", "8000"))
    workers = int(max_workers if max_workers is not None else os.environ.get("IRALENS_API_MAX_WORKERS", "32"))
    server = create_server(host, port, max_workers=workers)
    _LOG.info("IraLens API listening on %s:%s (max workers=%s)", host, server.server_address[1], workers)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        server.service.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="iralens-api", description="Run the IraLens HTTP API")
    parser.add_argument("--host", default=None, help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="port (default: 8000)")
    parser.add_argument("--max-workers", type=int, default=None, help="maximum in-flight requests")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("IRALENS_LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        serve(args.host, args.port, args.max_workers)
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
