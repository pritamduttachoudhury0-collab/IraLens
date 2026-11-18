# News Engine — how the pieces fit

## Data path

```
query
  └─ detect_news_intent(question)              iralens/search/news.py
       └─ explicit window ("today", "last 24h") → SearchFilters.date_from
          └─ Google News appends `when:Nd`      engines/news_rss.py

feed fetch  (google-news, bing-news, publisher-feeds)
  └─ over-fetch ×2.0                          settings.news_feed_overfetch
       └─ parse_news_rss(...)                 engines/news_rss.py
            ├─ unwrap wrapper URL             engines/url_resolve.py
            │    ├─ cache hit → free
            │    ├─ POST batchexecute → garturlres → publisher URL
            │    └─ no target → status="no_target"   (never guessed)
            └─ classify portal host           engines/aggregators.py

orchestration                                 iralens/search/engine.py
  ├─ _post_filter(...)                        date, domain, public-only
  ├─ _public_only(...)                        SSRF + privacy
  └─ _rank(..., filter_report=...)             engine.py
       ├─ relevance blend                     search/ranking.py
       ├─ news weight profile                 settings.py
       └─ portal policy  drop|demote|off      engine.py

read                                          iralens/sources/web.py
  └─ _empty_body_reason(markdown, url)
       └─ portal shell → typed empty_body     (not a silent success)
```

## The two new modules

### `engines/url_resolve.py`

Given a wrapper URL, produce either a publisher URL or a **typed reason** it
could not. Statuses include `resolved`, `no_target`, `fetch_failed`, and
`budget_exhausted`. HTTP denials and CAPTCHA walls stop the resolver.

Design points:

- **Never guesses.** There is no fallback that constructs a URL from the token.
  A `no_target` hit is always dropped and counted, even in ablation mode.
- **Budgeted.** `news_resolve_budget` (default 12) network resolutions per
  search; cache hits are free. Spent budget is reported, not silently exceeded.
- **Cacheable.** A resolved wrapper is cached by token, so repeated queries pay
  nothing.
- Timeout per wrapper: `news_resolve_timeout_seconds` (default 8.0).

### `engines/aggregators.py` (114 lines)

A **named-host** mapping from syndication portal to severity:

```python
Aggregator hosts: msn.com, yahoo.com, apple.news, flipboard.com, …
severity: 0.0–1.0, default _DEFAULT_SEVERITY = 0.7
```

A host is a portal if it re-publishes other outlets' articles. It is *not* a
quality judgement — Reuters and BBC are publishers; msn.com is a portal even
when it carries Reuters copy.

## Portal policy

Set by `news_aggregator_policy` for news queries:

| value | behaviour | when to use |
|---|---|---|
| `drop` (default) | remove the slot, count it in `filter_report["aggregator_dropped"]` | a portal is unreadable here (measured: msn 10-char success, yahoo typed error) |
| `demote` | keep it, rank below every publisher, label it | you want the link and can accept a middleman |
| `off` | leave ranking untouched | reproducing pre-patch numbers |

Rationale for `drop` as default: a slot that cannot be read is not a source, and
citing a middleman is strictly worse than citing the article it copies. The
policy is a setting precisely because "unreadable" is an observation about *this*
reader on *this* date, not a law of nature.

## Diagnostics

`filter_report` travels out of `_rank`, so callers see what happened rather than
inferring it from a short list:

```json
{
  "applied_post": [],
  "date_unknown_kept": 0,
  "dropped": 0,
  "non_public_dropped": 0,
  "aggregator_dropped": {"msn.com": 1},
  "aggregator_dropped_total": 1
}
```

`news_rss.py` likewise stamps `last_resolution_log`: how many feed links were
wrappers, how many resolved, how many are still opaque.

## Why over-fetch is load-bearing

The four mechanisms interact. Dropping portals shortens the list; dropping
unresolved wrappers shortens it again. Without over-fetch the two together
starve the response:

```
ask for 5  →  5 returned  →  2 portals dropped, 1 wrapper unresolved  →  2 slots
ask for 10 → 10 returned  →  4 portals dropped, 2 wrappers unresolved →  4+ slots
```

The frozen split targets zero aggregator slots and at least 95% full five-slot
responses. A run that cannot reach the news feeds does not verify either claim.
