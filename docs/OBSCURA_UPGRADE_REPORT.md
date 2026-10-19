# Obscura upgrade report — IraLens 0.3.5

**Status:** Implemented and verified offline. Native Rust execution and live
website behaviour are not verified by this report.

## Decision

IraLens 0.3.5 keeps the vendored Obscura tree at upstream revision
`bd9529418b7fa975b50eabeac415cfaaa865a588` and corrects its release label to
**v0.2.5**. That revision is not merely close to v0.2.5: the upstream
`v0.2.5` tag points at exactly that commit. Earlier IraLens releases called
it “post-v0.2.4” because the tree was imported before the tag existed.

The newest suitable upstream revision at the time of the audit was the
v0.2.5 release (published 2026-10-17). Upstream `main` was 14 commits ahead,
but it had no newer release tag. IraLens therefore did not replace the
vendored tree with an unreleased snapshot. Instead, it reviewed those 14
commits and adopted only the self-contained JavaScript fixes that work in
both IraLens execution tiers.

## What was verified against upstream

The vendored Rust sources for all eight imported crates were compared
byte-for-byte with the upstream v0.2.5 source tree. They match except for
the three IraLens `Cargo.toml` modifications documented in
`iralens/_internal/browser_engine/UPSTREAM_PROVENANCE.md`:

1. the workspace is scoped to the eight crates IraLens ships;
2. `obscura-js` is decoupled from the excluded render crate;
3. `obscura-cli` is scoped to the vendored workspace and drops CDP-only
   targets and dev-dependencies.

The workspace package version is now pinned to `0.2.5`, matching the release.
`ENGINE_VERSION` in the prebuilt-binary installer also moved from `0.2.4`
to `0.2.5`; the upstream v0.2.5 release publishes the same platform asset
names IraLens already constructs. The upstream release publishes no checksum
asset, so binary integrity still relies on HTTPS plus IraLens's startup
probe unless the operator supplies a checksum.

## Adopted post-v0.2.5 fixes

Two upstream fixes from `main` were adopted verbatim into the vendored
`bootstrap.js`, ahead of the next upstream release:

- **Same-document fragment navigation** (`8a7aa8c3`, `fb23f18b`).
  `location.hash`, `location.assign()`/`replace()` and `<a href="#…">`
  activation now stay in-document when only the fragment changes, preserve
  JS state, push/replace history, and queue `hashchange` instead of
  refetching the page. Cross-path links still perform real navigations.
- **Ordered-list element identity** (`2f302d9b`). `HTMLOListElement` and
  `HTMLLIElement` are real interfaces reflecting `start`, `reversed` and
  `value`, instead of aliases of `Element`.

One IraLens host-side adaptation accompanies the first fix, outside the
vendored tree. Upstream queues `hashchange` through Obscura's async runtime;
the bundled Python/native-V8 host intentionally reports that runtime as
unavailable, so the queued event would never fire in that tier.
`iralens/_internal/browser_engine/host_mcp.js` now wraps the upstream helper
after each document init and delivers the same event through IraLens's own
timer queue. History and URL semantics remain upstream's.

Both fixes are covered by
`tests/test_browser_engine_integration.py::test_adopted_upstream_fixes_fragment_navigation_and_list_identity`,
which runs the real V8 tier against a local HTTP fixture and proves that
fragment navigations do not reach the server.

## Reviewed and rejected for 0.3.5

- **WebCrypto AES-KW and native JWK serialization** (`d57489cd`, `d853a731`,
  `77181049`). These require three new native ops
  (`op_subtle_jwk_json`, `op_subtle_aes_kw`, `op_subtle_jwk_parse`) in the
  Rust `obscura-js` crate. The vendored Rust tree remains at the v0.2.5
  release and the IraLens host bridge does not implement those ops, so
  adopting only the JavaScript half would break `wrapKey`/`unwrapKey` in
  both tiers. This should be adopted together with the Rust tree at the next
  upstream release.
- **MCP page-task driving during function waits** (`b1acb89e`). Rust-only;
  IraLens drives the engine through its own Python MCP layer. Deferred with
  the next upstream release sync.
- README, contributor-guide and sponsor-asset commits. Not code.

## IraLens-specific patches retained

Two IraLens-authored `bootstrap.js` patches predate this release and are
retained:

- defensive attribute accessors on the base `Node` stub, so a script calling
  `getAttribute()` on a non-Element gets `null` instead of aborting the
  render with a `TypeError`;
- `innerText` collection skips non-rendered subtrees (`SCRIPT`, `STYLE`,
  `NOSCRIPT`, `TEMPLATE`, `HEAD`, and similar), so inline script and style
  source is not extracted as page content.

The full patch ledger, including exact locations, is in
`iralens/_internal/browser_engine/UPSTREAM_PROVENANCE.md`.

## Capability boundaries

- Obscura remains the primary browser-rendering backend in IraLens's real
  retrieval pipeline: `sources/web.py` tries direct HTTP and the static
  reader first for speed, then renders through the engine in `browser` mode
  or automatically when a JS shell is detected. Results are mapped into the
  existing `Artifact` model with final URL, status and challenge metadata.
- No Lightpanda or other browser engine was introduced.
- The excluded upstream components remain excluded: `obscura-render`
  (pixel rasterization and its ~18 MB of fonts), `obscura-cdp` (remote CDP
  WebSocket server), and their vendored layout crates. IraLens communicates
  with the engine over MCP stdio and its internal Python/V8 bindings, never
  over remote CDP.
- The vendored source does not by itself make any feature available: IraLens
  reports capabilities from the tier that actually runs, and a requested but
  unavailable optional feature is reported rather than silently claimed.

## Build and packaging measurements

Measured in the release environment (Python 3.11, offline):

- `python -m build` produces `iralens-0.3.5-py3-none-any.whl` (1.3 MB) and
  `iralens-0.3.5.tar.gz` (1.5 MB); `twine check` passes for both.
- The vendored Obscura source and the Apache-2.0 notices are included in the
  package data, as before.
- The Rust binary tier was **not** compiled in this environment (no Rust
  toolchain was installed); the from-source build path is unchanged and
  remains covered by the documented `cargo build` command in the provenance
  file. This is a limitation of the release environment, not a code change.

## Licence

Obscura remains Apache-2.0. No new vendored dependency was introduced by
this upgrade; the only new optional Python dependency in 0.3.5 is
`curl_cffi` (MIT) behind the `iralens[stealth]` extra, used solely
for TLS/HTTP2 fingerprint impersonation and reported as unavailable when it
is not installed.
