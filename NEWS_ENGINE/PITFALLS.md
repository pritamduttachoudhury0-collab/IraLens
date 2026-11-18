# Pitfalls — read before changing anything

Traps that cost real debugging time on this component. Each is verified, not
speculative.

## 1. Modern Google News tokens cannot be decoded offline

`news.google.com/rss/articles/CBMi…` is **not** base64 of the target URL any
more. The token is encrypted; the only path to the publisher URL is a POST to

```
POST https://news.google.com/_/DotsSplashUi/data/batchexecute
```

with a `garturlreq` payload. Two hard constraints:

- `garturlreq` argument 1 must be **exactly 12 elements** or the endpoint
  returns an error envelope.
- The reply is `wrb.fr` with a `garturlres` field. If that field is absent or
  null, the answer is `no_target` — not a guess, not a heuristic decode.

Fixture: `tests/fixtures/gn/batchexecute.txt` is a real reply; it resolves to
`thehackernews.com/2026/11/stolen-fireworks-ai-key-led-to-cursor.html`.

## 2. The interstitial is huge and trips the read cap

`GET` on a wrapper returns a JavaScript interstitial of **601,735 bytes** — over
the 512 KB read cap, so a naive fetch fails before parsing and every resolution
looks like `fetch_failed`. The resolver requests the HTML with a budget that
accounts for this; do not lower it below ~620 KB.

Fixture: `tests/fixtures/gn/interstitial.html`.

## 3. `msn.com` has no recoverable target, ever

Measured: HTTP 200, 42,414 bytes, `<title>MSN</title>`, and **no** `canonical`,
`og:url`, `original-source` or `sourceUrl` anywhere in the body. Reading it
through IraLens gives `SUCCESS` with the 10-character body `"Title: MSN"`.

That is why portal policy defaults to `drop`: there is nothing to resolve *to*.
Do not spend effort trying to extract a publisher from msn.com.

## 4. Portal-shell detection must stay a named-host list

The first attempt was shape-based ("body under N characters and looks like a
title"). It rejected legitimate short pages — a stub article, a one-line note,
`example.com` itself. `_PORTAL_SHELL_TITLES` deliberately checks **named hosts**
and their exact title-only bodies. A short body alone is not evidence.

If you need to extend it, add a host. Do not add a shape rule.

## 5. Recency comes from the feed operator, not from ranking

Google News RSS ranks by relevance, and for a low-volume topic that surfaces
year-old articles. Measured on `earthquake today magnitude USGS`: 28 of 30 rows
were 2016–2025 and **zero** from today. With `when:1d`: 4 hits, 2 from today.

The server-side `when:` operator is the only recency control the endpoint has,
and it must be applied even for a one-day window (`days == 0`).

## 6. Over-fetch before you filter

Dropping portals shrinks the response. Without over-fetch, a feed that is half
portals hands the orchestrator five candidates of which two or three are
unreadable, and the response comes back short. `news_feed_overfetch=2.0` means
ask for 10 to show 5. It costs one slightly larger RSS body.

## 7. Do not force `+term` on news feeds

`+term` is a real Bing/Google requirement operator and it demonstrably improves
topical precision — `"why does my sourdough bread taste like alcohol"` went from
0 results to 4 on-topic bread articles.

It also **dropped the news split from 76% to 36%**, because constraining the
feed systematically loses the wire-service coverage the queries expect
(`r006`, `r012`, `r016`, `r017`, `r019`, `r020`, `r022`, `r024`, `r025` all
lost their listed publisher). Recorded as `ANCHOR-news-25.json`.

Topical relevance and coverage-shape are in tension here. Prefer coverage.

## 8. `Config.get` returns raw strings

`config.get("news_resolve_budget")` returns `"12"`, not `12`. `"0"` is truthy.
Every numeric setting in this patch parses explicitly — keep that pattern.

## 9. The news split is not a stable number

The same 25 queries measured **72%** on the delivery run and **40%** on a
clean-state re-run the same day, with no code change. Which feeds are reachable
at that hour dominates the result.

Corollary: never quote the quality number without saying when it was measured
and what the feed state was. The structural numbers — aggregator share 0.00%,
publisher share 100%, full 5-slot rate 100% — are the stable claims.

## 10. `--limit` is not a category filter

`benchmarks/run_benchmark.py --limit N` takes the first N queries overall, not N
per category. To isolate news, use `news_split_eval.py`, which filters the
`recent_news` split itself.

Related: the search cache replays by default. Pass `cache="bypass"` when you
actually want a fresh network call.

## 11. A mixed public/private DNS answer must remain fatal

`resolve_public_addresses()` treats a host resolving to both a public and a
private address as a rebinding signature and refuses to connect to any of them
— that includes `169.254.169.254`. If you touch address handling for any reason,
this check and its test must survive.
