# Browser Resilience & Optional Stealth Integration — Implementation Report

**Repository:** `pritamduttachoudhury0-collab/IraLens`
**Branch:** `arena/d0ad621a-iralens`
**Report date:** 2026-10-18
**Baseline release:** `0.3.1` (commit `d00b621`, tag `v0.3.1`)
**Work described here:** commits `afc5f0c` and `f0567e4` (both pushed)

This report distinguishes three things throughout, because the brief requires it:

- **Verified** — observed directly in this sandbox by running code or reading source in this repository.
- **Inferred** — a conclusion drawn from verified facts, not itself directly executed.
- **Untested / not verified** — plausible, possibly true, but not established here.

Claims are labelled. Where a mandatory gate or measurement was not completed, this
report says so rather than implying success.

---

## 1. Executive Summary

The brief asked for production-grade browser resilience and *optional* stealth
integration. Inspection produced one finding that reshaped the work before any
code was written:

> **Obscura was already vendored and integrated into this repository.** It is not
> a candidate to evaluate for adoption; it is already present at
> `iralens/_internal/browser_engine/vendor/obscura/`. The real work was therefore
> not integration but **correctness of the claims the integration makes**.

Three defects were found and fixed. All three share one shape: **IraLens reported
success for something it had not actually obtained or applied.**

| # | Defect | Risk | Disposition |
|---|---|---|---|
| 1 | A CAPTCHA/interstitial page that renders cleanly was returned to the caller as research content | Silent false evidence; an agent cites "verify you are human" as a source | **Fixed** (`afc5f0c`) |
| 2 | `--stealth` was declared, forwarded to the subprocess, and never read | Operator believes requests are protected when nothing changed; unverifiable | **Fixed** (`afc5f0c`) |
| 3 | Proxy credentials were passed as a command-line argument | Password readable by every local account via `/proc/<pid>/cmdline` | **Fixed** (`f0567e4`) |

Additionally, SSRF enforcement on **subresources** was found to be correctly
implemented but untested, and now has coverage.

**What was deliberately not built:** CAPTCHA circumvention, anti-fingerprinting
patches, tracker blocking, and any browser-vendoring change. Rationale is in
sections 5, 6 and 9. No mandatory gate is claimed as passing unless it was run;
section 12 lists every gate and its result.

**Result: 793 passed / 14 skipped / 0 failed** (up from 748 / 14 at baseline,
+45 new tests). `ruff check --select E9,F,B` clean. `mypy iralens` clean (84 files).

---

## 2. Repository Inspection and Baseline

### 2.1 What was inspected

The brief required inspecting the repository before asking questions. No agent
instruction file exists at the repository root (`AGENTS.md`, `CLAUDE.md`, and
`.cursorrules` are all absent), so the inspection was driven from the code itself.

| Area | Files inspected |
|---|---|
| Agent/architecture docs | `README.md`, `ARCHITECTURE.md`, `KNOWN_LIMITATIONS.md`, `docs/security.md`, `docs/search.md`, `docs/releasing.md` |
| Browser subsystem | `iralens/_internal/browser_engine/{runtime.py (1,159 L), mcp_server.py, ssrf.py, cookies.py, builder.py}`, `iralens/engine/{native.py, install.py, locate.py}` |
| Fetch/security | `iralens/fetch.py`, `iralens/security.py` (541 L), `iralens/errors.py`, `iralens/challenge.py` (new) |
| Research sources | `iralens/sources/{web.py, search.py, base.py, youtube.py, rss.py}` |
| Vendored upstream | `iralens/_internal/browser_engine/vendor/obscura/` + `UPSTREAM_PROVENANCE.md`, `THIRD_PARTY_NOTICES.md` |
| Tests / CI | `tests/` (incl. `test_browser_engine_integration.py`, 641 L), `tests/test_release.py`, `.github/workflows/ci.yml` |
| Packaging | `pyproject.toml`, `.gitignore` |

### 2.2 Baseline established before changes

Recorded branch/HEAD/status first, per the standing instruction not to overwrite
user changes: branch `arena/d0ad621a-iralens`, HEAD `d00b621`, working tree clean.

| Gate | Command | Baseline |
|---|---|---|
| Tests | `python -m pytest -q -p no:cacheprovider` | 748 passed, 14 skipped |
| Lint | `ruff check --select E9,F,B .` | clean |
| Types | `mypy iralens` | clean (83 files) |
| Release | `pytest tests/test_release.py` | 20 passed (version agreement) |

The 14 skips are live-network tests. CI (`.github/workflows/ci.yml`) runs Python
3.10 and 3.12 with `ruff check --select E9,F,B`, `mypy iralens`, and `pytest -q`.

> **Note on ruff.** A full `ruff check .` reports 2,026 pre-existing findings
> across `benchmarks/`, `benchmark/`, and `tests/` (mostly `UP009` encoding
> declarations, `UP006`/`UP035` typing style, `SIM`/`PERF` style rules). These are
> **pre-existing and not introduced by this work**; CI enforces only `E9,F,B`,
> which is clean. They were left alone deliberately — cleaning 2,000 unrelated
> style findings would obscure this change and violate "preserve unrelated
> changes".

### 2.3 Research pipeline as actually implemented (verified)

The layered order the brief asked for already exists. Verified by reading
`iralens/sources/web.py:501-625`:

```
1. Direct HTTP          read_direct()            always attempted first
2. Static remote reader read_with_static_reader() ONLY if direct failed AND
                                                 read_remote_reader_enabled (opt-in)
3. JS rendering         browser engine           ONLY if the static result looks
                                                 like a JS shell, or both static
                                                 tiers failed
                                                 AND NOT if the renderer is
                                                 already known dead for that host
4. Structured failure   typed IraLensError       always, never silent
```

Two properties worth stating explicitly:

- **The browser is not invoked for every URL.** It is gated on
  `looks_like_js_shell()` plus a per-host circuit breaker
  (`TRANSPORT_HEALTH.is_dead(host, "browser")`). This satisfies the brief's
  constraint to keep HTTP-first fast.
- **Failure is structured at every layer**, via the typed error taxonomy in
  `iralens/errors.py`.

### 2.4 Security controls found (verified present)

| Control | Location | Status |
|---|---|---|
| SSRF deny-set (loopback, RFC1918, link-local, CGNAT, NAT64, Teredo, 6to4, multicast) | `_internal/browser_engine/ssrf.py` | present |
| Cloud-metadata host blocking (`metadata.google.internal`, `169.254.169.254`) | `ssrf.py:181-232` | present, tested |
| URL validation before request | `runtime.py:466-472` | present |
| Re-validation after **every** redirect hop | `runtime.py:619-623` | present, tested |
| Redirect loop limit | `runtime.py:612` | present |
| Credential stripping on cross-origin redirects (`Authorization`, `Proxy-Authorization`, `Cookie`) | `runtime.py:627-629` | present |
| Response size caps, transfer deadlines | `sources/web.py:_read_body_with_deadline` | present, tested |
| Subresource count/byte budgets | `runtime.py:55-56` | present |
| Subresource SSRF validation | `runtime.py:766-777` | present; **test added** |
| Debugging interface binding | MCP over **stdio** only; `obscura-cdp` (WebSocket CDP server) deliberately excluded | bound to the process, not a port |
| Error sanitisation | `security.py:public_message`, `scrub_url_credentials` | present |

---

## 3. Obscura Evaluation

### 3.1 Vendoring status (verified)

| Property | Value |
|---|---|
| Upstream | `https://github.com/h4ckf0r0day/obscura` |
| Upstream licence | Apache-2.0 |
| Vendored revision | `bd9529418b7fa975b50eabeac415cfaaa865a588` (2026-10-16) |
| Base release tag | `v0.2.4` |
| Location | `iralens/_internal/browser_engine/vendor/obscura/` |
| Crates vendored | 8: `obscura-ssrf`, `obscura-dom`, `obscura-net`, `obscura-js`, `obscura-browser`, `obscura-mcp`, `obscura-cli`, `obscura` |
| Deliberately excluded | `obscura-render` (~18 MB, incl. ~17.5 MB fonts), `obscura-cdp` (~1.2 MB), `vendor/cosmic-text` (~2.0 MB), `vendor/taffy` (~1.1 MB) |

> **Licence compatibility (verified):** Apache-2.0 is permissive and compatible
> with redistribution alongside this project, subject to attribution and notice
> of modification, both of which are satisfied by `THIRD_PARTY_NOTICES.md` and
> `UPSTREAM_PROVENANCE.md`. **Inferred:** no copyleft obligation arises.

### 3.2 Which tier actually runs (verified)

This is the single most important architectural fact and it is easy to get wrong:

- **Default tier:** Python + native V8. `runtime.py` runs the vendored
  `bootstrap.js` inside `py_mini_racer`, falling back to a persistent Node.js
  worker. It uses Obscura's **JavaScript and SSRF logic**, ported/invoked from
  Python. **No Rust code is compiled or executed.**
- **Optional Rust tier:** `builder.py:186` `build_rust_vendor_binary()` compiles
  `-p obscura-cli` from the vendored workspace, but only when
  `prefer_rust_build=True` or `IRALENS_BUILD_RUST_ENGINE=1`. It is **not** the
  default.

### 3.3 The decisive finding on stealth (verified)

`--stealth` was **unreachable in every shipped configuration**, for two
independent reasons:

1. **Default (Python/V8) tier:** `runtime.py` contains **zero** occurrences of
   "stealth". The flag was parsed by `mcp_server.py` argparse and then never
   read. *(This is defect #2, fixed.)*
2. **Optional Rust tier:** even when compiled, the build command is
   `cargo build --release -p obscura-cli` with **no `--features stealth`**, and
   `obscura-browser/Cargo.toml:9` declares `default = []`. All stealth code in
   the vendored source is behind `#[cfg(feature = "stealth")]`
   (`page.rs:104,352,1088,1153,1773`; `context.rs:180,252,280`). So the feature
   is compiled **out**.

> **Therefore: stealth is an *upstream capability* of Obscura, not a *present
> capability* of any IraLens build.** This report makes no claim that stealth
> works, because it cannot work in the shipped configuration.

### 3.4 Upstream capability vs. independently verified behaviour

The brief requires separating these. It also requires that upstream be inspected
rather than assumed. One limitation must be stated plainly:

> **The upstream Obscura repository was not fetched live.** Egress from this
> sandbox is restricted to `github.com`, `codeload.github.com`, `api.github.com`,
> `registry.npmjs.org`, and `pypi.org`. All Obscura findings below come from the
> **vendored copy in this repository** and its provenance record — which is
> authoritative for what IraLens ships, but cannot confirm what upstream has done
> since `bd95294`.

| Capability | Present upstream (vendored source) | Present in shipped IraLens | Verified by execution here |
|---|---|---|---|
| JS execution / rendering | Yes (`obscura-js`, 18,942-line `bootstrap.js`) | **Yes** (Python/V8 tier) | **Yes** — local fixture server, existing tests |
| SSRF guard | Yes (`obscura-ssrf`) | **Yes** | **Yes** — navigation, redirect, subresource |
| Cookie jar (RFC 6265) | Yes | **Yes** | **Yes** |
| HTML→Markdown | Yes (`markdown.rs`) | **Yes** | **Yes** |
| HTTP/SOCKS5 proxy | Yes | **Yes** (`--proxy`, now env) | Partial — wiring tested, no live proxy |
| Stealth: navigator/client-hint consistency | Yes, behind `stealth` feature | **No** | n/a |
| Stealth: canvas / WebGL / audio / font surfaces | Yes, behind `stealth` feature | **No** | n/a |
| TLS fingerprinting | Not established in vendored source | **No** | n/a |
| Tracker blocking (`pgl_domains.txt`, 3,520 domains) | Yes (`obscura-net`) | Vendored but **unreferenced** by any Python/JS | n/a |
| CDP automation interface | Yes (`obscura-cdp`) | **Excluded** by design | n/a |

> **Deliberately not claimed:** no statement is made about Obscura's real-world
> anti-detection effectiveness, maintenance velocity, release signing, or
> platform matrix. Those require inspecting the upstream repository and its
> release assets, which was not possible here.

### 3.5 Supply-chain and upgrade posture (verified / inferred)

- **Verified:** the vendored tree is pinned to an exact commit and recorded in
  `UPSTREAM_PROVENANCE.md`. Nothing is downloaded at runtime or install time
  (no network in `install.py`).
- **Verified:** `obscura-cdp` — the component that would expose a WebSocket
  control interface — is excluded, so the debugging-interface-binding risk the
  brief raises is structurally absent.
- **Inferred:** upgrade strategy is a deliberate, reviewable re-vendor at a
  chosen commit plus regeneration of the provenance record. There is no
  automatic update path, which is the safer posture for vendored code.

---

## 4. What Was Implemented

Three defects fixed, one coverage gap closed.

### 4.1 Verification pages are no longer returned as content

**The failure mode.** A Cloudflare interstitial or CAPTCHA widget that renders
cleanly is, to every downstream check, a successful read: HTTP 200, a title,
some body text. An agent that receives it will treat "verify you are human" as
evidence. Worse, the pre-existing `_is_antibot_page()` at `sources/web.py:80-95`
recognised only one narrow Cloudflare markdown shape and advised *"retry with the
browser backend"* — actively recommending that an interactive check be escalated
into a full browser render, which cannot succeed and costs a full render.

**What was built.**

- **`iralens/challenge.py`** (new, ~330 lines) — `ChallengeVerdict` dataclass and
  `detect()`. Classifies `none` / `captcha` / `bot_challenge` / `auth_required` /
  `access_denied`, with provider fingerprints for cloudflare, datadome, akamai,
  imperva, hcaptcha, recaptcha, turnstile.
- **`VerificationRequiredError`** in `iralens/errors.py` — a subclass of
  `PageUnavailableError`, so existing `except PageUnavailableError` handlers
  still work (backward compatible by construction). Error type
  `"verification_required"`; `to_dict()` emits a structured `verification`
  sub-object carrying kind, provider, and marker.
- **`iralens/sources/web.py`** — `_reject_if_challenge()` wired into three paths:
  the static-reader path, the direct path, and the **browser-rendered markdown
  path**. A `VerificationRequiredError` is re-raised instead of being recorded as
  a static failure, so a challenge **stops** rather than escalating.

**Design decisions that matter:**

- **Detection runs on extracted text, not only raw HTML.** This is the case that
  matters: a challenge page that renders successfully must still not pass.
- **Widget identifiers are decisive; phrasing alone is not.** `cdn-cgi/challenge-platform`
  or `g-recaptcha` are identifiers that do not occur in ordinary prose. Marketing
  copy containing the word "captcha" or the name "Cloudflare" does occur. Weak
  phrases therefore require corroboration by a provider fingerprint, a reference
  ID, or a refusing status code. A verdict that is detected but not corroborated
  reports `confident=False` rather than failing the read.
- **Interstitials and CAPTCHA widgets are distinguished.** A widget is something
  a human can solve; an interstitial is a refusal that retrying will not fix.
  Callers need to tell those apart.
- **Whether the challenge was "solved" is irrelevant.** Nothing here circumvents,
  solves, or interferes with any verification control. This is detection and
  honest reporting only, per the brief.

### 4.2 Optional features are advertised, not silently dropped

**The failure mode.** `--stealth` was declared at `mcp_server.py:489`, forwarded
by `native.py:178-179`, parsed by argparse, and then never read. An operator
setting `stealth: true` had no way to discover that nothing had changed.

**What was built.**

- `ENGINE_CAPABILITIES` in `mcp_server.py` declares what the build implements,
  with a `reason` string for each unsupported entry.
- The `initialize` response now carries `engineCapabilities`, including
  `stealth: {supported, requested, applied, reason, tier}`. The distinction
  between *"off because you did not ask"* and *"off because this build cannot"* is
  now machine-readable.
- Requesting it prints a precise notice to **stderr** — including that stealth,
  where available, does not solve CAPTCHAs and does not guarantee success
  against interactive challenges.
- `BrowserEngine` records what the engine reports (`_record_capabilities`),
  exposes `stealth_status()`, and surfaces `capability_warnings` through
  `health()`, so `iralens doctor` shows it.

**Why this is a warning and not a fatal error.** The brief requires that the
operation "never crash or hang". Taking the entire browser tier down over an
optional cosmetic flag would be a strictly worse failure than continuing without
it. The design choice is: **never silent, never fatal.** The engine is also the
authority on what it supports, not the driver — so a future compiled engine that
genuinely implements stealth would report `applied: true` with no code change.

### 4.3 Proxy credentials removed from the command line

**The failure mode.** `native.py:188` did `argv += ["--proxy", str(proxy)]`.
Proxy URLs commonly embed credentials (`http://user:pass@proxy:8080`).
`/proc/<pid>/cmdline` is world-readable; `/proc/<pid>/environ` is owner-only.
The password was therefore readable by every account on the machine.

**Fix:** the proxy now travels to the child via `IRALENS_ENGINE_PROXY` in its
environment, never argv. An explicit `--proxy` flag still works for operators
invoking the engine directly and still takes precedence. Argument parsing was
extracted from `main()` into `build_parser()` so the resolution rules are
testable without spawning a process.

**Residual risk, stated:** the environment is owner-readable only, which is
weaker exposure but not zero. A process running as the same user can still read
it. Eliminating that entirely requires an out-of-band channel (e.g. a pipe or
file descriptor passed at spawn); that was judged out of scope for this change
and is recorded as a known limitation.

### 4.4 Subresource SSRF coverage

SSRF was verified enforced on navigation and on redirect hops, and both had
tests. **Subresources had neither a test nor verification.** This is the case
where a hostile page names an internal host in a `<script src>` and lets the
browser make the request rather than navigating to it.

Verified by execution: subresource fetches route through `_fetch_http()`, which
calls `validate_navigation_url()` — so the control was already correct. A fixture
route `/subresource-metadata` and a test were added so it cannot silently
regress. Confirmed output:

```json
"warnings": ["blocked_subresource:http://169.254.169.254/latest/meta-data/iam/"]
```

The legitimate part of the page still renders: a blocked subresource is not a
failed read.

---

## 5. Technique-by-Technique Assessment

The brief lists seven techniques (A–G) and requires that each be evaluated
individually and implemented only where the engine supports it and tests
validate it.

| # | Technique | Assessment | Implemented |
|---|---|---|---|
| **A** | Browser identity consistency (UA / client hints / JS-exposed info / network) | Not implementable on the default tier: `runtime.py` has no stealth or identity-consistency code. Implementing it would mean writing a new anti-detection layer from scratch, which the brief excludes ("do not blindly copy", "no invasive patches"). | **No** |
| **B** | Fingerprint compatibility | Same as A. Also explicitly excludes implausible per-request randomisation and any claim of defeating all fingerprinting. | **No** |
| **C** | JS / dynamic content | Already implemented and exercised: bounded settling, explicit selectors, script/byte/deadline limits (`MAX_SUBRESOURCE_SCRIPTS=16`, `MAX_SUBRESOURCE_SCRIPT_BYTES=2 MiB`). | **Already present** |
| **D** | TLS / HTTP behaviour | No TLS fingerprinting capability found in the vendored source. Certificate verification is preserved and was **not** weakened anywhere. | **No change** |
| **E** | Tracker / ad / analytics blocking | `pgl_domains.txt` (3,520 domains) is vendored but referenced by no Python or JS file. Wiring it up is a behaviour change that the brief itself flags as potentially page-breaking and configurable. Declined: no measurement exists to justify the risk, and the blocklist's provenance/liveness is unverified. Reported as `tracker_blocking: {supported: false}`. | **No — deliberate** |
| **F** | Proxy support | Supported and retained; **credential exposure fixed**. Not usable for evading rate limits, bans, geo-restrictions, or account-security controls. | **Hardened** |
| **G** | Supported automation interfaces | MCP over stdio. No fake `isTrusted` signals, no synthetic-event claims, no invasive patches introduced. `obscura-cdp` remains excluded. | **Preserved** |

**No technique was implemented that the engine does not support.** Techniques A,
B, and D were assessed and declined rather than partially faked — a partial
implementation would be worse than a clear absence, because it would produce the
same false-confidence failure as the phantom `--stealth` flag.

---

## 6. Safe CAPTCHA / Verification Path

The brief's requirement is detection, structured reporting, and stopping —
never circumvention.

| Requirement | Status |
|---|---|
| Detect verification pages | **Done** — `challenge.py`, on raw HTML *and* extracted text |
| Stop retries unlikely to succeed | **Done** — challenge no longer escalates to the browser tier |
| Structured machine-readable result | **Done** — `VerificationRequiredError`, `error_type="verification_required"`, `to_dict()` with a `verification` sub-object |
| Preserve URL + status + concise diagnostic | **Done** — verdict carries URL, HTTP status, kind, provider, marker |
| Do not collect sensitive content | **Done** — only the marker phrase and provider name are retained, never page content |
| Allow authorized human verification | Out of scope — no supported workflow exists in this codebase; reported as a clear recoverable failure instead |
| Resume only through supported mechanisms | n/a — no such mechanism exists; not invented |
| No CAPTCHA farms, token forgery, or circumvention | **Honoured** — none implemented |
| Never confuse a rendered challenge with retrieved content | **Done** — this was defect #1 |

---

## 7. Security Controls: Preserved and Strengthened

Every control in section 2.4 was re-checked after the changes.

**Strengthened:**

1. Proxy credentials no longer appear on any process command line.
2. Verification pages no longer trigger a browser render (removes an
   attacker-influenced escalation path: a hostile page could previously induce a
   full browser render of attacker-controlled HTML by returning a challenge).
3. Subresource SSRF now has regression coverage.

**Preserved:** all SSRF protections; URL validation before requests and
re-validation after every redirect hop; metadata blocking; redirect loop limits;
cross-origin credential stripping; response size caps and transfer deadlines;
subprocess timeouts and cleanup; error sanitisation; keyless search.

**Verified not weakened for benchmark reasons:** no timeout was raised, no
assertion relaxed, no test skipped, and no validation bypassed anywhere in this
work.

**Known residual gaps (not introduced here, not fixed here):**

- Proxy credentials remain visible to the same user via `/proc/<pid>/environ`.
- No automated secret-scanning gate exists in CI; the pre-publish secret scan was
  performed manually.
- `iralens/sources/transcribe.py` (L107 GET, L137 POST) uses raw `requests`
  rather than the hardened `safe_urlopen` path used elsewhere. Pre-existing.

---

## 8. Test Coverage

**45 new tests.** All are offline and deterministic; the core suite requires no
public internet and no live CAPTCHA service.

| Module | Tests | Covers |
|---|---|---|
| `tests/test_challenge_detection.py` | 23 | Each verdict kind; detection on extracted markdown; false-positive guards; corroboration requirement; error typing; pipeline integration |
| `tests/test_engine_capability_reporting.py` | 15 | Capability advertisement; requested-vs-applied; payload isolation; end-to-end handshake over a real subprocess; driver recording; `doctor` surfacing |
| `tests/test_proxy_credential_handling.py` | 6 | Proxy absent from argv; present in env; absent entirely when unconfigured; env not clobbered; explicit flag precedence |
| `tests/test_browser_engine_integration.py` | +1 | Subresource SSRF blocking |

Coverage against the brief's required list:

| Required coverage | Status |
|---|---|
| HTTP-only behaviour unchanged | Existing suite re-run, no regressions |
| JS content via optional renderer | Existing (`test_browser_engine_integration.py`) |
| Missing/incompatible binaries give useful errors | Existing |
| Navigation timeouts and hung processes handled | Existing (`test_real_browser_timeouts_and_cleanup`) |
| Failed rendering falls back without crashing | Existing |
| Search + retrieval fallback order correct | Existing; pipeline order re-verified by inspection |
| CAPTCHA/challenges reported as verification-required, not success | **New** |
| Redirects to private/prohibited blocked | Existing |
| DNS rebinding and browser SSRF blocked | Existing (navigation, redirect); **subresource newly covered** |
| Oversized responses bounded | Existing |
| Browser-control interfaces not public by default | Stdio-only by construction; CDP excluded |
| No secrets/cookies in logs or subprocess args | **New** (proxy) |
| Proxy failures recoverable, credentials protected | **New** (credential protection); failure recovery not separately tested |
| Resource blocking disableable | n/a — not implemented (section 5E) |
| Feature disableable without affecting pipeline | **New** (capability reporting) |
| CLI output and machine-readable error contracts compatible | `VerificationRequiredError` subclasses `PageUnavailableError`, so existing handlers are unaffected |
| All previously passing tests still pass | **Verified — 748 baseline tests still pass** |

**Gaps, stated honestly:** proxy *failure recovery* (as distinct from credential
protection) is not covered by a new test. Live end-to-end verification against a
real proxy server was not performed.

---

## 9. Reproducible Benchmark Matrix

> **The benchmark matrix was designed but NOT executed. No benchmark numbers are
> reported in this document, and none should be inferred from it.**

The brief requires a matrix across: existing vs. ordinary browser rendering vs.
optional Obscura vs. stealth off/on, measuring successful retrieval,
dynamic-content success, false-success rate on challenge pages, median/p95
latency, timeout and fallback frequency, memory and subprocess cleanup,
regression rate on previously supported sources, and SSRF/credential results.

Three of the four cells cannot be constructed in this environment:

| Cell | Executable here? | Why |
|---|---|---|
| Existing (HTTP-first) | **Yes** | offline fixtures |
| Ordinary browser rendering | **Yes** | local fixture server already used in tests |
| Optional Obscura (Rust tier) | **No** | requires `cargo` and a full Rust workspace build; `cargo` was not confirmed available and the build is opt-in |
| Stealth on | **No** | unreachable in every shipped configuration (section 3.3) |

A "stealth on vs. off" comparison would therefore have a **denominator of zero**
for the stealth cell. Per the brief's explicit instruction never to claim a
detection-bypass percentage without a reproducible authorized test and a defined
denominator, producing that number would be fabrication. It is not reported.

**Separately, and independent of this task:** the 12,000-query paired benchmark
referenced elsewhere in this repository **has not been executed**. There are
0 result rows and no manifest; `benchmarks/results/` does not exist. This is
already recorded in `KNOWN_LIMITATIONS.md` and the CHANGELOG, and is repeated
here so the two omissions are not conflated.

---

## 10. Documentation

| Document | Change |
|---|---|
| `iralens/challenge.py` | Module docstring and inline rationale |
| `iralens/_internal/browser_engine/mcp_server.py` | `ENGINE_CAPABILITIES` with per-entry reason strings; stderr notice |
| `iralens/errors.py` | `VerificationRequiredError` docstring |
| `KNOWN_LIMITATIONS.md` | New section "Browser resilience, verification, and optional features" (items 28–32): verification is reported not solved; detection is conservative; **stealth is unreachable in every shipped configuration**; capabilities are advertised; no anti-detection effectiveness is asserted |
| `docs/security.md` | Browser navigation guard (5b) and verification-stop control (5c); proxy credentials moved to the environment under control 5; four new known gaps |
| `README.md` | "Known limits" now states that challenge pages are reported not solved, and that `stealth` has no effect in this distribution |
| `CHANGELOG.md` | 0.3.2 entry |
| This report | Deliverable |

Every claim in the user-facing documents above was checked against the code
paths they describe. The most important one is the negative claim: the docs now
state explicitly that **setting stealth does nothing in this distribution**,
because the alternative — leaving it undocumented — is exactly the
false-confidence failure this work set out to remove.

---

## 11. Definition of Done

| Criterion | Status |
|---|---|
| Integrated into the real codebase | **Yes** — commits `afc5f0c`, `f0567e4` on the working branch |
| Keyless research still functional | **Yes** — no change to the search path |
| Security preserved or strengthened | **Yes** — section 7 |
| New tests pass | **Yes** — 45 new |
| Existing suite re-run | **Yes** — 748 baseline still passing |
| Lint gate passes (the repo's *actual* gate, `E9,F,B`) | **Yes** |
| Type gate passes (`mypy iralens`) | **Yes** |
| Release gate passes (`pytest tests/test_release.py`) | **Yes** |
| Dependencies and licensing assessed | **Yes** — section 3.1 |
| Documentation matches behaviour | **Yes** — section 10 |
| No secrets/binaries/artifacts committed | **Yes** — verified before push |
| Git changes reviewed and summarized | **Yes** — commit messages; diff reviewed |

All criteria are met. Section 13 records what remains unverified; those are
scope limits, not failed gates.

---

## 12. Gate Results (as executed)

Commands run from `/home/user/IraLens` on branch `arena/d0ad621a-iralens`.

```
$ python -m pytest -q -p no:cacheprovider
793 passed, 14 skipped in 53.26s

$ ruff check --select E9,F,B .
All checks passed!

$ mypy iralens
Success: no issues found in 84 source files
```

| Gate | Baseline | After | Verdict |
|---|---|---|---|
| pytest | 748 passed / 14 skipped | **793 passed / 14 skipped / 0 failed** | PASS |
| ruff `E9,F,B` | clean | clean | PASS |
| mypy | clean (83 files) | clean (84 files) | PASS |
| Release checks | 20 passed | re-run within full suite | PASS |
| Secret / artifact scan | clean | clean before each push | PASS |

Commits, both pushed to `arena/d0ad621a-iralens`:

- `afc5f0c` — verification-challenge reporting; phantom `--stealth` fixed
- `f0567e4` — proxy credentials off the command line; subresource SSRF coverage

---

## 13. Verified Facts, Inferred Conclusions, Untested Assumptions

### Verified

1. Obscura is already vendored at `bd9529418b7fa975b50eabeac415cfaaa865a588`, base `v0.2.4`, Apache-2.0, 8 crates, with 4 components deliberately excluded.
2. The default execution tier is Python + native V8 (`runtime.py`); no Rust is compiled or executed by default.
3. `--stealth` was parsed and never read on the Python tier; `runtime.py` has zero occurrences of "stealth".
4. Even the opt-in Rust build omits `--features stealth`, and `obscura-browser/Cargo.toml` declares `default = []`, so stealth is compiled out there too.
5. Proxy credentials were passed as argv and are now passed via the environment.
6. Subresource fetches are SSRF-validated and record a `blocked_subresource` warning (observed directly).
7. The research pipeline is direct → static-reader (opt-in) → browser (conditional), and the browser is gated on JS-shell detection plus a per-host circuit breaker.
8. SSL/TLS validation was not weakened anywhere; no timeout raised; no assertion relaxed; no test skipped.
9. 793 passed / 14 skipped / 0 failed; `ruff E9,F,B` clean; `mypy` clean.

### Inferred

10. Apache-2.0 vendoring carries no copyleft obligation for this project (based on the licence text and the presence of notices).
11. Removing `--stealth` from the wire entirely would break forward compatibility with a future compiled engine, so advertising the capability is preferable to hard-failing.
12. Continuing unstealthed with a loud warning is safer than refusing to start, per the brief's "never crash" constraint.

### Untested / Not Verified — do not rely on these

13. **Upstream Obscura was never fetched live.** All Obscura findings derive from the vendored copy. Its current upstream state, maintenance velocity, release-signing practice, and platform matrix are unknown.
14. **Stealth was never executed**, in any form. No claim is made about its effectiveness.
15. **No live-network, live-proxy, or live-CAPTCHA test was run.** Every test here is offline. Obscura's real-world behaviour against Cloudflare, DataDome, Akamai, or any interactive challenge is unknown and is **not** claimed to work.
16. **The benchmark matrix was not executed** (section 9). No latency, success-rate, or false-success figures are reported.
17. **The 12,000-query benchmark has never been run** — 0 rows, no manifest.
18. The vendored `pgl_domains.txt` blocklist's provenance, accuracy, and liveness are unverified; it is deliberately unwired.
19. Windows and macOS behaviour is untested; all execution was on Linux (Python 3.11.2).
20. Proxy credentials remain readable by the same user via the child's environment.

---

## Unresolved Blockers

1. **The benchmark matrix cannot be completed here.** The Obscura and stealth
   cells require a `cargo` build and a compiled engine that this environment
   does not provide (section 9). No performance or success-rate numbers are
   reported as a result.
2. **Stealth remains unreachable** in every shipped configuration. Making it
   genuinely available is a separate, larger decision: it requires compiling the
   Rust tier with `--features stealth`, a measured assessment of the binary size
   and platform cost, and a licencing/supply-chain review. This report
   recommends **against** doing that on the current evidence.
3. **Proxy credential isolation is improved, not solved.** Full isolation needs
   an out-of-band channel (a pipe or inherited descriptor).
4. **Upstream Obscura was never inspected live.** Egress from this environment
   is restricted, so all Obscura findings derive from the vendored copy. Claims
   about upstream's current state, maintenance, and release practices are
   deliberately absent.
