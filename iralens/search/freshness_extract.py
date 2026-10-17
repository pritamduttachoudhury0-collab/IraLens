# -*- coding: utf-8 -*-
"""Cheap offline date extraction for results that carry no date (D7).

Sources, in order: ISO date in the snippet, ``/YYYY/MM/DD/`` or
``/YYYY-MM-DD`` in the URL path, and a written date (``5 August 2026`` /
``Aug 5, 2026``) in the snippet. No page is fetched. The second return value
records *which* pattern produced the date so a wrong date is debuggable.
Ambiguous numeric forms (05/08/2026) are deliberately not guessed.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional, Tuple

_ISO_RE = re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
_URL_DATE_RE = re.compile(r"/(20\d{2})[/-](0[1-9]|1[0-2])[/-](0[1-9]|[12]\d|3[01])(?:[/-]|\b)")
_MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"), start=1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_WRITTEN_RE = re.compile(
    r"\b(0?[1-9]|[12]\d|3[01])\s+(January|February|March|April|May|June|July|August|"
    r"September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(20\d{2})\b",
    re.IGNORECASE)
_WRITTEN_US_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(0?[1-9]|[12]\d|3[01]),\s*(20\d{2})\b",
    re.IGNORECASE)


def _valid(y: int, m: int, d: int) -> Optional[str]:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def extract_date(text: str, url: str = "") -> Tuple[Optional[str], str]:
    text = text or ""
    m = _ISO_RE.search(text)
    if m:
        found = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if found:
            return found, "snippet_iso"
    m = _URL_DATE_RE.search(url or "")
    if m:
        found = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if found:
            return found, "url_path"
    m = _WRITTEN_RE.search(text)
    if m:
        found = _valid(int(m.group(3)), _MONTHS[m.group(2).lower().rstrip(".")[:3] if len(m.group(2)) > 3 else m.group(2).lower()], int(m.group(1)))
        if found:
            return found, "snippet_written"
    m = _WRITTEN_US_RE.search(text)
    if m:
        key = m.group(1).lower().rstrip(".")
        found = _valid(int(m.group(3)), _MONTHS.get(key, _MONTHS.get(key[:3], 0)), int(m.group(2)))
        if found:
            return found, "snippet_written"
    return None, ""
