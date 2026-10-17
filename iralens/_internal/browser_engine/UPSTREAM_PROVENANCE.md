# Vendored Browser Engine Provenance

## Upstream Source

- **Upstream Repository**: `https://github.com/h4ckf0r0day/obscura`
- **Upstream License**: Apache License 2.0 (`Apache-2.0`)
- **Vendored Commit Revision**: `bd9529418b7fa975b50eabeac415cfaaa865a588` (2026-10-16)
- **Base Release Tag**: `v0.2.4` (`1fccab2c1ecd0f7eadc16d7a520ba227f9e7f782`, 2026-10-04)
- **Internal Location in IraLens**: `iralens/_internal/browser_engine/vendor/obscura/`

## Imported Upstream Components

The following crates and modules from Obscura are vendored into `iralens/_internal/browser_engine/vendor/obscura/`:

| Component | Path | Purpose | Size |
|---|---|---|---|
| `obscura-ssrf` | `crates/obscura-ssrf/` | Shared IPv4/IPv6 SSRF deny-set (loopback, RFC1918, link-local, CGNAT, NAT64, Teredo, 6to4, multicast, benchmarking) and private-network opt-in | ~12 KB |
| `obscura-dom` | `crates/obscura-dom/` | HTML5 DOM tree representation (`tree.rs`), CSS selector engine (`selector.rs`), HTML serialization (`serialize.rs`), and `html5ever` sink (`tree_sink.rs`) | ~232 KB |
| `obscura-net` | `crates/obscura-net/` | HTTP client transport (`client.rs`), RFC 6265 cookie jar (`cookies.rs`), WHATWG encoding labels (`encoding.rs`), `robots.txt` cache (`robots.rs`), and tracker blocklist (`blocklist.rs`, `pgl_domains.txt`) | ~352 KB |
| `obscura-js` | `crates/obscura-js/` | V8 JavaScript runtime (`runtime.rs`), native DOM/WebAPI host ops (`ops.rs`), WebIDL DOM/Window/Event/Fetch/Storage/Crypto bootstrap (`js/bootstrap.js`, 18,942 lines), IndexedDB implementation (`js/indexed_db.js`, 790 lines), and HTML-to-Markdown converter (`src/markdown.rs`) | ~2.5 MB |
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

1. `vendor/obscura/Cargo.toml`: Scoped `workspace.members` and `workspace.dependencies` to the 8 imported core crates (`obscura-dom`, `obscura-ssrf`, `obscura-net`, `obscura-js`, `obscura-browser`, `obscura-mcp`, `obscura-cli`, `obscura`), removing `obscura-render`, `obscura-cdp`, `vendor/cosmic-text`, and `vendor/taffy`.
2. `vendor/obscura/crates/obscura-js/Cargo.toml`: Decoupled optional `obscura-render` dependency so the workspace builds cleanly with default features (`render` off).
3. `vendor/obscura/crates/obscura-cli/Cargo.toml`: Scoped dependencies to the vendored workspace crates.

All core upstream source files (`bootstrap.js`, `indexed_db.js`, `markdown.rs`, `ops.rs`, `runtime.rs`, `tree.rs`, `selector.rs`, `cookies.rs`, `obscura-ssrf/src/lib.rs`, `obscura-mcp/src/lib.rs`, `page.rs`, `accessibility.rs`, `context.rs`) are preserved unmodified from upstream commit `bd9529418b7fa975b50eabeac415cfaaa865a588`.

## Execution Architecture in IraLens

`iralens/_internal/browser_engine/` provides two seamlessly integrated execution tiers backed by the vendored Obscura source:

1. **Compiled Rust Binary Tier**: When built with a Rust toolchain (`cargo build --release --manifest-path iralens/_internal/browser_engine/vendor/obscura/Cargo.toml -p obscura-cli --bin obscura` or `iralens install-engine --from-source`), IraLens runs the compiled Rust binary directly.
2. **Bundled Native V8 + Obscura Runtime Tier (`iralens/_internal/browser_engine/bin/iralens-browser-engine`)**: Shipped out-of-the-box with `pip install .` so users never need `cargo`/`rustc` or runtime binary downloads from GitHub Releases. This tier executes the vendored `vendor/obscura/crates/obscura-js/js/bootstrap.js`, `vendor/obscura/crates/obscura-js/js/indexed_db.js`, and `vendor/obscura/crates/obscura-js/src/markdown.rs` (`HTML_TO_MARKDOWN_JS`) inside a native V8 isolate (`py_mini_racer` / `libmini_racer` V8 or Node V8 VM) wired to a faithful implementation of Obscura's `Deno.core.ops` bridge (`host_ops.js`), `obscura-ssrf` (`ssrf.py`), `obscura-net` RFC 6265 `CookieJar` (`cookies.py`), `obscura-browser` accessibility tree, and `obscura-mcp` JSON-RPC 2.0 stdio protocol (`mcp_server.py`).

## Procedure for Applying Upstream Security Updates

To pull future upstream security fixes from `https://github.com/h4ckf0r0day/obscura`:

```bash
# 1. Clone upstream Obscura to a temporary directory and inspect commits
git clone https://github.com/h4ckf0r0day/obscura /tmp/obscura-upstream
git -C /tmp/obscura-upstream log --oneline bd9529418b7fa975b50eabeac415cfaaa865a588..HEAD

# 2. Sync the 8 vendored crates into iralens/_internal/browser_engine/vendor/obscura/crates/
for crate in obscura-ssrf obscura-dom obscura-net obscura-js obscura-browser obscura-mcp obscura-cli obscura; do
    rsync -av --delete \
      --exclude='tests/' \
      "/tmp/obscura-upstream/crates/$crate/src/" \
      "iralens/_internal/browser_engine/vendor/obscura/crates/$crate/src/"
done
cp /tmp/obscura-upstream/crates/obscura-js/js/*.js \
   iralens/_internal/browser_engine/vendor/obscura/crates/obscura-js/js/

# 3. Record the new commit hash in UPSTREAM_PROVENANCE.md and runtime.py (UPSTREAM_REVISION)
# 4. Run the full verification suite
pytest -q
ruff check --select E9,F,B .
mypy iralens
```
