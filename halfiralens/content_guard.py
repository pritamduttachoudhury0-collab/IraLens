# -*- coding: utf-8 -*-
"""Content guard: sanitization and prompt-injection flagging for web content.

Layer 2 of the security model (see docs/THREAT_MODEL.md). It never decides
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
