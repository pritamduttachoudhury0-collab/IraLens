# IraLens HTTP API

The HTTP service exposes IraLens search and page-reading to remote agents. It
uses only Python's standard-library HTTP server; the normal `iralens` package
dependencies are sufficient.

## Install and start

From a checkout:

```bash
python -m pip install .
# local-only by default; no service key required on loopback
iralens-api
# or: iralens serve
```

For a remotely reachable bind, configure a strong service token first:

```bash
export IRALENS_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
IRALENS_API_HOST=0.0.0.0 IRALENS_API_PORT=8000 iralens-api
```

A non-loopback host without `IRALENS_API_KEY`, or with a token shorter than
32 characters, is rejected at startup. Clients
may send `Authorization: Bearer <token>` or `X-API-KEY: <token>`. The health
route is intentionally unauthenticated; search, read, and metrics routes are
authenticated when a token is configured. Do not put the token in a URL.

For a bundled local deployment, from the repository root:

```bash
export IRALENS_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export SEARXNG_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
docker compose up --build -d
```

The Compose stack keeps SearXNG on the container network and publishes only
the IraLens API. `IRALENS_HOME` is a named volume, so caches and persistent
health state survive restarts. The Docker image and compose file have not been
built in this restricted workspace; treat Docker startup as unverified until
you build and smoke-test it on the target host.

## Routes

| Method / path | Auth | Purpose |
|---|---|---|
| `GET /health` (also `/healthz`, `/v1/health`) | no | Liveness only; `200` means the HTTP process responds, not that upstream search is healthy. |
| `POST /v1/search` | yes when configured | Native `SearchResponse`, including per-engine outcomes, fallback reasons, ranking, filters, cache state, and security flags. |
| `GET /v1/search?q=...&count=5` | yes when configured | Native search response; useful for simple clients. |
| `POST /search` | yes when configured | Serper-shaped basic web-search request/response for a simple base-URL swap. It returns `organic` entries plus an `iralens` diagnostics object; it is not a complete replacement for every Serper product (news, images, maps, etc.). |
| `GET /search?q=...&num=5` | yes when configured | Same compatibility shape. |
| `POST /v1/read` | yes when configured | Read a public URL locally and return an `Artifact`. SSRF protections still apply. |
| `GET /v1/health/search` | yes when configured | Snapshot of persistent engine circuits, cooldowns, and local state-store availability; it issues no upstream request. |
| `GET /metrics` | yes when configured | Process-local Prometheus counters and latency histogram. No query text is used as a metric label. |

Search accepts either `query` or `q`, plus `max_results`/`limit`/`count`/`num`,
`filters`, `options`, and optional `engines`. Limits and query validation are
identical to the Python API. A search for which every source failed returns
HTTP `503` **and** a structured body whose `no_results_reason` is
`engines_failed`; an honest zero-results answer is HTTP `200`.

Example native call:

```bash
curl -sS http://localhost:8000/v1/search \
  -H "Authorization: Bearer $IRALENS_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query":"solar cell efficiency","max_results":5,
       "options":{"cache":"bypass"},
       "filters":{"include_domains":["nrel.gov"]}}'
```

Example page read:

```bash
curl -sS http://localhost:8000/v1/read \
  -H "X-API-KEY: $IRALENS_API_KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://example.org/","mode":"auto","max_chars":12000}'
```

## Limits and operations

- Request bodies are limited to 1 MiB. Active request workers default to 8
  (`IRALENS_API_MAX_WORKERS`) and can be capped from the CLI. Search concurrency
  is separately limited by IraLens settings.
- `GET /health` is a liveness check. `/metrics` provides process-local
  Prometheus request counts and search latency; it does not prove search-source
  availability. Monitor source outcomes, the `engines_failed` rate, latency,
  and SearXNG container health separately.
- The HTTP process uses one shared IraLens facade. Browser operations are
  serialized by the engine; run a separately sized worker pool for heavier
  browser workloads.
- Logging omits query strings, request bodies, credentials, and upstream
  exception text. Reverse proxies and hosting platforms must be configured the
  same way; their default access logs may include query strings.
- The service is HTTP-only and single-node. Put it behind TLS, authentication
  controls/rate limits at the edge, and a firewall. Do not expose the SearXNG
  container port publicly. SQLite health state is for one host/local process
  group; sharing it over an arbitrary network filesystem or using several API
  replicas is not a supported deployment.

## Replacing a paid service

For a client that supports a Serper-style request/response, the base URL can be
changed from the provider's `/search` endpoint to `https://<your-host>/search`
and the client credential changed to the IraLens service token. Other clients
can call `/v1/search` with `query` and parse the normalized IraLens schema.
The protocol is not byte-for-byte compatible with Brave, Tavily, Google CSE,
or every Serper feature. Search quality, freshness, uptime, capacity, support,
terms compliance, and semantic parity are not guaranteed; run the comparison
benchmark before relying on a swap.
