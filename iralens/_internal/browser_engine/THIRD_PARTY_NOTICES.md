# Third-Party License Notices for `iralens._internal.browser_engine`

## 1. Obscura Headless Browser Engine (Vendored Source)

- **Project**: Obscura (`https://github.com/h4ckf0r0day/obscura`)
- **Copyright**: Copyright (c) 2026 Obscura Contributors (`h4ckf0r0day`)
- **License**: Apache License, Version 2.0 (`Apache-2.0`)
- **Revision**: `bd9529418b7fa975b50eabeac415cfaaa865a588` (post-`v0.2.4`)
- **License Text**: See `iralens/_internal/browser_engine/vendor/obscura/LICENSE` and repository root `LICENSE`.

## 2. Embedded Domain Blocklist Data (`pgl_domains.txt`)

- **File**: `iralens/_internal/browser_engine/vendor/obscura/crates/obscura-net/src/pgl_domains.txt`
- **Source**: Peter Lowe's Ad and Tracking Server List (`https://pgl.yoyo.org/adservers/`)
- **Usage**: Used only when stealth tracker-blocking is enabled.

## 3. Native V8 Runtime Binding (`mini-racer` / `py_mini_racer`)

- **Project**: PyMiniRacer (`https://github.com/bpcreech/PyMiniRacer` / Sqreen)
- **Copyright**: Copyright (c) 2018, Sqreen. All rights reserved.
- **License**: ISC License (permissive, compatible with Apache-2.0)
- **Embedded Engine**: Google V8 JavaScript Engine (BSD-3-Clause License, Copyright The V8 Project Authors)

```text
Copyright (c) 2018, Sqreen
All rights reserved.

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

## 4. Upstream Rust Crate Dependencies (When Compiling from Rust Source via Cargo)

Per `iralens/_internal/browser_engine/vendor/obscura/deny.toml`, all upstream Rust dependencies are restricted to permissive licenses (`MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `Unicode-3.0`, `Unicode-DFS-2016`, `Zlib`, `CC0-1.0`, `MPL-2.0`):

- `deno_core` / `v8` (MIT) — Copyright Deno Land Inc. / The V8 Project Authors
- `html5ever` / `markup5ever` / `selectors` / `cssparser` / `servo_arc` (MIT OR Apache-2.0 / MPL-2.0) — Copyright The Servo Project Developers
- `tokio` (MIT) — Copyright Tokio Contributors
- `reqwest` (MIT OR Apache-2.0) — Copyright Sean McArthur
- `serde` / `serde_json` (MIT OR Apache-2.0) — Copyright Erick Tryzelaar, David Tolnay
- `url` (MIT OR Apache-2.0) — Copyright The rust-url developers
- `encoding_rs` (Apache-2.0 OR MIT) — Copyright Mozilla Foundation
- `psl` (MIT OR Apache-2.0) — Copyright Rushmore Mushambi
