# Known limitations — IraLens

Ordered by how much they can mislead a user. Read this before relying on
output. Everything here is stated as a product limit; dated verification
records at the end show exactly what was exercised and where.

## Live behavior

1. **Live results depend on your network and the search sources you
   configure.** The free engines fetch public results pages over HTTP; some
   networks and some engines answer such fetches with bot checks, captchas,
   or rate limits. The chain classifies these honestly (`captcha`,
   `rate_limited`) rather than inventing results, and reports
   `engines_failed` when nothing answered. Run on a normal network,
   `iralens --json doctor` and `search-api` outcomes tell you the live
   state; nothing else can.
2. **Engine markup can drift.** Parsers are fixture-tested. If an engine's
   markup changes, the outcome is `layout_changed`, not silently wrong
   results.
3. **Bing is experimental.** Its parser mirrors the `li.b_algo` structure and
   is less exercised than DuckDuckGo's. Use `--engine duckduckgo` if it
   misbehaves.
4. **The integrated browser engine ships bundled and is startup-probed.**
   Core Obscura source (`bd9529418b7fa975b50eabeac415cfaaa865a588`, base tag
   `v0.2.4`) is vendored under `iralens/_internal/browser_engine/vendor/obscura`
   and runs out of the box via `py_mini_racer`'s isolated V8 runtime without
   runtime downloads from GitHub Releases (`iralens install-engine --status`
   and `iralens doctor` report the live state). Pixel-level GPU rendering
   (`obscura-render` / `wgpu` screenshots) and Chrome DevTools Protocol server
   (`obscura-cdp`) are excluded from the default vendored runtime to keep the
   distribution lightweight (`1.2 MB` wheel). Prebuilt `mini-racer` wheels
   cover Linux (`x86_64`, `aarch64`), macOS (`x86_64`, `arm64`), and Windows
   (`AMD64`); on platforms without `mini-racer`, the runtime falls back to a
   persistent Node.js V8 VM worker if `node` is installed, or reports
   `browser_engine_unavailable`.
5. **Direct-fetch User-Agents may hit bot walls.** The free engines and the
   direct reader fetch with a browser-like UA; bot-walled sites answer with
   a challenge. The browser engine, when installed, is the rendered
   fallback.

## Research quality

6. **Claims are heuristic.** Claims are whole sentences that share a term
   with the question; there is no entailment check, so a sentence can be a
   claim without supporting the answer.
7. **Contradictions are candidates.** Numeric and negation mismatches can be
   false positives: two different measurements, or a negation in an unrelated
   clause. They are labeled `heuristic` and are never a verdict.
8. **Confidence is a formula, not a probability.** It reflects domain count
   and source score; it does not measure truth.
9. **Authority is a suffix table.** `.gov` 0.9, `.edu` 0.85, `.org` 0.6,
   default 0.5. A coarse heuristic, not a credibility assessment.
10. **Without an LLM, statements are quotations.** The deterministic
    synthesizer quotes the best-scoring claim per cluster; it does not
    paraphrase or reconcile. Research output is leads with stated limits,
    not verified answers.
11. **The contradiction path is covered by constructed-evidence tests.** Real
    contradictory corpora are rare in practice, so that path is exercised by
    design, not by a large live corpus.

## Search behavior

12. **Date and language filters are approximate.** Date filters are
    post-filtered. Undated results are kept and counted by default, so a
    recency window is not enforced against them; pass
    `exclude_unknown_dates=True` to drop them instead. A date recovered from a
    snippet is recorded with its `date_evidence`, and a *revision* timestamp
    (Wikipedia) is reported as `modified_at` with `date_kind="revision"` — it
    is never presented as a publication date. Detected scripts supply a
    best-effort language hint to Bing and select Wikipedia editions;
    Latin-script language detection remains incomplete. Other unsupported
    filter modes are reported per engine.
13. **Exa bridge filters are post-filtered.** The bridge is called with only
    the arguments known to work, so domain and date filters do not reach the
    provider.
14. **Cross-engine agreement needs two engines.** With one engine configured,
    the agreement factor is always 1.0 and tells you nothing.
15. **Ranking and intent classification remain heuristic.** Snippet coverage,
    URL specificity, intent and jurisdiction priors now demote common hub,
    dictionary, homepage and Wikipedia distractors, but they do not establish
    answerhood. The five-round benchmark found genericisation on specific
    procedures and troubleshooting; its raw result rows are not in this checkout,
    and live holdout relevance after these changes is **NOT MEASURED**. A low-ranked
    relevant page can still lose, and a plausible wrong page can still win.
16. **CJK matching uses character bigrams.** Adequate for matching; not a
    word-segmentation model, so rare-compound queries can still miss.
17. **SearXNG upstreams keep their own rules.** A configured SearXNG instance
    forwards queries to its enabled upstream engines; their terms, rate
    limits, and availability apply. Review `docs/search.md` (source policy)
    before deployment.
- **Mojeek is a tertiary default fallback, but its live behavior remains
  unverified.** The HTML parser has offline fixtures only; no claim is made
  about live markup stability, service terms, result quality, or availability.
  It follows DuckDuckGo and Bing, has an 8-second request cap, and is subject
  to the shared circuit breaker.
- **GDELT news search is newly intent-routed and not yet live-validated.** It
  uses the public DOC 2.0 API with no key, but the datacenter benchmark run at
  `240ff01` predates this adapter. GDELT's article-index timestamp is not
  treated as publication time; news freshness remains unknown unless a source
  reports a publication date.
- **Live frozen-search results remain below the requested target.** The
  2026-10-19 datacenter run on `240ff01` scored `free_replacement_rate` 0.715
  (143/200) with no paid-backup attempts. Recent-news was 0/25 and
  non-English was 12/25; DuckDuckGo was fully circuit-open and Bing was
  intermittently circuit-open, including on the five unavailable queries.
  This is one provider/network-dependent run, not a universal
  search-quality estimate, and 90% has not been achieved.
- **A non-empty result set is not an answer.** Every search carries a
  `coverage` verdict: how much of the question the results actually address.
  When the results are generic filler, `no_results_reason` is
  `low_coverage`, not `none`, and the summary says so. The verdict is a
  term-overlap heuristic, not a judgement of truth.
- **The relevance gap itself is not closed, and the audit could not close
  it either.** The independent audit's S-RL entry carries
  `root_status: "Unknown"`: it confirmed that IraLens presented unrelated
  filler as usable evidence (fixed — the response now reports
  `low_coverage` and the research layer will not call such a search a
  success), but it could not establish *why* the providers returned that
  filler. It manually confirmed a relevance defect on **45 of 3,000**
  queries and left the other **2,955** as `unresolved`. A lexical
  term-overlap proxy against those 45 has precision **0.016** and recall
  **0.756**, so the true count of weak-result queries is a range, not a
  number, and is reported as one. Fixing the upstream cause means fixing
  Bing and DuckDuckGo, not this repository — and the audit explicitly
  forbade the shortcut: "keep the full query; do not silently simplify it."
- **A `site:` scope is enforced on the results.** The operator survives
  tokenizing and reformulation, and out-of-scope hits are dropped and
  counted. A scoped query that finds nothing in scope returns nothing; it
  does not fall back to out-of-scope results.
- **Pagination is real but not universal.** `SearchOptions.offset` skips
  results; Bing (`first`), DuckDuckGo and Mojeek (`s`), SearXNG (`pageno`)
  and Wikipedia (`sroffset`) implement it. An engine that cannot paginate
  says so rather than returning the same page again.
- **Short result lists are not padded.** `shortfall_reason` reports whether
  a list was capped for quality, deduplicated, filtered, or exhausted. The
  Wikipedia cap for non-definitional intents can intentionally result in fewer
  than the requested count if no other sources supplied candidates.
- **Date extraction is a heuristic and not yet coverage-benchmarked.** It
  handles unambiguous snippet dates and URL date paths without fetching pages,
  and records `date_evidence`. Ambiguous dates and locale-specific formats are
  ignored; overall coverage and false-date rate are **NOT MEASURED**.

## Reading

18. **Local extraction is lossy for complex pages.** The stdlib extractor has
    no DOM, CSS layout, or JavaScript. Tables become pipe rows; widgets
    disappear. Use `mode=browser` when fidelity matters. The extractor now
    selects the page's main-content region when the page marks one
    (`<main>`, `<article>`, `.mw-parser-output`, `.post-content`, …), so a
    navigation-heavy page no longer spends the whole character budget on its
    menu; a page with no recognisable content region is returned whole.
    Script and style source never appears in the extracted text.
19. **Soft-404 detection is heuristic.** Short bodies with not-found markers
    are classified `soft_404`; novel 404 page designs can slip through, and
    short real pages mentioning "404" are protected only by a length bound.
20. **The JS-shell heuristic can over-trigger.** A static page is re-read
    through the browser when it has an "enable JavaScript" notice, under ~150
    visible characters, is mostly link labels, has almost no sentence
    structure, or renders a block of link-only lines (a menu with no content
    behind it). The cost is one extra render; the static text is kept if
    rendering is not better, and a renderer that is known to be dead for a
    host is not tried again — the read says so instead.
21. **OCR needs host tools and is bounded.** Scanned-page OCR runs only when
    `pdftoppm` and `tesseract` are installed, defaults to five pages in
    English, and never uploads the document (`IRALENS_PDF_OCR_ENABLED=false`
    opts out). Without the tools, scans fail with typed
    `pdf_ocr_support_missing` and `doctor` says what is missing. OCR quality
    on a broad scan corpus is not yet verified.
22. **The remote reader is opt-in and third-party.** With
    `IRALENS_READ_REMOTE_READER_ENABLED=1`, URLs that fail the direct local
    read are sent to `r.jina.ai`. It is off by default.
23. **A query that names a page is read, and a read can fail.** A query
    whose head is a URL ("https://react.dev/learn", optionally followed by
    what the caller wants from that page) is now a *read* request: the page
    is fetched and returned with its own text, because searching for the
    text of a URL returns whatever happens to rank highest for it. A URL
    followed by a *question* and a query naming two pages stay searches. If
    the page cannot be read, the search still runs and the response says
    what happened. In the verification sandbox only 9 of the audit corpus's
    130 URL queries were reachable at all, so the fix is measured on those
    9 (coverage `low` → `good` in 9 of 9); the other 121 need egress this
    environment does not have.

## Reliability and security

23. **Circuit state is local, not distributed.** Engine circuits and
    per-instance cooldowns persist in a private SQLite file under
    `IRALENS_HOME/state`; they survive restarts and coordinate local
    processes. Multi-host locking, external health storage, and long
    operational history are not implemented. A read-only state directory
    fails open. Search responses now expose skipped/failed engine details,
    but the live benchmark's DuckDuckGo failure was not re-run after this change.
24. **Injection flags are heuristic.** They cover common English phrasings,
    miss paraphrases and other languages, and are advisory — not a boundary.
25. **The private-address check is literal plus fetch-time.** The literal URL
    check covers everything; DNS resolution, answer validation, and redirect
    re-validation cover the fetch path (`security.safe_urlopen`). Page reads
    **and RSS/Atom feeds** use that transport, so both are DNS-pinned across
    redirects. One source still uses its own HTTP client — `transcribe`, which
    downloads audio and posts it to a Whisper endpoint with `requests` — and
    validates only the literal URL; the GitHub API targets are constants.
26. **The private-host allowlist applies to search transports only.** Page
    reads keep the strict public-URL policy, so a self-hosted SearXNG cannot
    be used as a general reader, and a *search* response linking to private
    hosts still cannot be fetched. Link-local and metadata names are refused
    even when allowlisted.

## Browser resilience, verification, and optional features

28. **Verification pages are reported; only an operator-configured solving
    service is used to answer them.** A CAPTCHA widget, a bot-check
    interstitial, an authentication wall, or an access-denied page is
    detected on both the raw response body *and* the extracted text, and
    raises a typed `verification_required` error. IraLens does not forge a
    token, break a widget, or interfere with any verification control, and
    does not retry a challenge through a different transport — a second
    render cannot clear an interactive check, so the read stops instead. Two
    things are done with a challenge, both opt-in and both reported: a
    JavaScript interstitial is *waited out* (the page's own script computes
    a clearance cookie; the engine waits for it and refetches), and an
    interactive widget is handed to a solving service the operator
    configured with their own account and API key. Without such a service a
    challenge is reported, never bypassed.
29. **Challenge detection is heuristic and deliberately conservative.**
    Widget identifiers (`cdn-cgi/challenge-platform`, `g-recaptcha`,
    `cf-turnstile`, …) are treated as decisive because they do not occur in
    ordinary prose. Marketing or editorial copy containing the word
    "captcha" or the name "Cloudflare" must not be misread, so interstitial
    *phrasing* alone is not enough: it requires corroboration by a provider
    fingerprint, a reference ID, or a refusing status code, and is otherwise
    reported as detected-but-unconfirmed. Novel challenge designs, and
    challenges served in languages other than English, will be missed. A
    page that merely *contains* a login widget is also reported as a
    challenge, even when its article text is readable.
30. **Stealth is a browser-identity consistency measure, not a
    guarantee.** The Python/native-V8 tier implements stealth: one stable,
    internally coherent identity (Chrome-shaped request headers including
    client hints, a user agent the page agrees with, a screen/GPU/canvas
    fingerprint that does not change between pages) plus challenge
    detection and interstitial waiting. It is *not* a promise of success:
    it does not defeat an interactive challenge on its own, and it does not
    make the client indistinguishable from a real browser to a commercial
    bot-detection service. No live test against any bot-detection service
    was run for this work; the properties asserted are internal consistency
    (the headers, the client hints and the page agree) and stability (the
    identity does not change between requests).
31. **TLS impersonation is an optional extra.** The standard library
    presents the local OpenSSL ClientHello, which is not a Chrome
    ClientHello, and a determined detector can tell. `pip install
    "iralens[stealth]"` adds `curl_cffi`, which reproduces Chrome's TLS and
    HTTP/2 fingerprints for `https://` requests. Without it the capability
    is reported as unavailable rather than assumed. Cleartext `http://`
    requests always use the standard library, because the impersonation
    backend would attempt an HTTP/2 upgrade (`Upgrade: h2c`) that Chrome
    never sends.
32. **The stealth canvas fallback does not render glyphs.** The engine's
    2D canvas is backed by a host operation that only exists in the Rust
    tier, so `getContext("2d")` used to return `null` — which tells a
    fingerprinting script that it is not talking to a browser. The stealth
    layer supplies a real pixel buffer and a PNG encoder, so the canvas is
    present, stable and unique per profile; but `fillText` draws nothing, so
    a page that renders text into a canvas gets an empty area. This is a
    deliberate, documented limitation: a wrong-but-present canvas beats a
    missing one for detection, and faking glyphs would be a larger lie.
33. **Optional features now advertise what they actually do.** The engine
    reports its capabilities in the MCP `initialize` response, including
    whether a feature was *requested*, whether it is *supported*, and
    whether it was *applied*. A requested-but-unavailable feature produces a
    warning on stderr and in `doctor` rather than being silently dropped, or
    refusing to start — taking the browser tier down over a cosmetic option
    would be the worse failure.
34. **Standing claim: no anti-detection effectiveness is asserted.** No
    statement is made about the real-world anti-bot effectiveness of the
    stealth tier, of Obscura, or of anything else here. It was evaluated
    from the source in this repository; the upstream repository was not
    fetched, and no live test against any bot-detection service was run.

## Retrieval trust (what the results tell you)

35. **A degraded result set says so.** When an engine is circuit-open or
    failed, another engine fills the slots, so the result count stays the
    same while the coverage silently shrinks. Every search result now
    carries a `degradation` block (`degraded`, `engines_served`,
    `engines_impaired`, `reasons`, `filler_risk`) derived from engine
    outcomes. An engine that answered with nothing is *not* counted as
    impaired. `doctor` reports `warn` while any engine is tripped.
36. **Recency cannot rescue an irrelevant result.** Freshness is held at
    neutral when relevance falls below the gate, so a page published today
    no longer outranks an undated page that actually answers the question.
    The cap only removes upside; it never inflates a score. Gated results
    report `freshness_gated: true`.
37. **Snippets are chosen for relevance, not length.** Candidate snippets
    are scored on query-term coverage and density, penalised for
    marketing/navigation copy, and credited for carrying a figure when the
    question asks for one. When no candidate matches any query term the
    longest is used, so an uninformative choice is at least a deterministic
    one. This is a heuristic: it improves snippet selection, it does not
    guarantee the snippet answers the question.
38. **Disagreements between sources are surfaced, never adjudicated.**
    Where two different domains state materially different figures sharing a
    unit (more than 5% apart), the response carries a conflict record with
    both sides, their sources and their dates. Detection is **lexical and
    heuristic** — it compares numbers, it does not understand claims, and it
    never decides which source is correct. Agreement is not reported as
    conflict, and one domain restating itself is not two sources. Every
    result also carries a `sufficiency` label: `insufficient` (fewer than two
    independent domains), `undated`, or `sourced`.

## Still open — not fixed

39. **Retrieval quality on generic, multi-concept queries is still unproven
    (the principal remaining gap).** A prior 10-query product audit found
    answer-bearing evidence in the top 5 for only 2 of 10 queries, with
    failures on single-fact lookups, market data, benchmarks, and advisory
    queries. Release 0.3.5 adds token-boundary-aware coverage,
    answerability-gated query expansion, and intent-routed official APIs for
    scholarly, technical, and GitHub-specific searches. These are real
    engineering changes, but they do not improve the general indexes' recall
    and have not been measured against a fresh live gold set. **The earlier
    2/10 result is not replaced by a newer percentage; 90% is not claimed.**
40. **The 12,000-query paired benchmark has still not been run.** Zero
    queries have been submitted; the ≥98% acceptance bar is untested and is
    not claimed. This is the same unmet bar recorded in the earlier audits.
41. **The paired-query audit was performed against `main` at `af0af08`,
    before the 0.3.1 remediation.** Several of its findings (extraction
    flattening, the injection false positive on benign documentation, cache
    TTL and RSS documentation mismatches) are fixed in the 0.3.1 line but
    were published as open because the audited revision predates them. The
    audit should be re-run against current `main` before its severity
    ratings are relied upon.
40. **The new Crossref, arXiv, Stack Exchange, and GitHub search adapters are
    specialist sources, not substitutes for a general web index.** Their
    parsers have deterministic offline fixtures. One read-only GitHub releases
    smoke (`site:github.com/scipy/scipy latest release`) returned six public
    API hits, but that is not a quality rate; Crossref/arXiv/Stack Exchange
    could not be reached here. Live reachability, API quotas, current terms,
    and top-5 contribution must be verified on an operator-approved network.
    Provider refusals are surfaced, not routed around.
41. **The 90% paid-key replacement target is not measured.** `run_benchmark.py`
    now reports `free_replacement_rate`: a query counts only when its frozen
    top-5 gold predicate is met without attempting `paid-backup`. This is a
    quality-based rate, not the fraction of calls that happen not to use a key.
    It needs a live run on the frozen query set; no replacement percentage is
    claimed from offline fixtures, a missing key, or an empty provider response.

## Platform and packaging

27. **Not published to PyPI.** Install from a checkout, or straight from
    GitHub: `pip install "iralens @ git+https://github.com/pritamduttachoudhury0-collab/IraLens.git"`
    gives the latest developed version (README). The release artifacts are
    built and attached to every GitHub release and the publish workflow is
    in the repository, but the upload itself needs the project's PyPI
    account — see `docs/releasing.md` for the one step that is not
    automatable from here.
28. **The HTTP API is a single node.** No hosted URL, SLA, TLS termination,
    multi-node health store, or managed uptime. Put a TLS reverse proxy and
    resource limits in front of it for anything beyond local use.
29. **Docker files are provided, not yet verified on a live host.** Build
    the image and start the Compose stack on the target host before relying
    on them. The API itself runs fine without Docker (`iralens serve`).
30. **Windows setup is untested.** `scripts/setup.ps1` is provided but has
    not run on a Windows machine; `scripts/setup.sh` is the tested path
    (Linux/macOS).
31. **CI verifies Python 3.10 and 3.12** (offline gates on every push and
    pull request). The primary development interpreter is 3.11.
32. **CI runs offline gates only.** The CI workflow runs pytest, ruff, and
    mypy. Live search, browser startup, Docker builds, and multi-day
    monitoring are not exercised in CI.
33. **`xiaoyuzhou` (podcast) source is not implemented.** No module ships for
    it; the architecture may grow one later.
34. **Paid-provider parity is implemented but unmeasured.** The optional
    benchmark reference phase reports labeled top-5 gold hit rates,
    availability, p50/p95, and domain overlap for both IraLens and a
    configured paid provider — but no provider key has been supplied for a
    run, so no parity claim exists. The Serper-compatible `/search` endpoint
    covers general web search only; proprietary Google features (knowledge
    graph, verticals) are intentionally not synthesized.

## The audit-driven verification suite

The repository carries the harnesses that measure the behaviour above. They
run offline and print a score:

| command | what it measures | result |
|---|---|---|
| `python benchmarks/acceptance_17.py` | the 17 offline acceptance checks from the independent audit, inverted so that each one asserts the defect is **gone** | **17/17** |
| `python benchmarks/run_scraping.py` | the read/extract probes: character budget, main-content selection, JS-shell detection, script leakage, typed 429, block classification, cache provenance, concurrent session saves, typed refusal | **12/12** |
| `python benchmarks/run_audit3000.py --offline-pipeline` | the frozen 3,000-query corpus through the real engine with a local backend: scoping, date labelling, snippet handling, engine readiness, coverage verdict | **14,262/14,262 clean** |
| `python benchmarks/run_audit3000.py --replay FILE` | a captured run (for example the audit's own 3,000-row baseline) scored with the same predicates, so before/after are comparable | see `BENCHMARKS.md` |
| `python benchmarks/project_score.py` | the projected per-query score, each number traced to the register entry that produces it | **3,000/3,000** with no code-caused failure; see `BENCHMARKS.md` |
| `python benchmarks/run_audit3000.py --live` | the frozen 3,000-query corpus against the live engines | needs provider egress |

The corpus is `benchmarks/audit3000/queries.jsonl` with its sha256 in
`benchmarks/audit3000/dataset.sha256`; it is never edited to move a number.
A family that could not be run is reported as `not_run` with its reason and
is never counted as a pass.

## The 0.3.1 remediation

The eight findings of the 2026-10-18 feasibility-gated partial audit
(`F-01`…`F-08`) are fixed and regression-tested offline. What follows is what
is *still* not established.

- **The 12,000-query paired benchmark was NOT run.** No query manifest was
  produced, no per-query criteria were registered, and no corpus was
  submitted to either system. The repository's offline suites and its
  3,000-query replay do not substitute for it, and the 98% acceptance target
  remains unmeasured.
- **F-08's suspect date is unresolved.** One result carried
  `published_at: 2015-03-13` on a Python 3.13 page. The raw engine response
  was never captured, so no cause is assigned: a provider field, a snippet or
  URL-path extraction, and a merged duplicate all remain possible. The date
  is now *inspectable* (`date_evidence`, `date_kind`) and typed `unknown`
  where it cannot be attributed, rather than asserted as a publication date.
- **Main-content selection is heuristic.** It isolates a region when one
  container holds at least half the page's prose and at least 200 visible
  characters. Pages that split their article across several equal containers,
  or that mark up content in an unrecognised way, are read whole — which is
  correct but can include navigation. Fixtures cover the threshold; arbitrary
  real-world markup does not.
- **Injection flags are still a heuristic.** F-07 removed one false positive
  by requiring syntactic evidence of an invocation. Paraphrases, other
  languages, and genuinely novel phrasings are still missed. A page that is
  *not* flagged is not thereby trusted.
- **Live provider reachability is not probed by `doctor`.** It reports
  `provider_probe: not_run` by design: the health check makes no unsolicited
  upstream request. Verify live state with an actual search.
- **Browser-engine integrity is unchanged.** The bundled engine keeps its own
  version (`0.2.4`); release artifacts still have no published checksum
  unless the operator supplies one.

## Verification records

Each record states the date, the environment class, and the exact claim.
"Restricted egress" means the verification environment could reach only
package indexes and GitHub — live search engines and arbitrary web pages
were not reachable there, so anything requiring them is listed as not yet
verified.

**2026-10-19, restricted egress (keyless retrieval iteration).**

- `.venv/bin/python -m pytest -q` — **843 passed, 14 skipped, 0 failed**.
  The skips require live-network access.
- `.venv/bin/ruff check --select E9,F,B .` — clean. `.venv/bin/mypy iralens`
  — clean (88 source files).
- `python benchmarks/acceptance_17.py` — **17/17**; `python
  benchmarks/run_scraping.py` — **12/12**.
- `python benchmarks/run_audit3000.py --offline-pipeline` — **14,262/14,262
  clean**. This validates the pipeline only, not provider retrieval.
- `python benchmarks/run_benchmark.py --self-test` — passed; this validates
  the harness, not search quality.
- `python benchmarks/ab_query_forms.py` — coverage agrees with the existing
  ten hand-labelled provider captures in **10/10** cases. It remains a small
  demonstration, not a replacement-rate estimate.
- **Not measured:** live quality of the new specialist APIs; the frozen
  200-query `free_replacement_rate`; paid-provider parity; and the 90% goal.
  A single read-only GitHub releases request returned six public results, but
  that is a smoke test, not a relevance benchmark. Crossref, arXiv, Stack
  Exchange, and general search-provider egress were not available here. A run
  on an open, approved network is required.

**2026-10-18, restricted egress (post-audit remediation).**

- `.venv/bin/python -m pytest -q` — **636 passed, 14 skipped, 0 failed** after
  the three-phase remediation of the independent audit (the 14 skips are
  live-network tests).
- `ruff check --select E9,F,B .` — clean. `mypy iralens` — clean (81 source
  files).
- `python benchmarks/acceptance_17.py` — **17/17**.
- `python benchmarks/run_scraping.py` — **12/12**.
- `python benchmarks/run_audit3000.py --offline-pipeline` — **14,262/14,262
  clean** over the frozen 3,000-query corpus.
- `python benchmarks/run_audit3000.py --replay <audit baseline>` — the audit's
  own captured 3,000-row run, re-scored with the same predicates:
  **8,435 code-caused failures and 5,223 external failures** out of 31,264
  checks. Every code-caused family it names is fixed in the code above; the
  external ones (provider captchas and throttles) are outside this project's
  control and are reported as such.
- `python benchmarks/project_score.py` — the projected per-query score:
  **3,000/3,000** with no code-caused failure, **2,955/3,000** with no
  defect at all, **1,637/3,000** under the strictest reading. A projection, not
  a measurement; the live run is the only measurement.
- `python benchmarks/measure_focus_split.py` — the subject/request split
  recovers **2,622 / 2,950 = 88.88%** of the corpus topics exactly; the
  request lexicon learned on a training half scores **89.15%** on the
  held-out half. The remaining ~11% are questions whose request shape is not
  separable by the lexicon; they are left unsplit, which is the safe
  direction (the full question is still sent).
- `python benchmarks/ab_query_forms.py` — over ten hand-labelled real
  provider captures: the `focus` form is **7/7** on-target for the nginx
  question where the loose engine returns **0/10** for the full question,
  and the coverage metric agrees with the human labels in **10/10** captures
  (6/10 before E-37). This is a **demonstration over ten captures, not a
  rate**: four questions, one engine each. It shows the mechanism works; it
  does not establish what fraction of the 3,000 queries it would rescue.
- Not verified there: live DuckDuckGo/Bing/Wikipedia runs *from the sandbox*.
  The environment allows outbound access only to package indexes and GitHub,
  so the `--live` 3,000-query run could not be executed; the runner is in
  place and reports `not_run` with the reason when it cannot reach a
  provider. The provider captures used above were fetched through the
  review tool's own proxy, not from the sandbox.

**2026-10-17, restricted egress (v0.2.1 + integrated browser engine).**

- `.venv/bin/python -m pytest -q` — **463 passed, 14 skipped, 0 failed** after
  evidence-driven search changes (including 13 end-to-end real-browser
  integration tests in `tests/test_browser_engine_integration.py` against a
  local fixture server). The 14 skips are live-network tests without general
  Internet egress.
- `ruff check --select E9,F,B .` — clean. `mypy iralens` — clean (74 source
  files).
- Package distribution sizes: wheel `1.2 MB` (`1,239,833` bytes), sdist
  `1.3 MB` (`1,341 KB`), vendored Obscura source tree `4.1 MB` uncompressed.

**2026-10-16, restricted egress (v0.2.1).**

- `.venv/bin/python -m pytest -q` — **428 passed, 14 skipped, 0 failed**. The
  14 skips are live-network and browser-engine tests that skip cleanly
  without general Internet egress.
- `ruff check --select E9,F,B .` — clean. `mypy iralens` — clean (67 source
  files).
- `benchmarks/run_benchmark.py --self-test` — passed (harness math validated
  against a synthetic system; not a measurement).
- `scripts/daily_watch.py` — 2/2 live public GitHub page reads passed.
- Full feature walk and clean-install check: `docs/audits/final-verification.md`.
- Not verified there: live DuckDuckGo/Bing/SearXNG runs, the remote reader,
  the engine binary download, Docker builds, Windows setup.

**2026-10-16, open network (paid-search replacement audit).**

An independent audit on an unrestricted network exercised the live paths the
restricted environment could not; the full report is
`docs/audits/paid-search-replacement-audit-2026-10-14.md`. Headlines: clean
install with the live test suite **416 passed, 0 failed**; DuckDuckGo and
Bing **15/15** each; the frozen 100-query quality slice passed (exact-site
top 3 **24/25**, facts top 10 **25/25**); research answered **12/12** with no
invented citations; page reading **33/60 (55%)**, which is why the reading
repairs above exist; 20-way burst latency exceeded its target at audit time
(p95 18.8 s → sub-second offline after the concurrency rework). Those
numbers are point-in-time measurements, re-collected per the benchmark
schedule in `BENCHMARKS.md`.

**Earlier restricted-egress records (2026-10-09 → 2026-10-13).**

The acceptance audit at `docs/audits/production-acceptance-audit-2026-10-09.md`
records the offline hardening verification in full (adversarial SSRF matrix,
ranking delta, clean-environment install). Interim suite runs grew from
328 → 378 → 402 passing tests as coverage expanded; all are superseded by the
2026-10-16 records above.

## Deliberate non-goals

- Independently crawled search index or vector database.
- Managed endpoint, uptime promise, or multi-node health store.
- LLM-by-default research (the deterministic core stays the baseline).
- Public SearXNG instances bundled as defaults.
- Auto-login to platforms; authenticated sources use credentials or browser
  sessions you provide.
