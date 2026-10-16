# Search

How IraLens finds things on the web: engines, filters, ranking, reliability,
caching, and the source policy you should review before deployment.

## Three ways to call it

```bash
# 1. Simple: ranked artifacts
iralens search "solar cell efficiency" --limit 5

# 2. Structured: filters, engine choice, provenance
iralens search-api "solar cell efficiency" --limit 5 \
    --date-from 2024-01-01 --include-domain nrel.gov --file-type pdf \
    --engine duckduckgo --engine bing --cache use
```

```python
from iralens import IraLens
with IraLens() as lens:
    artifacts = lens.search("solar cell efficiency", limit=5)            # List[Artifact]
    resp = lens.search_api(
        "solar cell efficiency",
        filters={"date_from": "2024-01-01", "include_domains": ["nrel.gov"]},
        options={"max_results": 5, "engines": ["duckduckgo", "bing"], "cache": "use"},
    )
    for r in resp.results:
        print(r.score, r.url, r.engines, r.score_breakdown, r.security_flags)
    print(resp.outcomes, resp.fallbacks, resp.filters["report"], resp.cache)
```

MCP: the `search_api` tool takes `query`, `filters`, and `options` objects
with the same keys.

## Engines and the default chain

The default chain is `duckduckgo -> bing -> wikipedia -> semantic-search`:

- **duckduckgo** and **bing** fetch public results pages over direct HTTP
  (no key needed). Bing is experimental.
- **wikipedia** uses Wikimedia's official API with an identifying User-Agent
  and at least one second between requests. It is a useful supplemental
  index, not general web search.
- **semantic-search** is the optional `mcporter`/Exa bridge, used only when
  installed.
- A configured **private SearXNG** instance is inserted before Bing, and the
  optional **paid-backup** (Brave, Serper, or Tavily) is appended only when
  both its provider and key are configured. No third-party public SearXNG
  host is queried by default.

The reliability benchmark requires **two engines to succeed on 80%+ of
queries on their own**, so a single endpoint cannot be the whole system. A
self-hosted SearXNG removes dependence on a shared public instance but still
forwards queries to whichever upstream engines its operator enables (see the
source policy below).

```bash
export SEARXNG_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
cd deploy/searxng && docker compose up -d      # see deploy/searxng/README.md
export IRALENS_SEARXNG_INSTANCES="http://127.0.0.1:8888"
export IRALENS_ALLOWED_PRIVATE_HOSTS="127.0.0.1:8888"
```

`IRALENS_ALLOWED_PRIVATE_HOSTS` is an exact `host` / `host:port` allowlist
consulted **only by search transports**. Page reads stay strict, so a
private URL that appears in a search result is still blocked; link-local and
metadata names are refused even when allowlisted.

## Reading a `SearchResponse`

| Field | Meaning |
|---|---|
| `results[]` | Deduplicated, ranked. `score_breakdown` shows relevance, authority, freshness, agreement. |
| `outcomes[]` | One per engine attempt: `status` (results / empty / failed / skipped), `kind`, `attempts`, `filter_modes`. |
| `fallbacks[]` | Each switch between engines, with the reason. |
| `no_results_reason` | `none`, `no_results` (engines answered, nothing matched), or `engines_failed`. |
| `filters.report` | What was post-filtered and how many results were dropped or kept with unknown dates. |
| `dedup_log[]` | Why two hits were merged. |
| `cache` | `hit` / `miss` / `bypass` / `refresh`, with age and freshness. |
| `security_flags` | Prompt-injection or invisible-character flags on result text. Treat results as data. |

## Filter support by engine

| Filter | duckduckgo | bing | wikipedia | semantic-search (Exa) | paid-backup |
|---|---|---|---|---|---|
| date_from / date_to | post-filtered | post-filtered | post-filtered | post-filtered | post-filtered |
| include_domains | emulated (`site:`, first domain) + post-filtered | same | post-filtered | post-filtered | post-filtered |
| exclude_domains | post-filtered | post-filtered | unsupported | post-filtered | post-filtered |
| file_type | emulated (`filetype:`) + post-filtered | same | unsupported | post-filtered | post-filtered |
| region | native (`kl`) | unsupported | unsupported | unsupported | unsupported |
| language | unsupported | native (`setlang`) | unsupported | unsupported | unsupported |

Filters an engine cannot apply are reported `unsupported`, never faked.
Undated results are kept and counted (`date_unknown_kept`).

## Failure taxonomy and retries

Every failed engine attempt is classified (`iralens/reliability.py`):

| kind | meaning | retried? | counts toward breaker? |
|---|---|---|---|
| `captcha` | block page detected (captcha / unusual-traffic markers) | no | yes |
| `rate_limited` | 429 / rate-limit markers | no | yes |
| `layout_changed` | results markers present but no parseable hits | no | yes |
| `transient` | timeout, 5xx, connection reset | yes (backoff) | yes |
| `unavailable` | anything else (bridge missing, engine down) | no | yes |
| `empty` | engine answered, zero hits | no | no (counts as success) |
| `circuit_open` | engine skipped by the breaker | — | — |

Only transient failures are retried, with exponential backoff
(`retry_max_attempts` = 3, `retry_base_delay_seconds` = 1.0,
`retry_max_delay_seconds` = 8.0). Retrying a captcha or a rate limit makes
the block worse, so those are never retried.

## Persistent circuit state

The search path keeps circuit state in
`~/.iralens/state/search-health.sqlite3` (override root: `IRALENS_HOME`),
shared across processes and restarts:

- 3 (`breaker_failure_threshold`) consecutive non-block failures open an
  engine circuit for 300 s (`breaker_cooldown_seconds`).
- A detected CAPTCHA or 429 opens the circuit immediately for 900 s
  (`breaker_block_cooldown_seconds`); repeated failures grow the cooldown
  exponentially up to six hours.
- While open, an engine is reported `status="skipped"`. After cooldown, one
  half-open probe is permitted across local processes; success closes the
  circuit, failure reopens it.
- SearXNG endpoints have per-instance durable cooldowns, so one failed
  instance is avoided while the rest of the pool stays usable.
- The database holds engine/host identifiers, counters, and coarse latency
  estimates only — never queries, snippets, content, or API keys. If it is
  unavailable or read-only, search fails open.

## Concurrency

The global gate allows 20 concurrent searches (`search_max_concurrent`) with
an acquire timeout (`search_acquire_timeout_seconds`); a per-site gate
(`search_per_site_concurrent`, default 3) keeps simultaneous connections to
any single provider polite; a 300-second answer cache
(`cache_search_ttl_seconds`) answers repeated queries before any engine slot
is taken.

## Caching

`iralens/cache.py` stores JSON files under `~/.iralens/state/cache/`, one
per key, outside the repository:

| What | Namespace | TTL | Cached? |
|---|---|---|---|
| Search responses | `search.v1` | `cache_search_ttl_seconds` (900) | yes, only when there are results and no engine failed |
| Static page reads | `page.v1` | `cache_page_ttl_seconds` (3600) | yes, when markdown is non-empty |
| Browser page reads | — | — | **never** (the browser can hold session state) |
| Authenticated content | — | — | **never** (`cacheable=False` is required) |

Keys hash the namespace and the full request (query, filters, options,
engine chain), so different filters or engines never share an entry.
`SearchOptions.cache` takes `use` (default), `bypass`, or `refresh`
(write-only). Responses report `cache.status`, `age_seconds`, and `fresh`.
Caching never hides failures: a degraded search is not stored, so the next
call retries live. Disable everything with `IRALENS_CACHE_ENABLED=false`.

## Configuration

Every knob lives in `iralens/settings.py` and reads from `config.yaml` or
`IRALENS_<KEY>` environment variables, e.g.
`IRALENS_SEARCH_ENGINES=duckduckgo,bing,wikipedia`,
`IRALENS_SEARXNG_INSTANCES`, `IRALENS_BACKUP_SEARCH_PROVIDER=brave|serper|tavily`
with `IRALENS_BACKUP_SEARCH_API_KEY` (never called unless both are set).

## Source policy

**Review before commercial or high-volume deployment.** This inventory is
engineering documentation, not a legal opinion, and passing a technical
benchmark does not establish permission to automate queries. Check each
source's current terms for your jurisdiction, volume, and use case.

| Source | IraLens behavior | Key/host dependency | Review required |
|---|---|---|---|
| DuckDuckGo HTML/lite | Requests public results pages and parses HTML. | No provider key; DDG receives the query and caller network identity. | Verify current terms, permitted automation/rate, robots/technical restrictions, caching, and result-display rules. |
| Bing HTML | Requests public search HTML; parser is experimental. | No provider key; Microsoft receives the query and caller network identity. | Verify current Microsoft/Bing terms and any published API alternatives. |
| Wikipedia / MediaWiki API | Official `list=search` API, identifying User-Agent, ≥1 s between calls. | No API key; Wikimedia receives the query. | Verify current API etiquette, rate guidance, attribution, and reuse obligations. |
| SearXNG | Contacted only when an operator configures an instance; the bundled stack runs privately. | Self-hosted or explicitly configured endpoint; forwards to its configured upstream engines. | Review SearXNG and each enabled upstream engine's terms, rate limits, data handling, and deployment jurisdiction. Self-hosting does not grant upstream permission. |
| Exa bridge | Optional external `mcporter` tool, not installed or invoked by default. | User-configured account/provider. | Review the configured provider contract, retention, quotas, resale, and output terms. |
| Brave / Serper / Tavily backup | Optional direct provider adapter; not called unless provider and key are configured. | Paid key held in config/environment, sent over HTTPS with a same-origin redirect policy. | Review provider contract, key handling, quotas, data processing, and failover disclosure. |
| Remote reader (`r.jina.ai`) | Opt-in fallback only after local direct reading fails; off by default. | Third party receives the page URL and content request. | Review service terms, privacy/data retention, and rights to submit pages. |

The keyless adapters are not an independently crawled or licensed search
index. Self-hosting components does not change upstream terms. If a provider
blocks requests or its terms are unclear, disable that backend rather than
rotating endpoints to work around a restriction.
