# -*- coding: utf-8 -*-
"""Content guard: sanitization and prompt-injection flagging for web content.

Layer 2 of the security model (see docs/security.md). It never decides
what the agent does. It removes invisible/control characters, bounds length,
and attaches *flags* to the affected item so the consumer can see the risk.
Flagged content is still data; the untrusted envelope always applies.
"""

from __future__ import annotations

import re
from typing import List, Tuple

FLAG_INVISIBLE = "invisible_characters"

#: (flag name, pattern). Patterns are case-insensitive. Conservative by design:
#: they target instruction-like phrasing aimed at an agent, not ordinary prose.
INJECTION_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("instruction_override",
         r"ignore (?:all |any |the )?(?:previous|prior|above) (?:instructions|prompts|messages|rules)"),
        ("role_hijack",
         r"\byou are now (?:a |an |the )?(?:system|developer|admin|unrestricted)|\bact as (?:the )?(?:system|developer)\b"),
        ("system_prompt_probe",
         r"(?:reveal|print|show|leak) (?:your|the) (?:system )?(?:prompt|instructions)"),
        ("tool_directive",
         r"(?:call|invoke|execute|run) (?:the )?(?:tool|function|shell command|bash)\b"),
        ("credential_exfiltration",
         r"(?:send|post|upload|exfiltrate|forward) .{0,80}(?:password|api key|cookies?|credentials?|session token)"),
        # A page whose entire "content" is a base64 image is a block page or
        # a shell, not an article. It is also the shape Reddit's bot wall
        # takes, so it must never be stored as if it were a post.
        ("embedded_data_image",
         r"data:image/(?:png|jpe?g|gif|webp|svg\+xml);base64,[A-Za-z0-9+/]{256,}"),
        # An <iframe> in retrieved content is a common exfiltration vector.
        ("embedded_iframe", r"<\s*iframe\b"),
        # Private key material pasted into a page.
        ("secret_material",
         r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----"),
    )
)

_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"[ \t]{2,}")


def scan(text: str) -> List[str]:
    """Return sorted, de-duplicated flags for a piece of web text."""
    flags = set()
    if not text:
        return []
    if _INVISIBLE_RE.search(text):
        flags.add(FLAG_INVISIBLE)
    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            flags.add(f"prompt_injection:{name}")
    return sorted(flags)


def sanitize(text: str, max_chars: int = 2000) -> str:
    """Strip invisible and control characters, collapse spaces, bound length."""
    cleaned = _INVISIBLE_RE.sub("", str(text or ""))
    cleaned = _CONTROL_RE.sub("", cleaned)
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    if max_chars and len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1].rstrip() + "…"
    return cleaned


def strip_control_chars(text: str) -> str:
    """Remove control and invisible characters without touching anything else."""
    cleaned = _INVISIBLE_RE.sub("", str(text or ""))
    return _CONTROL_RE.sub("", cleaned)


def truncate_text(text: str, max_chars: int) -> Tuple[str, bool]:
    """Enforce a hard character budget on retrieved content.

    Returns ``(text, truncated)``. The result is **never longer than
    ``max_chars``**: the truncation notice is reserved *inside* the budget
    rather than appended to it. (It used to be appended, so the returned
    string was 24-26 characters longer than the documented cap — a budget
    the caller could not rely on.)

    Truncation is explicit: a marker says how many characters were omitted,
    so the consumer knows the content is partial instead of assuming it is
    complete.
    """
    text = str(text or "")
    if not max_chars or len(text) <= max_chars:
        return text, False
    if max_chars <= 0:
        return "", True
    # Reserve room for the notice, then re-state the omitted count (its width
    # can change the reservation by a digit) until the arithmetic is stable.
    keep = max(0, max_chars - 32)
    notice = ""
    for _ in range(4):
        notice = f"\n\n…[truncated {len(text) - keep} chars]"
        if len(notice) > max_chars:
            # The budget is too small for a notice; cut hard and stay honest.
            return text[:max_chars], True
        new_keep = max(0, max_chars - len(notice))
        if new_keep == keep:
            break
        keep = new_keep
    body = text[:keep].rstrip()
    result = body + notice
    # Hard guarantee: the budget is the budget.
    if len(result) > max_chars:
        result = result[:max_chars]
    return result, True
