# IraLens 0.3.5 release report

**Release:** IraLens 0.3.5  
**Starting point:** `main` at
`ab54804964eb9d811222ca2e16e7b1b8a84f11a2` (version 0.3.1)  
**Obscura:** upstream `v0.2.5`, revision
`bd9529418b7fa975b50eabeac415cfaaa865a588`

Status labels used below: **Implemented** (code is present),
**Verified Offline** (covered by deterministic offline tests or local
fixtures), **Partially Verified** (some, but not all, layers exercised), and
**Not Verified Live** (requires an authorised live environment).

## Branches inspected and integrated

| Branch | Head inspected | Decision |
|---|---|---|
| `arena/1bcf9252-iralens` | `313eecedc0d19d177ad0f35b17c9bbb60c10daf1` | **Integrated.** Newer stealth tier: coherent browser identity, browser-shaped transport, optional `curl_cffi` TLS impersonation, interstitial waiting, opt-in CAPTCHA solver adapters, structured capability reporting, credential safeguards, and challenge metadata. |
| `arena/3586248a-iralens` | `b91f9bd` | **Integrated.** Keyless specialist search (GDELT, arXiv, Crossref, GitHub, Stack Exchange), Mojeek in the default rotation, adaptive reformulation, token-boundary coverage, and multilingual routing. The one merge conflict, in `KNOWN_LIMITATIONS.md`, was resolved by keeping the stealth numbering and renumbering the search limitations sequentially. |
| `arena/4e9b550b-iralens` | `e01fe4b` | **Reviewed, not merged.** An earlier, alternate stealth implementation for the same tier. It is superseded by the newer branch above, which covers the same identity-consistency ground and adds the transport, TLS-reporting and challenge-handling layers with a larger test suite. Its unique files (`docs/stealth.md`, `scripts/verify_stealth.py`, an alternate `stealth.py`) were not carried over, to avoid shipping two competing stealth implementations. The branch is retained on the remote because it still contains that unique, unmerged work. |

No branch was deleted. `arena/1bcf9252-iralens` and
`arena/3586248a-iralens` are fully merged into this release;
`arena/4e9b550b-iralens` is intentionally retained as the only copy of the
superseded implementation.

## Main implementation changes

- **Stealth tier (Implemented, Verified Offline).** One `StealthProfile`
  drives Chrome-shaped request headers, client hints, and page-visible
  identity for the whole session. It is off by default and enabled with
  `stealth=true` or `--stealth`.
- **Browser-shaped transport (Implemented, Verified Offline).** Persistent
  per-origin HTTP/1.1 connections, Chrome header order/casing, honest
  `Accept-Encoding`, and gzip/deflate/brotli/zstd decoding replace the
  `urllib` request path in the browser tier.
- **Optional TLS impersonation (Implemented, Partially Verified).**
  `curl_cffi` is an optional extra (`iralens[stealth]`). Availability is
  detected and reported; without it the engine works and says the capability
  is unavailable. The version-mapping contract is tested both with the
  backend installed (44 stealth tests pass) and without it (43 pass, 1
  correctly skipped).
- **Challenge handling (Implemented, Verified Offline with mocks).** Four
  outcomes are distinguished: JavaScript interstitials are waited out within
  a bounded window; interactive CAPTCHAs go only to an operator-configured
  solver using the operator's own key; authentication-required and
  access-denied responses are reported, not retried through another
  identity. Unresolved challenges raise a typed `verification_required`
  error and never enter the pipeline as content.
- **Obscura upgrade (Implemented, Verified Offline).** Provenance corrected
  to v0.2.5, prebuilt-binary version bumped to 0.2.5, and two post-v0.2.5
  upstream `bootstrap.js` fixes adopted with tests. Details:
  [OBSCURA_UPGRADE_REPORT.md](OBSCURA_UPGRADE_REPORT.md).
- **Keyless search expansion (Implemented, Verified Offline).** Intent-routed
  GDELT, arXiv, Crossref, GitHub and Stack Exchange adapters; Mojeek in the
  default rotation; adaptive reformulation with answerability gating;
  token-boundary coverage; improved multilingual routing.
- **Version (Implemented, Verified Offline).** Package, runtime, MCP
  handshake, GDELT user agent, README badge and changelog all report
  0.3.5; an existing test pins that agreement.

## Tests and checks actually run

Environment: Python 3.11, offline, 2026-10-19.

| Command | Result |
|---|---|
| `python -m pytest -q` (baseline `main`, before integration) | 829 passed, 14 skipped |
| `python -m pytest -q` (0.3.5 tree) | **902 passed, 14 skipped, 0 failed** |
| `python -m pytest tests/test_stealth.py -q` with `curl_cffi` installed | 44 passed |
| `python -m pytest tests/test_stealth.py -q` without `curl_cffi` | 43 passed, 1 skipped |
| `ruff check --select E9,F,B .` | clean |
| `mypy iralens` | clean, 92 source files |
| `python benchmarks/acceptance_17.py` | 17/17 |
| `python benchmarks/run_scraping.py` | 12/12 |
| `python -m build` + `twine check dist/*` | wheel 1.3 MB, sdist 1.5 MB, both pass |
| `iralens doctor` | browser engine ready (v0.2.5) |

One branch test was corrected during integration:
`test_the_tls_profile_matches_the_claimed_chrome_version` silently assumed
the optional `curl_cffi` backend was installed. It now pins both halves of
the contract — the honest generic fallback without the backend, and the
exact/closest-older profile mapping with it — and passes in both states.

## Performance and memory

Local fixtures only (a `ThreadingHTTPServer` serving one static and one
JavaScript page; 10 browser opens, 5 of each):

| Build | Total | Per open | Process-tree RSS (sampled) |
|---|---|---|---|
| 0.3.1 baseline (`ab54804`) | 0.328 s | 0.033 s | 136.5 MB |
| 0.3.5 | 0.343 s | 0.034 s | 136.9 MB |

No material regression was observed; the difference is within run-to-run
noise for a local fixture. These are local-fixture numbers, not live-web
performance claims. The Rust binary tier was not compiled in this
environment, so no native-binary size or performance figure is claimed.

## Security checks

Covered by the offline suite and not weakened for this release: SSRF and
private/loopback/link-local destination blocking, redirect-by-redirect
revalidation, subresource SSRF blocking, credential stripping across
untrusted cross-origin redirects, response-size and timeout limits, prompt
-injection treatment of retrieved content, and credential isolation (proxy
and solver keys travel through the environment, never argv, URLs, logs or
artifacts). The full test suite includes the security, SSRF and
credential-handling modules; all pass.

## Known limitations

- Live anti-bot effectiveness of the stealth tier: **Not Verified Live**.
- Real CAPTCHA-solving success with any provider: **Not Verified Live**
  (offline tests mock provider responses only).
- Broad modern-website compatibility: **Not Verified Live**.
- Live reachability, quotas and terms for the new specialist APIs:
  **Not Verified Live**.
- Concept-level retrieval quality on generic queries remains the principal
  open gap; the 12,000-query paired benchmark has still not been run.
- The from-source Rust build was not exercised in this environment.
- The superseded stealth branch `arena/4e9b550b-iralens` is retained on the
  remote as the only copy of that implementation.

The full live-validation procedure is in
[LIVE_VALIDATION_PLAN.md](LIVE_VALIDATION_PLAN.md).

## Delivery

- Final commit: recorded at the end of the release process (see the
  `v0.3.5` tag on `main`).
- The release is delivered on `main`; the GitHub release and `v0.3.5` tag
  are created from that commit, and the `release.yml` workflow attaches the
  wheel, sdist and checksums.
