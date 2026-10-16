# Reliability, caching, and resource management

## Failure taxonomy

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

`no_results_reason` tells the caller which case happened: `no_results` (every
engine answered and none had hits) versus `engines_failed`.

## Retries

Exponential backoff: `delay(n) = min(max_delay, base * 2^(n-1))`. Settings:
`retry_max_attempts` (3), `retry_base_delay_seconds` (1.0), `retry_max_delay_seconds`
(8.0). Only transient failures are retried. Retrying a captcha or a rate limit
makes the block worse, so those are not retried.

## Persistent circuit breaker and adaptive backoff

The search path uses `PersistentCircuitBreaker`, stored in
`~/.iralens/state/search-health.sqlite3` (override root: `IRALENS_HOME`). It is
shared across API threads and survives process restarts:

- `breaker_failure_threshold` (3) consecutive non-block failures open an
  engine circuit for `breaker_cooldown_seconds` (300 s).
- A detected CAPTCHA or 429 opens the circuit immediately for
  `breaker_block_cooldown_seconds` (900 s); repeated failures increase the
  cooldown exponentially up to six hours.
- While open, an engine is reported as `status="skipped"`. After cooldown,
  SQLite transactionally permits one half-open probe across processes. Success
  closes the circuit; failure reopens it.
- SearXNG endpoints have per-instance durable cooldowns, so one failed instance
  is avoided while the rest of the configured pool remains usable.
- The database holds engine/host identifiers, counters and coarse latency
  estimates only. It never persists queries, snippets, content, or API keys.
  If the health database is unavailable/read-only, search fails open and still
  tries sources rather than making persistence a hard dependency.

Use a private, durable volume for `IRALENS_HOME` in the HTTP deployment. The
circuit breaker is a backoff mechanism, not a provider SLA and not proof that
upstream automation is permitted.

## Resource management

- `ConcurrencyGate` caps concurrent searches on the shared browser engine
  (`search_max_concurrent`, default 2). Acquire times out after
  `search_acquire_timeout_seconds` with a clear `OperationTimeoutError`. The slot
  is always released, even when the guarded code raises.
- Each engine navigation has a timeout (`search_timeout_seconds`, 45).
- Static page reads are bounded three ways: a general response size cap
  (`read_max_bytes`, default 5 MB; PDF MIME/paths get a separate bounded
  `read_pdf_max_bytes`, default 20 MB), an overall read deadline
  (`read_total_timeout_seconds`, default 60) that defeats slow-drip responses,
  and the per-operation socket timeout. `max_chars` is then enforced when the
  content is served (both static and browser backends), and the artifact's
  metadata says `truncated: true` when it was cut.
- Search queries are sanitized before any engine sees them: control and
  invisible characters are stripped and queries longer than
  `search_max_query_chars` (400) are rejected with a clear message.
- Result counts are bounded: `search_max_results` (default 8, max 50) and
  `max_results * 2` raw hits per engine page. Snippets are capped at 400
  characters and titles at 200.
- Reformulated queries are capped at `reformulate_max_queries`, and research
  rounds and queries are capped by the research settings (Phase 3).

## Caching

`iralens/cache.py`. Files are JSON in `~/.iralens/state/cache/`, one file
per key. The directory is outside the repository.

| What | Namespace | TTL setting | Cached? |
|---|---|---|---|
| Search responses | `search.v1` | `cache_search_ttl_seconds` (900) | yes, only when there are results and no engine failed |
| Static page reads | `page.v1` | `cache_page_ttl_seconds` (3600) | yes, when markdown is non-empty |
| Browser page reads | — | — | **never** (the browser can hold session state) |
| Authenticated content | — | — | **never** (`cacheable=False` is required) |

Keys hash the namespace and the full request: query, filters, options, and the
engine chain. Different filters or engines never share an entry.
`SearchOptions.cache` takes `use` (default), `bypass` (neither read nor write),
or `refresh` (write only). Responses report `cache.status` (`hit` / `miss` /
`bypass` / `refresh`), `age_seconds`, and `fresh`.

Caching never hides failures: a degraded search (any engine failed or was
skipped) is not stored, so the next call retries live.

Disable everything with `IRALENS_CACHE_ENABLED=false`.
