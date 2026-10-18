# IraLens

**One honest Internet-access layer for AI agents.**

[![CI](https://github.com/pritamduttachoudhury0-collab/IraLens/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/pritamduttachoudhury0-collab/IraLens/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.2.1-informational)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-blue)](pyproject.toml)

IraLens lets an AI agent search the web, read pages and documents, drive a
real browser, and reach specialized platforms — through one CLI, one Python
API, and one MCP server, with one data model, one session state store behind
it all, one error taxonomy, and no inventiveness: when something cannot be
done or has not been verified, IraLens says so instead of papering over it.
A deterministic research layer on top plans queries, reads sources, and
produces cited reports with no model required.

```
AI agent
   ↓
IRALENS            ← CLI · Python API · MCP server · HTTP service
   ↓
The Internet       ← search engines · pages & PDFs · browser · platforms
```

## Why IraLens

- **Free to run.** Web search works out of the box with no paid API key —
  public search endpoints, Wikimedia's official API, or your own self-hosted
  SearXNG. A paid key you already have can be added as an optional emergency
  fallback; it is never required and never called unless you configure it.
- **Works with your own setup.** Local-first: pages are fetched and extracted
  on your machine, session state and caches live in `~/.iralens`, and nothing
  is sent to the project authors. Optional tools (`yt-dlp`, `gh`, OCR
  binaries, the browser engine) unlock more capabilities as you install them.
- **Safe by default.** An SSRF guard on every URL, redirect-validated
  DNS-pinned fetches, response caps and deadlines, credentials scrubbed from
  errors, and Internet content always flagged as untrusted data — never
  instructions.
- **Honest about limits.** Every search reports per-engine outcomes; every
  failure is a typed error with a reason; every limit is documented in
  [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md). `iralens doctor` always tells
  the truth about what works right now.

## Quick start

Three commands on Linux or macOS (Python 3.10+):

```bash
git clone https://github.com/pritamduttachoudhury0-collab/IraLens.git && cd IraLens && ./scripts/setup.sh
source .venv/bin/activate
iralens doctor
```

`setup.sh` creates the virtualenv, installs IraLens, and runs a health
check. `doctor` shows exactly what is available on your machine. Then:

```bash
iralens search "solar cell efficiency" --limit 5
iralens read "https://arxiv.org/pdf/1706.03762"
iralens research "What efficiency did solar panels reach in 2025?" --rounds 2
```

Windows setup is provided (`scripts/setup.ps1`) and is untested. The package
is not yet published to PyPI. Full guide:
[docs/getting-started.md](docs/getting-started.md).

## Features

| Feature | What you get |
|---|---|
| **Search** | Keyless engine chain (DuckDuckGo, Bing — experimental — Wikimedia official API, optional self-hosted SearXNG), ranked results with per-engine outcomes, cross-engine dedup, explainable scores, and honest `engines_failed` reporting instead of invented results. |
| **Page reading** | Any URL through specialized readers → fast local static extraction → local browser fallback for JS-heavy pages, with typed errors (`http_403`, `soft_404`, `antibot_challenge`, …) that say exactly what happened. |
| **Documents** | PDF text extraction with ligature/hyphen normalization and a 20 MB cap; scanned PDFs get automatic **local** OCR when `pdftoppm` + `tesseract` are installed — never uploaded. |
| **Browser** | Integrated native browser engine (`iralens._internal.browser_engine`, V8 + HTML5 DOM): navigate, snapshot, click, fill, extract, evaluate JS, tabs, RFC 6265 cookies, storage state. Bundled out of the box with zero runtime binary downloads; stealth off; stdio-only control. |
| **API** | Single-node HTTP service (`iralens serve`): native `/v1/search` and `/v1/read`, a Serper-compatible `/search` for a base-URL swap, authenticated metrics, mandatory token on non-loopback binds. |
| **Research mode** | Bounded multi-round runs that plan queries, read top sources, flag possible contradictions, and return cited statements with a provenance graph and a replayable trace — deterministic by default. |
| **Safety** | SSRF guards on every URL and inside the engine, DNS-pinned fetches, content-injection flags that demote but never silently drop, credentials in owner-only config scrubbed from all output. |

Specialized sources — GitHub, YouTube, RSS, V2EX, plus Twitter/X, Reddit,
Bilibili, XiaoHongShu, LinkedIn, Xueqiu, Boss直聘, Facebook, and Instagram
with your own credentials — each report honest health status with
multi-backend fallbacks. Per-capability status:
[docs/getting-started.md](docs/getting-started.md#capability-status).

## Architecture

```
                    ┌────────────────────────────────────────────┐
  AI agent ───────► │  facade · CLI · MCP server · HTTP service  │
                    ├────────────────────────────────────────────┤
                    │  search: keyless engines, fallback, dedup, │
                    │  ranking, filters, persistent circuit state│
                    │  read: static → PDF/OCR → browser render   │
                    │  research: planner → evidence → citations  │
                    │  guards: SSRF · DNS pinning · injection    │
                    │  flags · budget caps · credential hygiene  │
                    ├────────────────────────────────────────────┤
                    │  unified Artifact model · session · errors │
                    └────────────────────────────────────────────┘
                                       │
            search engines · web pages & PDFs · platforms · browser engine
```

Module-level detail: [ARCHITECTURE.md](ARCHITECTURE.md). Design rationale:
[DECISIONS.md](DECISIONS.md).

## Examples

### CLI

```bash
iralens search "rust headless browser" --limit 5 --json
iralens search-api "solar cell efficiency" --include-domain nrel.gov --cache bypass
iralens open https://github.com/owner/repo          # source-aware open
iralens read https://example.com --max-chars 8000
iralens navigate URL && iralens click "a.next" && iralens extract '{"rows[]": "tr"}'
iralens fetch github search_repos query=cdp limit=5
iralens sources | doctor | session                  # inventories and health
iralens mcp                                          # start the MCP server
iralens serve                                        # HTTP API on 127.0.0.1:8000
```

Every command accepts `--json` (stable exit codes: 0 ok — including an honest
zero-results answer — 2 caller error, 3 internal error, 4 every search
engine failed, 130 interrupted).

### Python

```python
from iralens import IraLens

with IraLens() as lens:
    results = lens.search("distributed consensus", limit=5)
    page = lens.read(results[0].url)
    report = lens.research("What efficiency did solar panels reach in 2025?",
                           options={"max_rounds": 2})
    print(report.render_text())
```

### MCP

```json
{ "mcpServers": { "iralens": { "command": "iralens", "args": ["mcp"] } } }
```

49 tools (`search`, `search_api`, `research`, `read`, `open`, `navigate`,
`source_fetch`, `doctor`, …) over stdio; tool errors are typed JSON.

### HTTP API and the paid-style swap

```bash
export IRALENS_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
iralens serve                                         # or: IRALENS_API_HOST=0.0.0.0 iralens-api
curl -H "Authorization: Bearer $IRALENS_API_KEY" -H 'Content-Type: application/json' \
  -d '{"query":"solar cell efficiency","max_results":5}' http://localhost:8000/v1/search
```

A client built against a Serper-style endpoint can swap providers by changing
**only its base URL and key**:

```python
# before: base "https://google.serper.dev", key = paid key
# after:  base "https://<your-host>",       key = your IraLens token
requests.post(f"{base}/search", headers={"X-API-KEY": key}, json={"q": q, "num": 10})
```

`POST /search` answers `{"q", "num"}` with `organic` results plus an `iralens`
diagnostics object. Coverage is general web search only; proprietary Google
features (knowledge graph, verticals, direct-answer cards) are intentionally
not synthesized. Full reference: [docs/api.md](docs/api.md). A Docker Compose
stack with a private SearXNG is included — build and verify it on your host;
see [docs/deployment.md](docs/deployment.md).

## Measured results

Only measured numbers, each dated with its evidence. Anything not measured
says so.

| What | Result | Date | Evidence |
|---|---|---|---|
| Offline + local-fixture test suite (Python 3.11) | **608 passed, 14 skipped, 0 failed** (includes local V8 browser integration tests; skipped tests require live network) | 2026-10-18 | `tests/` · [error register](docs/audit/ERROR_REGISTER.md) |
| Audit acceptance suite (17 offline checks, inverted so each asserts the defect is gone) | **17/17** | 2026-10-18 | `python benchmarks/acceptance_17.py` |
| Scraping test (read/extract probes: budget, main content, JS shell, script leakage, typed 429, block classification, cache provenance, concurrent saves, typed refusal) | **12/12** | 2026-10-18 | `python benchmarks/run_scraping.py` |
| Frozen 3,000-query corpus through the engine (offline pipeline) | **14,262/14,262 clean**; the same corpus re-scored from the audit's own captured baseline run shows 8,435 code-caused and 5,223 external failures out of 31,264 checks, every code-caused family now fixed | 2026-10-18 | `python benchmarks/run_audit3000.py --offline-pipeline` · `--replay` |
| Projected 3,000-query score, each number traced to the fix that produces it | **3,000/3,000** with no code-caused failure; **2,955/3,000** with no defect at all; **1,637/3,000** under the strictest reading | 2026-10-18 | `python benchmarks/project_score.py` |
| Retrieval quality against **live providers**, results read by hand | the `focus` query form returns **7/7** on-target for the nginx question where the loose engine returns **0/10** for the full question; the coverage metric agrees with the human labels in **10/10** captures (6/10 before the fix) | 2026-10-18 | `python benchmarks/ab_query_forms.py` |
| Subject/request split, scored against the corpus's own recorded topics | **2,619 / 2,950 = 88.78%** recovered exactly; the request lexicon scores **89.02%** on a held-out half | 2026-10-18 | `python benchmarks/measure_focus_split.py` · `derive_request_markers.py` |
| Lint (`ruff --select E9,F,B`) + types (`mypy`) | clean; 81 source files typed | 2026-10-18 | `docs/audit/ERROR_REGISTER.md` |
| Evidence-driven search improvement (D1–D8) | Ranking/intent, script handling, degradation, date provenance and security regressions covered offline; live quality P@5, 100-page real extraction and live engine resilience **NOT MEASURED** in restricted egress | 2026-10-17 | [final report](docs/improvements/FINAL_REPORT.md) · frozen 300-query holdout in `benchmark/holdout/` |
| Live 3,000-query run (frozen corpus, live engines) | **NOT MEASURED** — the verification environment reaches only package indexes and GitHub, so the provider-side captchas and throttles could not be exercised; the runner reports `not_run` with the reason | — | `python benchmarks/run_audit3000.py --live` |
| Lint (`ruff --select E9,F,B`) + types (`mypy`) | clean; 74 source files typed | 2026-10-17 | [final verification](docs/audits/final-verification.md) |
| Full suite on an open network | **416 passed, 0 failed**; DuckDuckGo 15/15 and Bing 15/15 independent live queries | 2026-10-14 | [audit](docs/audits/paid-search-replacement-audit-2026-10-14.md) |
| Search quality (100-query audit slice) | exact-site top-3 **24/25**; facts top-10 **25/25**; recent news **20/20** | 2026-10-14 | [audit](docs/audits/paid-search-replacement-audit-2026-10-14.md) |
| Research accuracy (12 questions) | **12/12** correct answer present; **0 invented citations** | 2026-10-14 | [audit](docs/audits/paid-search-replacement-audit-2026-10-14.md) |
| Page reading (60-page audit, pre-repair code) | **33/60 (55%)** — measured before the reading repairs; the repair set is regression-tested, the live re-run is not yet verified | 2026-10-14 | [audit](docs/audits/paid-search-replacement-audit-2026-10-14.md) · [triage](docs/audits/page-read-triage-2026-10-14.md) |
| Load (pre-repair code) | median 1.44 s; 20-way burst p95 18.8 s — **failed** the <6 s target, which is why the concurrency rework exists | 2026-10-14 | [audit](docs/audits/paid-search-replacement-audit-2026-10-14.md) |
| Ranking scorer (synthetic 20-case set) | Historical 2026-10-09 run: MRR 0.875 → 0.975; after 2026-10-17 evidence-driven changes: MRR **0.925**, nDCG@5 **0.945** vs legacy 0.875 / 0.908. Synthetic only, not live search quality. | 2026-10-17 | `tests/test_ranking_benchmark.py` · [final report](docs/improvements/FINAL_REPORT.md) |
| Frozen acceptance benchmark (200 queries / 100 URLs, 3 consecutive passes) | not yet verified — awaits an open-network run | — | [BENCHMARKS.md](BENCHMARKS.md) |
| Paid-provider parity | not yet verified — no provider key has been supplied | — | [benchmarks/README.md](benchmarks/README.md) |
| Docker / Compose stack | not yet verified on a live host | — | [docs/deployment.md](docs/deployment.md) |
| Seven-day daily-watch record | accumulating — the canary runs daily; no multi-day claim yet | — | [scripts/daily_watch.py](scripts/daily_watch.py) |

## Verification runners

Every claim above has a command that produces it.

```bash
python benchmarks/acceptance_17.py            # 17 offline acceptance checks
python benchmarks/run_scraping.py              # the read/extract probes
python benchmarks/run_audit3000.py --self-test        # validate the 3,000-query harness
python benchmarks/run_audit3000.py --offline-pipeline # 3,000 queries, no network
python benchmarks/run_audit3000.py --live             # 3,000 queries, live engines
python benchmarks/run_audit3000.py --replay FILE      # score a captured run
python benchmarks/project_score.py             # project the 3,000-query score
```

The 3,000-query corpus is `benchmarks/audit3000/queries.jsonl`, hash-verified
against `benchmarks/audit3000/dataset.sha256`; it is never edited to move a
number. Failures are separated into **code-caused** (this project can fix
them) and **external** (a provider captcha or throttle); a family that could
not be run is reported as `not_run`, never as a pass.

`benchmarks/project_score.py` projects the per-query score: **3,000/3,000**
with no code-caused failure, **2,955/3,000** with no defect at all, and
**1,637/3,000** under the strictest reading, with every number traced to the
register entry that produces it. It is a projection — only a live run
measures a score.

## Known limits

Live results depend on your network and the search sources you configure;
bot walls and rate limits are reported honestly rather than hidden. The
browser engine is optional and anything that needs it says so. Research
output is evidence leads, not verified answers. The full list —
verification gaps, research heuristics, search behavior, packaging — is in
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## Responsible use

IraLens fetches public pages the way your browser would, and the sites you
query see your requests. Check each source's terms before automating at any
scale (the per-source inventory is in [docs/search.md](docs/search.md)),
keep request pacing polite — the built-in per-site concurrency limits help —
respect paywalls and login walls, and use your own credentials and sessions
for authenticated platforms. If a provider blocks you, stop; do not rotate
endpoints to work around it.

## Documentation

[Getting started](docs/getting-started.md) ·
[Search](docs/search.md) ·
[Reading pages & documents](docs/reading-pages-and-documents.md) ·
[API](docs/api.md) ·
[Research mode](docs/research.md) ·
[Security](docs/security.md) ·
[Deployment](docs/deployment.md) ·
[Benchmarks](BENCHMARKS.md) ·
[Known limitations](KNOWN_LIMITATIONS.md) ·
[Decisions](DECISIONS.md) ·
[Changelog](CHANGELOG.md)

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q            # offline suite; live tests auto-skip without egress
ruff check --select E9,F,B .   # lint gate
mypy iralens                   # type gate
```

Contributions welcome: [CONTRIBUTING.md](CONTRIBUTING.md). Security reports:
[SECURITY.md](SECURITY.md). Behavior expectations:
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

Apache License 2.0 ([LICENSE](LICENSE)). [NOTICE](NOTICE) carries the
copyright line and the attributions for incorporated open-source
components; the component audit is in
[docs/audits/license-audit.md](docs/audits/license-audit.md).
