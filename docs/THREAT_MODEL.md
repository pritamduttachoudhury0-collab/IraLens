# Threat model: Internet content in an AI agent

Scope: the IraLens search, reading, and research paths. The controlling AI
is trusted to decide what to do. Web content, search snippets, and page text are
**untrusted data**. They can try to steer the agent (prompt injection), leak
data, or point the agent at internal targets.

## Controls, by layer

1. **Untrusted envelope (existing).** Every artifact has `untrusted: true`.
   MCP tags Internet-sourced tool results as untrusted (`_UNTRUSTED_TOOLS`).
   Research output is also tagged.
2. **Content guard (`content_guard.py`).** Flags such as
   `prompt_injection:instruction_override` and `invisible_characters` are attached
   to each search hit, each page read, and each research claim. Flagged text is
   not altered, except that invisible characters are stripped from search titles
   and snippets. The guard flags risk. It does not decide.
3. **Public-URL guard (`security.normalize_public_http_url`).** Search hits that
   point at localhost, private IPs, credentials-in-URL, or non-http(s) schemes are
   dropped and counted (`filters.report.non_public_dropped`). A hostile results
   page cannot route the agent to internal services through a "result".
3b. **Fetch-time guard (`security.safe_urlopen`, D-072).** The static reader's
   HTTP fetch resolves the hostname, refuses answers that are (even partly)
   private/loopback/link-local, and connects directly to the validated address
   (DNS pinning), closing the DNS-rebinding window between check and connect.
   Every redirect hop is re-validated against the public-URL policy; https→http
   downgrades and chains longer than `MAX_REDIRECTS` (5) are refused.
4. **Credential isolation.** Caching never stores authenticated content. Browser
   reads are never cached. Search and static reads send no cookies.
5. **Third-party reader (opt-in, D-077).** Reads are local-first: direct fetch
   plus local extraction, no third party. Only if you opt in
   (`IRALENS_READ_REMOTE_READER_ENABLED=1`) does the remote reader
   (`r.jina.ai`) receive the URL and return markdown; page URLs then leave the
   machine. Use the default (off) or `mode=browser` to avoid it.

## Known gaps

- Injection patterns are a heuristic. They will miss paraphrases and other
  languages. Flags are advisory, not a security boundary.
- Authority scoring is a domain-suffix table. It is not a trust decision.
- Hostile pages cannot be fully sandboxed by this layer. The browser engine runs
  the page's JavaScript.
- The fetch-time guard covers the static-reader path (D-072). Specialized sources
  that issue their own HTTP calls (RSS feeds via `requests`, the GitHub API
  constant) validate the literal URL but do not pin resolved addresses; the
  GitHub/API targets are constants, and feed URLs are operator-supplied.
- The browser-engine release publishes no checksum; unless the operator supplies
  one (`IRALENS_ENGINE_SHA256` or `install-engine --checksum`), install integrity
  rests on HTTPS plus the startup probe. The install never claims a verified
  checksum it did not verify.
- Soft-404 detection is a heuristic (short body + not-found markers) and can
  miss novel 404 page designs.
