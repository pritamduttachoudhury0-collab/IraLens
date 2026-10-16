# Search: usage guide (IraLens, Phase 1)

## Three ways to call it

```bash
# 1. Simple (unchanged): ranked artifacts
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

MCP: the `search_api` tool takes `query`, `filters`, and `options` objects with
the same keys.

## Engine redundancy and self-hosted SearXNG

The default chain is `duckduckgo -> bing -> wikipedia -> semantic-search`.
`wikipedia` uses Wikimedia's official API, identifies itself with a User-Agent,
and spaces requests by at least one second; it is a useful supplemental index,
not general web search. A configured private SearXNG instance is inserted
before Bing, and the optional `paid-backup` is appended only when both its
provider and key are configured. No third-party public SearXNG host is queried
by default.

The reliability benchmark requires **two engines to succeed on 80%+ of queries
on their own**, so a single endpoint cannot be the whole system. A self-hosted
SearXNG removes dependence on a shared public SearXNG instance, but still
forwards queries to whichever upstream engines its operator enables; see
`docs/SOURCE_POLICY.md` for the unresolved terms and legal review.

```bash
export SEARXNG_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
cd deploy/searxng && docker compose up -d      # see deploy/searxng/README.md
export IRALENS_SEARXNG_INSTANCES="http://127.0.0.1:8888"
export IRALENS_ALLOWED_PRIVATE_HOSTS="127.0.0.1:8888"
```

`IRALENS_ALLOWED_PRIVATE_HOSTS` is an exact `host` / `host:port` allowlist
consulted **only by search transports** (D-082). Page reads stay strict, so a
private URL that appears in a search result or page body is still blocked;
link-local and metadata names are refused even when allowlisted.

Engine failures are classified (`captcha`, `rate_limited`, `layout_changed`,
`transient`, `unavailable`, `empty`) and visible in `outcomes`. The SQLite
circuit breaker persists counters and cooldown deadlines across CLI/API
restarts; a captcha or 429 opens it immediately, repeated failures back off
exponentially, and only one process can claim the half-open probe. Queries and
result content are not stored in the health database. SearXNG instance
cooldowns use the same durable state.

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

## Configuration

Every knob is in `iralens/settings.py` and reads from `config.yaml` or
`IRALENS_<KEY>` environment variables, for example
`IRALENS_SEARCH_ENGINES=duckduckgo,bing,wikipedia`. To add private SearXNG use
`IRALENS_SEARXNG_INSTANCES` plus the explicit `IRALENS_ALLOWED_PRIVATE_HOSTS`
allowlist. The optional paid fallback is configured with
`IRALENS_BACKUP_SEARCH_PROVIDER=brave|serper|tavily` and
`IRALENS_BACKUP_SEARCH_API_KEY`; it is not called unless both are set.
