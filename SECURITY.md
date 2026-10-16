# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security vulnerability. Report it
privately through GitHub's private vulnerability reporting on this repository
(**Security → Advisories → Report a vulnerability**), or by contacting the
repository owner through their GitHub profile.

Please include:

- the affected version or commit,
- steps to reproduce, and any proof-of-concept input,
- the impact you believe is possible.

You will receive an acknowledgement as soon as the report is read. This is a
small open-source project without a paid security team; please allow
reasonable time for a fix before public disclosure.

## Scope and security boundaries

The security boundaries IraLens implements are documented in
[`docs/security.md`](docs/security.md). In short:

- every URL passes an SSRF guard (private/internal hosts, metadata endpoints,
  credentials-in-URL, and non-HTTP(S) schemes are rejected), with a second
  guard inside the optional browser engine;
- fetches are redirect-validated and DNS-pinned;
- Internet content is treated as untrusted data, never as instructions;
- credentials live in an owner-only config file, are injected into child
  processes only, and are scrubbed from error messages.

Known limitations of these controls are listed honestly in
[`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md). Security-relevant gaps that
are already documented there are not vulnerabilities; bypasses of the guards
above are.

## Supported versions

Security fixes are applied to the latest release on `main`. Older releases
are not maintained.
