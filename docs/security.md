# Security

The security model and guard layers for the IraLens search, reading, and
research paths. The controlling AI is trusted to decide what to do. Web
content, search snippets, and page text are **untrusted data**: they can try
to steer the agent (prompt injection), leak data, or point the agent at
internal targets.

## Controls, by layer

1. **Untrusted envelope.** Every artifact has `untrusted: true`.
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
4. **Fetch-time guard (`security.safe_urlopen`).** The static reader's
   HTTP fetch resolves the hostname, refuses answers that are (even partly)
   private/loopback/link-local, and connects directly to the validated address
   (DNS pinning), closing the DNS-rebinding window between check and connect.
   Every redirect hop is re-validated against the public-URL policy; https→http
   downgrades and chains longer than `MAX_REDIRECTS` (5) are refused.
5. **Credential isolation.** Caching never stores authenticated content. Browser
   reads are never cached. Search and static reads send no cookies. Proxy
   credentials are never passed on a command line: `/proc/<pid>/cmdline` is
   world-readable, so the proxy reaches the engine through its environment
   (`IRALENS_ENGINE_PROXY`), which is owner-readable only. Error messages and
   reports are scrubbed of URL credentials (`security.scrub_url_credentials`)
   before they leave the process.
5b. **Browser navigation guard.** The browser tier applies the same SSRF policy
   as the fetch path: the URL is validated before navigation, **every redirect
   hop is re-validated**, and cross-origin hops strip `Authorization`,
   `Proxy-Authorization`, and `Cookie`. Subresources are validated too — a
   hostile page naming an internal host in a `<script src>` is refused and
   recorded as `blocked_subresource`; the rest of the page still renders.
   Subresource count and byte budgets are enforced, and the Chrome DevTools
   Protocol server is deliberately not vendored, so there is no WebSocket
   control port: the engine speaks MCP over stdio only.
5c. **Verification pages stop the read.** A CAPTCHA, bot-check interstitial,
   login wall, or access-denied page is detected and raised as
   `verification_required` rather than returned as content. It is never retried
   through another transport, and only the page's marker phrase and provider
   name are retained — never page content.
6. **Third-party reader (opt-in).** Reads are local-first: direct fetch
   plus local extraction, no third party. Only if you opt in
   (`IRALENS_READ_REMOTE_READER_ENABLED=1`) does the remote reader
   (`r.jina.ai`) receive the URL and return markdown; page URLs then leave the
   machine. Use the default (off) or `mode=browser` to avoid it.
7. **Paid backup credentials.** Optional paid-provider keys are sent in
   HTTPS headers and are excluded from health state and result reports. These
   requests reject redirects that change origin, so `urllib` cannot forward the
   credential header to a redirect destination. The optional Google CSE
   reference benchmark must place its key in a provider-required query
   parameter; it uses the same-origin redirect guard and must not run where
   request URLs are retained by untrusted logs/proxies.

## Known gaps

- Injection patterns are a heuristic. They will miss paraphrases and other
  languages. Flags are advisory, not a security boundary.
- Authority scoring is a domain-suffix table. It is not a trust decision.
- A fetched page cannot be fully contained by this layer. The browser engine
  runs the page's JavaScript by design.
- The fetch-time guard covers the static-reader path **and RSS/Atom feeds**:
  both use `security.safe_urlopen`, which pins the resolved address and
  re-validates every redirect hop. One specialized source still issues its own
  HTTP calls with `requests` (the `transcribe` audio download and its Whisper
  POST); it validates the literal URL with `normalize_public_http_url` but does
  not pin resolved addresses. The GitHub API targets are constants.
- The browser-engine release publishes no checksum; unless the operator supplies
  one (`IRALENS_ENGINE_SHA256` or `install-engine --checksum`), install integrity
  rests on HTTPS plus the startup probe. The install never claims a verified
  checksum it did not verify.
- Soft-404 detection is a heuristic (short body + not-found markers) and can
  miss novel 404 page designs.
- Challenge/interstitial detection is a heuristic. It is deliberately
  conservative to avoid failing legitimate pages: widget identifiers are
  decisive, but interstitial *phrasing* requires corroboration. Novel designs
  and non-English challenges will be missed. A missed challenge looks like a
  successful read.
- Proxy credentials remain readable by the same user through the child
  process's environment. Eliminating that needs an out-of-band channel
  (a pipe or inherited file descriptor) and is not implemented.
- Stealth, where an engine provides it, is a browser-identity consistency
  measure. It is not a security control, does not solve CAPTCHAs, and does not
  guarantee success against interactive challenges. It is unreachable in every
  shipped configuration of this distribution (see `KNOWN_LIMITATIONS.md`).
