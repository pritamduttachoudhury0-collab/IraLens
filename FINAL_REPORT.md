# IraLens Engineering Mission — Final Report

**Date:** 2026-11-05 (supersedes the 2026-11-04 report; section P is the follow-up)
**Branch:** `arena/ee563349-iralens` (from `main` @ `f5547d3`)
**Scope:** whole-repo audit, repair, optimization, test, documentation;
plus the PyNewsBrowser integration (section P).
**Verdict (section O):** **CONDITIONALLY READY** — see the single remaining
condition (live news validation) at the end.

> Honesty note that governs this whole report: the work environment allows
> outbound access only to package indexes and GitHub. News providers, search
> engines, and general web pages are unreachable from here. Every live result
> below is therefore reported as **blocked / not measured**, never fabricated.
> All offline work was actually run and its real output is quoted.

---

## A. Problems found

1. **Recent news scored 0/25** on the frozen benchmark (historical live run).
2. **Two baseline test-suite teardown ERRORs** under pytest 9 (901 passed but
   not a clean run).
3. **GDELT violated its provider contract**: paced at 1 s (contract is 5 s),
   and misread its HTTP-200 plain-text rate-limit notice as `invalid_json`.
4. **No publication dates in news results**: GDELT was the only news
   specialist; its `seendate` (index-seen) was correctly *not* used as a
   publication date, but nothing else supplied one, so freshness was unknown.
5. **No news-aware ranking**: a 365-day freshness half-life made a year-old
   article look as fresh as today's; no publisher-credibility signal.
6. **Browser-engine lifecycle races**: `start()` could double-spawn (orphan a
   child) under concurrent calls; `close()`/`_terminate()` left zombies
   (`kill()` without `wait()`).
7. **Documentation contradictions**: stale Mojeek "not in default chain"
   comment; ambiguous "browser engine optional" vs "bundled, zero downloads";
   news limitation text predating any fix.
8. **News attack surface untested**: feed/click-wrapper URLs are
   attacker-influenced and needed explicit SSRF/injection regressions.

## B. Root causes

- **News 0/25:** a single date-less provider (GDELT) + raw natural-language
  query + no recency window + evergreen ranking weights + no article-page date
  extraction. Freshness was structurally unknowable, so the frozen `fresh`
  predicate could never pass.
- **Teardown errors:** pytest 9 `MonkeyPatch.setattr` on an instance attribute
  that lives on the class records `NOTSET` and undoes via `delattr`; the
  autouse `pristine_sources` fixture finalized *before* monkeypatch undo and
  had already deleted the attribute → `AttributeError` inside pytest's undo.
- **GDELT:** the adapter predated enforcement of the published 5-second limit
  and treated any non-JSON body as a parse failure, including the rate-limit
  notice the API returns with HTTP 200.
- **Lifecycle races:** check-and-spawn and kill paths were not synchronized /
  did not reap the child.
- **Docs:** comments/limitations written before later changes and not
  reconciled.

## C. Files modified / added

**Modified (22 tracked):** `BENCHMARKS.md`, `CHANGELOG.md`,
`KNOWN_LIMITATIONS.md`, `README.md`, `docs/search.md`, `iralens/challenge.py`,
`iralens/engine/native.py`, `iralens/fetch.py`, `iralens/mcp_server.py`,
`iralens/search/dedup.py`, `iralens/search/engine.py`,
`iralens/search/engines/__init__.py`, `iralens/search/engines/gdelt.py`,
`iralens/search/intent.py`, `iralens/search/schema.py`, `iralens/settings.py`,
`iralens/sources/search.py`, `iralens/sources/web.py`, `tests/conftest.py`,
`tests/test_engine_lifecycle.py`, `tests/test_gdelt.py`,
`tests/test_keyless_search_backends.py`.

**Added — core (10):** `HOW_IT_WORKS.md`, `FINAL_REPORT.md`,
`benchmarks/run_recent_news.py`, `iralens/search/news.py`,
`iralens/search/engines/news_rss.py`, `tests/test_recent_news.py`,
`tests/test_news_security.py`, `tests/fixtures/news/article_jsonld.html`,
`tests/fixtures/search/bing_news_rss.xml`,
`tests/fixtures/search/google_news_rss.xml`.

**Added — PyNewsBrowser integration (9), section P:**
`iralens/search/engines/sitemaps.py`, `iralens/search/sources_pack.py`,
`iralens/search/engines/publisher_feeds.py`, `iralens/search/events.py`,
`tests/test_news_discovery_lanes.py`, `tests/test_contrib_safeurl.py`,
`contrib/pynewsbrowser/{safeurl.py,test_safeurl.py,ssrf.patch,SECURITY_PATCH.md}`.

Net tracked diff: **+1118 / −40** across 22 files, plus the new modules/tests.

## D. Changes made (what was actually implemented)

**Recent-news pipeline (Phase 1):**
- `news.py`: news-intent + explicit time-window detection; timestamp
  normalization; publisher-page date extraction (JSON-LD → article meta →
  `<time>`); bounded source-quality / primary-publisher signals; syndication
  keys. Publication / updated / index-seen / retrieval dates kept separate;
  unknown stays unknown.
- `news_rss.py`: keyless Bing News RSS + Google News RSS backends (publisher
  `pubDate` + direct URL; faithful click-wrapper unwrap; typed failures;
  refuses unverified pagination). Google's newer encrypted links are kept
  as-is, never fabricated into a publisher URL.
- Routing for news queries → `("bing-news", "google-news", "gdelt")`.
- News weight profile (freshness 0.35 on a 7-day half-life; credibility as its
  own factor) behind a **pure-relevance gate**; explicit window → real date
  filter; bounded publisher-page date/topic verification; cross-host
  syndication dedup (news-only); `SearchResponse.news` diagnostics; read-path
  `article_dates` metadata.
- GDELT: 5-second pacing; HTTP-200 rate-limit notice → `RateLimitedError`;
  gated recency `timespan` (raw-query and explicit-date contracts preserved).

**Browser engine (Phase 2):** reentrant lifecycle lock around
check-and-spawn-and-handshake; `wait()` after `kill()` in `close()` and
`_terminate()`. (The suspected "_rpc destroys concurrent responses" was
**investigated and found NOT to be a bug** — the lock already serializes the
whole exchange — so it was left unchanged.)

**Security (Phase 3):** dedicated news SSRF/injection regressions (private /
localhost / cloud-metadata `169.254.169.254` / credential / odd-scheme URLs via
feeds and click-wrappers are rejected before results or reads; injection
flagged, demoted, never executed). Existing SSRF/DNS-rebinding/redirect/
credential protections audited and confirmed intact.

**Research (Phase 3):** claim→evidence→`[S#]` citation mapping and heuristic
(not probability) confidence audited and confirmed already correct and
test-covered; no rewrite needed.

**Docs (Phase 4):** `HOW_IT_WORKS.md` (plain English); reconciled Mojeek
comment, README browser wording, news limitations, BENCHMARKS note (historical
0/25 preserved), CHANGELOG "Unreleased" entry.

**Test harness:** `conftest.py` teardown ordering fix (no assertions weakened).

## E. New dependencies

**None.** The news RSS backends use the standard library
`xml.etree.ElementTree` (chosen over `feedparser` for a small, dependency-free,
fixture-testable parser of a known, simple RSS/Atom shape; `feedparser` remains
a dependency for the existing RSS source). No paid API is required. All new
code runs on the existing dependency set.

## F. Offline results (actually run)

| Stage | Result |
|---|---|
| Baseline `pytest -q` | 901 passed, 15 skipped, **2 errors**, 58.44 s |
| After harness fix | 901 passed, 15 skipped, **0 errors**, 57.89 s |
| After Obscura DOM-date fix | 988 passed, 15 skipped, 0 errors, 71.24 s |
| **Final `pytest -q`** | **1091 passed, 15 skipped, 0 failures, 74.65 s** |
| `ruff check --select E9,F,B .` (CI rule set) | **All checks passed** |
| `mypy iralens` (CI invocation) | **Success: no issues in 98 source files** |

**+190 new tests** over the audited baseline: 62 news pipeline (incl. 9
browser/DOM date-extraction), 17 news security, 5 GDELT, 3 engine lifecycle,
**72 archive / publisher-feed / story-level lanes**, **29 contrib SSRF guard**,
**2 archive routing**. No existing assertion was weakened or deleted; the
routing tests that changed were strengthened to assert the new, larger
contract.

Separately, the patched PyNewsBrowser copy was verified in its **own**
virtualenv: **37 passed** (12 original + 25 new security tests), and the two key
tests **fail when both guard layers are removed** — so they detect the real
defect rather than passing vacuously. Its `pytest-asyncio` plugin was kept out
of IraLens's environment, so the 1091 above is measured on IraLens's own
declared dev dependencies only. Two optional extraction backends
(`trafilatura`, `readability-lxml`) could not be installed there because of a
pip resolver bug over `lxml`'s duplicated extra; no security test depends on
them. See `contrib/pynewsbrowser/SECURITY_PATCH.md`.

## G. Live results

**Not measured — blocked by environment egress.** Confirmed unreachable
(HTTP 000) from this sandbox: GDELT, Google News, Bing, DuckDuckGo, Mojeek,
Wikipedia, BBC, Reuters, example.com. The agent-side fetch tool was used only
to capture provider *shapes* into clearly-labelled offline fixtures, never
laundered into a "live" pass. `benchmarks/run_recent_news.py` was run live and
correctly reported all sampled queries **BLOCKED**, claiming **no score**.

## H. News score /25

**Not claimed.** The genuine /25 (a top-5 result that is both on-topic AND
within `recency_days`) requires live provider access, which this environment
lacks. The pipeline is implemented and offline-verified; the historical 0/25
reflects the *old* code. To measure the current score on a connected host:
`python benchmarks/run_recent_news.py`.

## I. Individual failed queries

No *measured* failures to list: no news query reached a live provider here, so
there is no honest per-query pass/fail to report. The reporter distinguishes
`blocked` (environment) from `fail` (measured, not genuine) and lists the
latter when run on a connected host. Offline, every news contract test passes.

## J. Obscura status

**Remains the primary browser engine.** Not replaced by Playwright / Selenium
/ Lightpanda; not made silently optional. The bundled in-package launcher
(vendored Obscura JS/DOM in an isolated V8 via `mini-racer`) works out of the
box with no runtime download; `install-engine` is an optional native-binary
upgrade. Lifecycle hardened (no double-spawn, no zombies) with real-subprocess
regression tests. Engine identity is still sanitized from AI-facing errors.

**Its concrete role in news:** Obscura is a *renderer*, not a discovery engine
— it cannot find the right recent articles (the feeds + GDELT + ranking do
that). It is used to *read* JS-heavy publisher pages during verification. A gap
was found and fixed here: the browser path previously converted the rendered
page to markdown and dropped the JSON-LD/meta publication dates, so a JS-heavy
article could not be date-verified. The browser read now extracts publication /
modification dates from the **rendered DOM** (bounded, read-only JS snippet)
using the same priority/normalization as the static path, and preserves them
even when the static text is kept. Dates still stay unknown when the renderer
cannot establish them. Covered by 9 new tests (fake-engine browser reads +
DOM-extraction contracts).

## K. Security audit

- **SSRF / DNS rebinding / redirects:** audited `security.py` — per-hop redirect
  re-validation, DNS pinning, mixed public/private answer rejection, HTTPS→HTTP
  downgrade rejection, redirect-chain cap, credential stripping. Confirmed
  intact and test-covered (`test_security.py`, `test_ssrf_fetch.py`).
- **News attack surface (new):** feed/click-wrapper URLs pointing at private /
  localhost / cloud-metadata / credentialed / odd-scheme targets are rejected
  before becoming results or reads (`test_news_security.py`, 17 tests).
- **Prompt injection:** web/feed content is untrusted data; injection flagged,
  demoted, never executed (`test_content_guard.py`, news regressions).
- **Credential leaks:** secrets travel in env, never argv/logs/URLs
  (`test_proxy_credential_handling.py`); API auth uses `hmac.compare_digest`,
  ≥32-char key for non-loopback. Confirmed.
- **CAPTCHA / walls:** detection-only, reported as `verification_required`,
  never bypassed. Confirmed.

## L. Performance measurements (offline, measured here)

| Metric | Value | Method |
|---|---|---|
| `import iralens` | ~181 ms median | 5 fresh interpreters (incl. spawn) |
| `extract_article_dates` | 0.097 ms/call | 2,000 calls, JSON-LD+meta+time fixture |
| `detect_news_intent` | 0.0049 ms/call | 20,000 calls |
| `normalize_timestamp` | 0.0025 ms/call | 20,000 calls |
| Offline search orchestrator | 0.13–0.14 ms median; p95 0.17–0.30 ms | 30 runs, stub backend |

The added news machinery is sub-millisecond and negligible next to network
I/O. **Live latency / memory under real provider load is not measured here**
(egress blocked); the README's prior local browser fixture numbers
(0.343 s / 136.9 MB for 10 opens) are historical and were not re-run.

## M. Remaining limitations

1. **Live news /25 not validated** (the one blocking condition). Needs a host
   with outbound access to the news providers. This now also applies to the
   three new lanes (sitemaps, publisher feeds, GDELT-backed trends): their
   parsers, bounds and failure paths are offline-verified, but no live
   end-to-end archive or regional result is claimed. See section P.
2. Google News' newer encrypted article links cannot be decoded offline; they
   are kept as readable Google redirects, not fabricated publisher URLs. Bing
   News RSS is the primary direct-URL path.
3. Bing/Google News RSS terms are personal, non-commercial feed-reader use;
   noted in docs. GDELT paced at its 5-second contract.
4. Live search quality / latency / memory and anti-bot effectiveness remain
   provider- and network-dependent and unmeasured here.
5. Windows setup script remains untested (as documented).

## N. Exact install / run commands

```bash
# Install (Linux/macOS)
git clone https://github.com/pritamduttachoudhury0-collab/IraLens.git
cd IraLens
./scripts/setup.sh            # venv + editable install + health check
iralens doctor

# Or pip
pip install "iralens @ git+https://github.com/pritamduttachoudhury0-collab/IraLens.git"

# Use
iralens search "latest news about interest rates" --limit 5 --json
iralens open "https://example.com"
iralens research "your question" --json
iralens install-engine        # optional native browser binary upgrade

# Offline tests / lint / types (no network needed)
pip install -e ".[dev]"
python -m pytest -q
ruff check --select E9,F,B .
mypy iralens

# Measure the genuine news /25 on a CONNECTED host
python benchmarks/run_recent_news.py
python benchmarks/run_recent_news.py --offline   # explicitly claims no score
```

## O. Verdict

**CONDITIONALLY READY.**

The repository is audited; the critical/high defects found (test-harness
teardown errors, GDELT provider-contract violations, browser-engine
double-spawn/zombie races) are fixed with regression tests; the recent-news
pipeline is rebuilt and offline-verified; the news attack surface is pinned;
Obscura remains primary; the full offline suite passes (1091 / 15 skipped / 0
errors) with ruff and mypy clean; docs are reconciled and `HOW_IT_WORKS.md`
exists; no new dependencies; backward compatibility preserved.

**The single remaining condition:** validate the genuine recent-news /25 (and
live search quality/latency) on a host with outbound access to the providers,
using `benchmarks/run_recent_news.py`. That step is blocked *only* by this
environment's egress allowlist, not by the code. Until it is run, no live news
score is claimed — by design. The same condition covers the archive and
publisher-feed lanes added in section P.

---

## P. PyNewsBrowser integration (follow-up)

**Request.** Embed PyNewsBrowser's capabilities into IraLens to solve the news
and discovery problems; free rein to modify or rebuild anything. The request
also asked for a CAPTCHA bypass and a "prove you are not a robot" bypass.

**The bypass was declined.** A CAPTCHA or bot check is the site owner's access
control. Defeating it is not a retrieval feature, it contradicts this
repository's documented detect-and-report contract, and it would make every
other honesty guarantee in the report meaningless. What was built instead is
better detection, typed reporting, and honest fallbacks — described below. The
rest of the request was delivered in full.

### P.1 What was ported, and how

PyNewsBrowser is MIT licensed (`Copyright (c) 2026 PyNewsBrowser
contributors`). Its ideas were ported as **native IraLens modules**, not
vendored as a dependency, so they inherit the existing SSRF guard, host pacing,
typed failures, cache and settings. Each ported module carries the attribution.
No new runtime dependency.

| New module | What it solves |
|---|---|
| `search/engines/sitemaps.py` | Archive questions. Every rolling news index is recent-only, so "in 2019" had no source at all. |
| `search/sources_pack.py` | 22 curated publisher feeds with region/topic selection. |
| `search/engines/publisher_feeds.py` | Local/regional reach — Assam and Northeast India outlets that global indexes surface late or never. |
| `search/events.py` | Story clusters, event timelines, trend signals. Ten links become "one story, eight outlets". |

Wiring: `is_archive` intent, archive routing (`sitemaps` → `publisher-feeds`),
news routing extended to four lanes, flat freshness for archive queries, four
new settings, `SearchResponse.events` / `.trends` (which flow through the API,
CLI and MCP unchanged because all three already serialize the full response).

### P.2 Defects in the ported source, fixed in the port

| Defect upstream | Fix here |
|---|---|
| `lastmod` treated as a publication date | Kept separate: `publication_date` → `published_at`, `lastmod` → `modified_at`, with `date_kind` and a revision-only flag. A page edited in 2026 is not a page published in 2026. |
| `SitemapProvider` took `[:3]` without dedup | `archive_domains` deduplicates, so the budget buys three publishers, not two. |
| No SSRF guard anywhere | IraLens's `fetch_url_text` + per-hop redirect revalidation. |
| `respect_robots=False` on several lanes | `robots.txt` read first and its declared sitemap preferred. |
| Trend score with no visible inputs | Every signal carries its counts and is labelled a heuristic, not a probability. |

### P.3 Defects found in the new code while testing it

These were caught by the new tests, not assumed:

- **The sitemap fetch budget was decorative.** It counted successes, not
  attempts, so a publisher whose sitemaps all failed could be walked one
  request at a time through an arbitrarily long index.
- **Unreadable sitemaps returned a silent empty success** — which reads as "no
  historical coverage exists". Now a typed failure, including the case where
  the index is readable but every child fails.
- **Hyphenated slugs never matched.** `term_set` keeps `climate-old-story` as
  one token, so naive URL matching silently returned nothing.
- **One story split in two** because "displace" and "displaced" are different
  tokens. Fixed with a small, symmetric inflection fold; "holds" vs "cuts"
  still separates.
- **`cluster_results(threshold=...)` was accepted and ignored**, and the
  syndication shortcut bypassed it entirely.
- **A time-dependent test.** A trend window computed from the real clock
  dropped its own fixtures when the date rolled over. Every story-level entry
  point now takes an injectable `today`, with a regression test that fails if
  the wall clock is read.
- **"history of the roman empire" was misclassified as news**, which sent an
  encyclopedia question to a rolling news index. Word-only archive intent now
  requires a reporting noun and no recency request.

### P.4 Human-verification walls: detection, not evasion

Detection was widened to the "prove you are not a robot" / "verify you are a
human" wording family, PerimeterX/HUMAN "Press & Hold", DataDome, Kasada,
Sucuri and AWS WAF, alongside the existing reCAPTCHA / hCaptcha / Turnstile /
Cloudflare / Imperva coverage. The keyless fetch path now raises a typed
`verification_required` error for an HTML wall instead of parsing the wall's
markup as content, and one publisher's wall no longer empties the lane.

Two properties are deliberately preserved and tested:

- **Only HTML is inspected**, so an RSS feed whose article title mentions a
  block page is not misread as one.
- **An article *about* bot checks is not censored.** A page quoting "prove you
  are not a robot" is not a confident detection and is still returned.

The pre-existing opt-in solver integration (operator's own account and API key,
off by default, reports what it did) was left exactly as it was. No solver,
token farm, fingerprint spoofing or challenge evasion was added.

### P.5 The upstream SSRF hole, and a fix offered back

Reviewing PyNewsBrowser found a real vulnerability: `HttpClient` used
`follow_redirects=True` and never validated the URL, while `/api/read?url=` and
`ops learn-json --url-template` both take a caller-supplied URL and the README
documents `serve --host 0.0.0.0`. That is an open fetch proxy to
`169.254.169.254` (cloud IAM credentials) and to the server's private network;
following redirects defeats a first-URL-only check.

`contrib/pynewsbrowser/` contains a self-contained, stdlib-only fix: the guard,
per-hop redirect revalidation, a patch, 25 tests and a write-up. Verified in its
own virtualenv: **37 passed** (12 original, no regressions, + 25 new). With both
guard layers removed the key tests **fail** — they detect the real defect
rather than passing vacuously. This code is not on IraLens's runtime path, but
it is security code shipped from this repository, so 29 further tests cover it
in the main suite.

### P.6 What is still not claimed

The three new lanes cannot be live-validated from this sandbox: the egress
allowlist blocks every publisher, sitemap and feed host. Their parsers, bounds,
failure paths and routing are offline-verified against fixtures. **No live
archive result, regional result or /25 score is claimed.** That is the same
single condition as section O, now covering more surface.
