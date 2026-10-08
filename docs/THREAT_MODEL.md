# Threat model: Internet content in an AI agent

Scope: the Full IraLens search, reading, and research paths. The controlling AI
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
4. **Credential isolation.** Caching never stores authenticated content. Browser
   reads are never cached. Search and static reads send no cookies.
5. **Third-party reader.** The static page reader (`r.jina.ai`) receives the URL
   and returns markdown. Page URLs leave the machine. This is existing behavior,
   recorded in DECISIONS.md. Use `mode=browser` to avoid it.

## Known gaps

- Injection patterns are a heuristic. They will miss paraphrases and other
  languages. Flags are advisory, not a security boundary.
- Authority scoring is a domain-suffix table. It is not a trust decision.
- Hostile pages cannot be fully sandboxed by this layer. The browser engine runs
  the page's JavaScript.
- DNS rebinding and redirects to private IPs after the initial URL check are not
  covered by `normalize_public_http_url`, which checks the literal URL only.
