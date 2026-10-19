# Vendored Browser Engine Provenance

## Upstream Source

- **Upstream Repository**: `https://github.com/h4ckf0r0day/obscura`
- **Upstream License**: Apache License 2.0 (`Apache-2.0`)
- **Vendored Base Revision**: `bd9529418b7fa975b50eabeac415cfaaa865a588` (committed 2026-10-16)
- **Base Release Tag**: `v0.2.5` — the tag `v0.2.5` points at exactly this
  commit (published 2026-10-17). Earlier IraLens releases labelled the tree
  "post-`v0.2.4`" because it was imported before the tag existed; IraLens
  0.3.5 corrects the label. The vendored Rust sources are byte-identical to
  the upstream `v0.2.5` tree except for the three documented `Cargo.toml`
  modifications below; `bootstrap.js` additionally carries the four local
  patches enumerated under "Modifications to Vendored Files".
- **Internal Location in IraLens**: `iralens/_internal/browser_engine/vendor/obscura/`

## Imported Upstream Components

The following crates and modules from Obscura are vendored into `iralens/_internal/browser_engine/vendor/obscura/`:

| Component | Path | Purpose | Size |
|---|---|---|---|
| `obscura-ssrf` | `crates/obscura-ssrf/` | Shared IPv4/IPv6 SSRF deny-set (loopback, RFC1918, link-local, CGNAT, NAT64, Teredo, 6to4, multicast, benchmarking) and private-network opt-in | ~12 KB |
| `obscura-dom` | `crates/obscura-dom/` | HTML5 DOM tree representation (`tree.rs`), CSS selector engine (`selector.rs`), HTML serialization (`serialize.rs`), and `html5ever` sink (`tree_sink.rs`) | ~232 KB |
| `obscura-net` | `crates/obscura-net/` | HTTP client transport (`client.rs`), RFC 6265 cookie jar (`cookies.rs`), WHATWG encoding labels (`encoding.rs`), `robots.txt` cache (`robots.rs`), and tracker blocklist (`blocklist.rs`, `pgl_domains.txt`) | ~352 KB |
| `obscura-js` | `crates/obscura-js/` | V8 JavaScript runtime (`runtime.rs`), native DOM/WebAPI host ops (`ops.rs`), WebIDL DOM/Window/Event/Fetch/Storage/Crypto bootstrap (`js/bootstrap.js`), IndexedDB implementation (`js/indexed_db.js`, 790 lines), and HTML-to-Markdown converter (`src/markdown.rs`) | ~2.6 MB |
| `obscura-browser` | `crates/obscura-browser/` | High-level browser `Page` (`page.rs`), `BrowserContext` (`context.rs`), accessibility tree builder (`accessibility.rs`), PDF text extraction (`pdf.rs`), and browser profiles (`profiles.rs`) | ~608 KB |
| `obscura-mcp` | `crates/obscura-mcp/` | JSON-RPC 2.0 stdio/HTTP tool server (`src/lib.rs`, `src/http.rs`) implementing 30+ `browser_*` automation tools | ~180 KB |
| `obscura-cli` | `crates/obscura-cli/` | CLI entrypoint (`src/main.rs`) supporting `mcp`, `fetch`, and `scrape` subcommands | ~152 KB |
| `obscura` | `crates/obscura/` | Embeddable Rust library facade (`Browser`, `Page`, `Cookie`, `BrowserConfig`) | ~44 KB |

## Excluded Upstream Components (and Why)

To keep the IraLens repository and distribution lean and avoid unnecessary third-party bloat, the following optional upstream directories are excluded:

1. `crates/obscura-render/` (~18 MB, including ~17.5 MB of embedded `.ttf` fonts in `assets/`) — Optional layout/rasterization layer (`paint` feature) used for pixel rasterization; off by default in Obscura's scraping/automation builds.
2. `vendor/cosmic-text/` (~2.0 MB) and `vendor/taffy/` (~1.1 MB) — Patched third-party layout crates used exclusively by `obscura-render`.
3. `crates/obscura-cdp/` (~1.2 MB) — Chrome DevTools Protocol WebSocket server (`obscura serve`); IraLens communicates with the engine over MCP stdio and internal Python/V8 bindings, never over remote CDP WebSockets.
4. `render-repros/`, `assets/sponsors/`, `.github/` — Upstream visual regression fixtures, sponsor logos, and CI workflows.

## Modifications to Vendored Files

Per Apache License 2.0 Section 4(b), all modified files carry prominent notices:

1. `vendor/obscura/Cargo.toml`: Scoped `workspace.members` and `workspace.dependencies` to the 8 imported core crates (`obscura-dom`, `obscura-ssrf`, `obscura-net`, `obscura-js`, `obscura-browser`, `obscura-mcp`, `obscura-cli`, `obscura`), removing `obscura-render`, `obscura-cdp`, `vendor/cosmic-text`, and `vendor/taffy`. Workspace package version pinned to `0.2.5` (upstream's workspace root declares `0.1.0`; the crate versions follow the release).
2. `vendor/obscura/crates/obscura-js/Cargo.toml`: Decoupled optional `obscura-render` dependency so the workspace builds cleanly with default features (`render` off).
3. `vendor/obscura/crates/obscura-cli/Cargo.toml`: Scoped dependencies to the vendored workspace crates; dropped the `obscura-worker` bin target and CDP-only dev-dependencies that belong to the excluded crates.

All core upstream Rust source files (`markdown.rs`, `ops.rs`, `runtime.rs`, `tree.rs`, `selector.rs`, `cookies.rs`, `obscura-ssrf/src/lib.rs`, `obscura-mcp/src/lib.rs`, `page.rs`, `accessibility.rs`, `context.rs`) are byte-identical to upstream tag `v0.2.5` (verified by tree comparison on 2026-10-19 for the IraLens 0.3.5 release).

### Local patches to `crates/obscura-js/js/bootstrap.js`

`bootstrap.js` (executed by the bundled native-V8 tier *and* embedded into the
Rust binary at build time via `obscura-js/build.rs`) carries four local
patches. Two are IraLens-authored; two are upstream post-`v0.2.5` fixes
adopted verbatim ahead of the next upstream release:

1. **IraLens patch — defensive attribute accessors on the base `Node` stub**
   (near line 2799). `getAttribute()`/`hasAttribute()`/`getAttributeNames()`
   on a non-Element node return `null`/`false`/`[]` instead of throwing a
   `TypeError` that would abort the whole render.
2. **IraLens patch — `innerText` skips non-rendered subtrees** (near line
   3984). `SCRIPT`, `STYLE`, `NOSCRIPT`, `TEMPLATE`, `HEAD`, `TITLE`, `META`,
   `LINK`, `BASE`, `IFRAME`, `OBJECT`, `EMBED`, `CANVAS`, `SVG`, `MAP`,
   `AREA` source text is not collected as visible page text.
3. **Adopted upstream fix — same-document fragment navigation** (upstream
   `main` commits `8a7aa8c3` "fix(js): navigate to fragments in the same
   document" and `fb23f18b` "fix(js): preserve fragment history state",
   post-`v0.2.5`, adopted verbatim 2026-10-19). `location.hash`/`assign()`/
   `replace()` and `<a href="#…">` activation whose target differs from the
   current URL only by fragment now stay in-document: they push/replace a
   session-history entry and queue a `hashchange` event instead of calling
   `op_navigate` (which reloads the document). Location component setters
   (`host`, `hostname`, `pathname`, `search`, `hash`, `port`) were added per
   the HTML spec. Hash-routed pages no longer trigger a spurious refetch.
   One IraLens host-side adaptation accompanies this patch *outside* the
   vendored tree: upstream queues `hashchange` through Obscura's async
   runtime, which the bundled Python/native-V8 host intentionally reports as
   unavailable, so `host_mcp.js` wraps the helper after each document init
   and delivers the same event through IraLens's own timer queue. History
   state and URL semantics remain entirely upstream's.
4. **Adopted upstream fix — ordered-list element identity** (upstream `main`
   commit `2f302d9b` "fix(js): reflect ordered-list and list-item
   attributes", post-`v0.2.5`, adopted verbatim 2026-10-19). `HTMLOListElement`
   and `HTMLLIElement` are real interface classes instead of aliases of
   `Element`, reflecting `reversed`/`start`/`type` and `value`.

### Upstream post-`v0.2.5` changes reviewed and *not* adopted

- **WebCrypto AES-KW / native JWK serialization** (upstream `main` commits
  `d57489cd`, `d853a731`, `77181049`): requires three new native ops
  (`op_subtle_jwk_json`, `op_subtle_aes_kw`, `op_subtle_jwk_parse`) that live
  in `obscura-js/src/ops.rs`/`runtime.rs` on upstream `main`. The vendored
  Rust tree is pinned to the `v0.2.5` release and the IraLens host bridge
  (`host_ops.js`) does not implement those ops, so adopting the JS half alone
  would break `wrapKey`/`unwrapKey` in both tiers. Rejected for 0.3.5;
  adopt together with the Rust tree at the next upstream release.
- **`obscura-mcp` "drive page tasks during function waits"** (`b1acb89e`,
  Rust-only): affects the compiled-binary tier's MCP server, which IraLens
  drives through its own Python MCP layer; no benefit without a Rust-tree
  bump. Deferred with the next upstream release sync.
- Upstream `main` README/AGENTS.md/sponsor-asset commits: not code.

## Execution Architecture in IraLens

`iralens/_internal/browser_engine/` provides two seamlessly integrated execution tiers backed by the vendored Obscura source:

1. **Compiled Rust Binary Tier**: When built with a Rust toolchain (`cargo build --release --manifest-path iralens/_internal/browser_engine/vendor/obscura/Cargo.toml -p obscura-cli --bin obscura` or `iralens install-engine --from-source`), IraLens runs the compiled Rust binary directly.
2. **Bundled Native V8 + Obscura Runtime Tier (`iralens/_internal/browser_engine/bin/iralens-browser-engine`)**: Shipped out-of-the-box with `pip install .` so users never need `cargo`/`rustc` or runtime binary downloads from GitHub Releases. This tier executes the vendored `vendor/obscura/crates/obscura-js/js/bootstrap.js`, `vendor/obscura/crates/obscura-js/js/indexed_db.js`, and `vendor/obscura/crates/obscura-js/src/markdown.rs` (`HTML_TO_MARKDOWN_JS`) inside a native V8 isolate (`py_mini_racer` / `libmini_racer` V8 or Node V8 VM) wired to a faithful implementation of Obscura's `Deno.core.ops` bridge (`host_ops.js`), `obscura-ssrf` (`ssrf.py`), `obscura-net` RFC 6265 `CookieJar` (`cookies.py`), `obscura-browser` accessibility tree, and `obscura-mcp` JSON-RPC 2.0 stdio protocol (`mcp_server.py`).

## Procedure for Applying Upstream Security Updates

To pull future upstream security fixes from `https://github.com/h4ckf0r0day/obscura`:

```bash
# 1. Clone upstream Obscura to a temporary directory and inspect commits
git clone https://github.com/h4ckf0r0day/obscura /tmp/obscura-upstream
git -C /tmp/obscura-upstream log --oneline v0.2.5..HEAD

# 2. Sync the 8 vendored crates into iralens/_internal/browser_engine/vendor/obscura/crates/
for crate in obscura-ssrf obscura-dom obscura-net obscura-js obscura-browser obscura-mcp obscura-cli obscura; do
    rsync -av --delete \
      --exclude='tests/' \
      "/tmp/obscura-upstream/crates/$crate/src/" \
      "iralens/_internal/browser_engine/vendor/obscura/crates/$crate/src/"
done
cp /tmp/obscura-upstream/crates/obscura-js/js/*.js \
   iralens/_internal/browser_engine/vendor/obscura/crates/obscura-js/js/

# 3. Re-apply the four local bootstrap.js patches listed above (steps 1-2 of
#    the patch list are IraLens-authored; steps 3-4 will already be in the
#    upstream file once the next release contains them -- drop them from the
#    local patch set at that point).
# 4. Record the new commit hash in UPSTREAM_PROVENANCE.md and runtime.py
#    (UPSTREAM_REVISION / UPSTREAM_BASE_TAG / UPSTREAM_BASE_TAG_COMMIT)
# 5. Run the full verification suite
pytest -q
ruff check --select E9,F,B .
mypy iralens
```
