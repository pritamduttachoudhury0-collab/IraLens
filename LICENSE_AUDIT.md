# Half IraLens — Dependency License Audit

**Date:** 2026-10-07 · **Audited release:** half-iralens 0.1.0
**Method:** Every license below was read from a primary source in this
workspace — the installed distribution metadata (`importlib.metadata`), the
license files of the actual cloned upstream repositories, and the shipped
file tree. No license was assumed from a parent project or from memory.

---

## 1. Half IraLens itself

| Item | Finding |
|---|---|
| Declared license | Apache-2.0 (`pyproject.toml`) |
| LICENSE file | **Was missing — added in this audit** (standard Apache-2.0 text + copyright line, 11,369 bytes) |
| NOTICE file | Present; **expanded in this audit** (full MIT text of the adapted upstream code, engine terms) |
| Code provenance | Original code except the Agent Reach–derived portions listed in §3.1 |
| Bundled binaries | **None.** `file(1)` over the whole distributable tree: pure text. The browser engine binary is downloaded at runtime from upstream releases into a gitignored cache (`tools/engine/`, `~/.cache/half-iralens/engine`) and is never committed or redistributed. |

## 2. Redistribution model (decides which obligations apply)

The sdist/wheel contains **only first-party code** plus LICENSE/NOTICE
(verified by building both: `dist-info/licenses/LICENSE`,
`dist-info/licenses/NOTICE` in the wheel; `LICENSE`, `NOTICE` in the sdist).
All runtime dependencies are declared in `pyproject.toml` and fetched from
PyPI by the installer — **no third-party source or binary is vendored,
bundled, or mirrored**. Consequently:

- Dependency licenses impose no redistribution obligations on the Half
  IraLens artifacts themselves; they matter for *compatibility* and for
  users assembling a deployment.
- The only obligations that attach to our distribution are the attribution
  duties for **adapted upstream code** (§3.1) — satisfied via NOTICE.

## 3. Incorporated upstream projects

### 3.1 Agent Reach (adapted source code)

| Item | Finding |
|---|---|
| Project | https://github.com/Panniantong/Agent-Reach |
| License | **MIT** — verified from the repo's LICENSE: “Copyright (c) 2025 Agent Eyes” |
| How incorporated | Source code **adapted** into: `halfiralens/security.py` (SSRF URL normalization, credential scrubbing — substantially verbatim), `halfiralens/proc.py` (probe framework), `halfiralens/config.py` (atomic owner-only config), `halfiralens/sources/web.py` (reader fetch + anti-bot detection), plus adapted semantics/knowledge in `sources/base.py`, `v2ex.py`, `youtube.py`, `twitter.py`, `reddit.py`, `_mcporter.py` |
| Redistributed by Half IraLens | **Yes** (as modified source) |
| Obligation | MIT: include the copyright notice and permission notice in copies/substantial portions |
| Status | **Satisfied** — full MIT text now reproduced in `NOTICE`; per-file docstrings record the adaptation |
| Compatibility with Apache-2.0 | **Compatible.** MIT is permissive and one-way compatible: MIT code may be incorporated into an Apache-2.0 work; the MIT attribution survives, no copyleft. Modification is expressly permitted. |

### 3.2 Obscura (browser engine)

| Item | Finding |
|---|---|
| Project | https://github.com/h4ckf0r0day/obscura |
| License | **Apache-2.0** — verified from the repo's LICENSE (standard 11,324-byte text) and `Cargo.toml` (`license = "Apache-2.0"`) |
| How incorporated | **No source code copied.** The engine is consumed as a prebuilt binary (pinned v0.2.4) that `halfiralens install-engine` downloads **directly from the upstream project's official GitHub releases** at runtime |
| Redistributed by Half IraLens | **No** — not vendored, not mirrored, gitignored |
| Upstream NOTICE file | **None exists** (verified) → nothing to propagate under Apache-2.0 §4(d) |
| Obligations on us | None for the binary (we don't distribute it). Attribution retained in NOTICE as good practice. |
| Compatibility | Same license as Half IraLens — zero friction even if a downstream user redistributes the binary under Apache-2.0 terms |
| Note for downstream | Anyone who *does* bundle the engine binary must honor its Apache-2.0 terms and the third-party notices published with the upstream distribution (the Rust binary statically links third-party crates; those obligations sit with the engine's own distribution). |

## 4. Runtime dependencies (declared, pip-installed — not redistributed by us)

Verified via installed metadata; versions as resolved in this environment:

| Package | Version | License | Copyleft? | Notes |
|---|---|---|---|---|
| requests | 2.33.0 | Apache-2.0 | No | |
| feedparser | 6.0.14 | BSD-2-Clause | No | |
| pyyaml | 6.0.3 | MIT | No | |
| yt-dlp | 2026.8.19 | **Unlicense** | No | Public-domain dedication; no attribution required, no restrictions |

## 5. Transitive runtime dependencies (full closure, verified)

| Package | Version | License | Copyleft? | Notes |
|---|---|---|---|---|
| urllib3 | 2.7.0 | MIT | No | via requests |
| idna | 3.18 | BSD-3-Clause | No | via requests |
| charset_normalizer | 3.4.9 | MIT | No | via requests |
| certifi | 2026.7.22 | **MPL-2.0** | **Weak (file-level)** | via requests; see §7 |
| feedparser-sgmllib | 2.1.0 | PSF-2.0 | No | via feedparser; Python Software Foundation license (permissive) |

## 6. Development-only dependencies (never shipped)

`pytest 9.0.3` (MIT) with `pluggy` (MIT), `iniconfig` (MIT), `packaging`
(Apache-2.0 OR BSD-2-Clause), and platform-conditionals (`pygments` BSD-3,
`colorama` BSD-3, `exceptiongroup`/`tomli` MIT — only on older Python/Windows).
Installed under the `dev` extra; not part of any runtime environment or
artifact.

## 7. Flagged-license analysis

- **certifi — MPL-2.0** (the only copyleft in the closure). File-scoped
  copyleft: obligations attach only to modifications of MPL-covered files
  and only when those files are distributed. Half IraLens does not
  distribute, vendor, or modify certifi — pip fetches it from PyPI. **No
  obligation, no incompatibility.** Even in a hypothetical bundled
  deployment, MPL-2.0 combines freely with Apache-2.0 works (no contagion
  to our code). **No action.**
- **yt-dlp — Unlicense.** Public-domain dedication; strictly more permissive
  than any SPDX license; no conditions. **No action.**
- **Copyleft scan of the full runtime closure:** no GPL, no AGPL, no LGPL,
  no SSPL/Elastic/BSL or other source-available licenses, no
  commercial-use restrictions, no custom/unknown licenses. The only
  non-permissive license present is MPL-2.0 (analyzed above).
- **Bundled binaries:** none (§1). **Vendored third-party code:** none
  beyond the MIT-licensed Agent Reach adaptations (§3.1). **Modified
  upstream code:** only those MIT portions, with notices retained.
- The `sources/` clones of the two upstream repositories in this workspace
  are study artifacts for development; they are outside the package
  directory and excluded from all release artifacts.

## 8. Compatibility conclusion

- Half IraLens (Apache-2.0) + adapted MIT code: **compatible** (attribution
  retained in NOTICE).
- Apache-2.0 + every runtime dependency (Apache-2.0, BSD-2, BSD-3, MIT,
  PSF-2.0, Unlicense, MPL-2.0-unbundled): **compatible** — no copyleft
  reaches our code or our distribution.
- Engine binary under Apache-2.0, fetched from upstream, not redistributed:
  **compatible**.

## 9. Actions taken in this audit

1. **Added `LICENSE`** (full Apache-2.0 text) — the declared license had no
   text file; required before any redistribution. *(Fixed.)*
2. **Expanded `NOTICE`** to reproduce the complete MIT license text and
   copyright notice for the adapted Agent Reach code (MIT's inclusion
   requirement), and to state the Obscura engine terms precisely. *(Fixed.)*
3. **Packaging wiring** so the notices actually ship: `license-files` in
   `pyproject.toml` (wheel) and `MANIFEST.in` (sdist); verified by building
   both artifacts and listing contents. *(Fixed.)*

No dependency was added, removed, or replaced — none had an actual
licensing problem (§10 of the audit mandate).

## 10. Verdict

**OPEN-SOURCE READY**

The two genuinely required actions (LICENSE file, complete MIT attribution
in NOTICE) were identified and applied during this audit; shipping of both
notices in the built wheel and sdist was verified. No GPL/AGPL/LGPL,
source-available, commercial-restriction, or bundled-binary issues remain.
