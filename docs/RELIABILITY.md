# Reliability, caching, and resource management

## Failure taxonomy

Every failed engine attempt is classified (`halfiralens/reliability.py`):

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

## Circuit breaker

Per engine, per `SearchEngine` instance:

- Opens after `breaker_failure_threshold` (3) consecutive failures.
- While open, the engine is skipped and recorded as `status="skipped"`. It is
  never called.
- After `breaker_cooldown_seconds` (300), one probe call is allowed (half-open).
  Success closes the breaker. Failure reopens it.
- State is in memory. It resets when the process restarts. This is deliberate:
  a persisted breaker could lock out an engine for a long time after a transient
  outage.

## Resource management

- `ConcurrencyGate` caps concurrent searches on the shared browser engine
  (`search_max_concurrent`, default 2). Acquire times out after
  `search_acquire_timeout_seconds` with a clear `OperationTimeoutError`. The slot
  is always released, even when the guarded code raises.
- Each engine navigation has a timeout (`search_timeout_seconds`, 45).
- Result counts are bounded: `search_max_results` (default 8, max 50) and
  `max_results * 2` raw hits per engine page. Snippets are capped at 400
  characters and titles at 200.
- Reformulated queries are capped at `reformulate_max_queries`, and research
  rounds and queries are capped by the research settings (Phase 3).

## Caching

`halfiralens/cache.py`. Files are JSON in `~/.half-iralens/state/cache/`, one file
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

Disable everything with `HIL_CACHE_ENABLED=false`.
