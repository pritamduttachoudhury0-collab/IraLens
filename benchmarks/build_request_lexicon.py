#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the shipped request lexicon: hand-written ∪ learned.

The hand-written part is the generalisation prior — the shapes a researcher
writes that the audit corpus does not happen to contain. The learned part
comes from `derive_request_markers.py`, which learns from a *training half*
of the corpus and reports its accuracy on a held-out half.

This script writes the merged lexicon into `iralens/search/queryfocus.py` so
the two stay in step and the derivation is reproducible.

    python benchmarks/build_request_lexicon.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import List

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

MODULE = REPO / "iralens" / "search" / "queryfocus.py"

#: The generalisation prior: shapes a researcher writes that the corpus does
#: not happen to contain, kept separate from what was learned.
HAND_MARKERS = """
archive arxiv articles cases change changes code comment comments comparison
concern consequences context counterexample counterexamples csv date dates
data dataset datasets debate definition definitions discrepancy disputed doi
documentation documents earliest economic encyclopedia error errors
evidence example examples experiment experimental explanation explanations
fact facts failing failure failures fee fees fix gaps guide guidelines
github government historical implications interpretation issue issues
latest legal legislation library literature limit limitations list literal
logs methodology methods migration minimal misconception misconceptions
note notes numeric official operations original page paper papers pdf primary principles proof
public published record records reference references release releases
reproduce reproducibility report reports research retrospective
revisions safety scoping sections semantics sources source statistics
study studies subscription superseding text texts trial uncertainty
version versions versus wiki worked
""".split()

HAND_SHAPE_WORDS = """
account accounts against alternative alternatives announcement article
behavior breaking browser case caveats check common comparability compare
competing conflicting conservation contemporary corroborated court current
dashboard debugging disambiguation downloadable effect failing guidance
guideline historical histories implication implications independent industry
interactive interpretations intervals known later latest lecture limitations
main metadata minimal navigation notes peer primary proof recent
recommended record reference regression reliable rendered reproducible
review reviewed reviews revisions rigorous section simplified specific
spelling statute study systematic technical terminology troubleshooting two
university update usual variants without worked who what where which how why
""".split()

HAND_PHRASES = """
original paper doi
original paper arxiv
latest release
latest release date
doi
arxiv
official documentation
primary source
original paper
worked numerical example
worked counterexample
exact date and terms
official dataset download
official policy
official procedure
official reference
primary documentation
exact dates primary sources
terminology definition sources
standards technical guide
source methodology and downloadable data
systematic review
troubleshooting guide
main article text without navigation
wikipedia references
""".split("\n")


def _learned() -> tuple:
    out = subprocess.run(
        [sys.executable, str(REPO / "benchmarks" / "derive_request_markers.py"), "--write"],
        capture_output=True, text=True, cwd=str(REPO)).stdout

    def first_line(header: str) -> List[str]:
        block = out.split(header, 1)[1]
        for line in block.split("\n"):
            if line.strip():
                return line.split()
        return []

    learned_markers = first_line("LEXICON:")
    learned_shapes = first_line("SHAPE_WORDS:")
    block = out.split("REQUEST_PHRASES:", 1)[1]
    learned_phrases = [m.group(1) for m in re.finditer(r'^"(.+?)\|"$', block, re.M)]
    return learned_markers, learned_shapes, learned_phrases


def _write(text: str, name: str, words: List[str], sep: str) -> str:
    """Replace one lexicon block.

    Word sets are wrapped; a phrase set keeps one phrase per line, because
    the phrases are separated by the ``|`` delimiter and must not be merged.
    """
    if sep == '"|"':
        # One phrase per line, because the phrases are separated by the
        # ``|`` delimiter and must not be merged. Each phrase is stripped,
        # so the set holds phrases and not lines of layout.
        start = text.index(f"{name}: frozenset = frozenset(")
        end = text.index(f'""".split({sep}))', start) + len(f'""".split({sep}))')
        body = "\n".join(f"{w}|" for w in words)
        block = (f'{name}: frozenset = frozenset(\n'
                 f'    phrase.strip()\n'
                 f'    for phrase in """\n{body}\n""".split({sep})\n'
                 f'    if phrase.strip()\n'
                 f')')
        return text[:start] + block + text[end:]
    start = text.index(f"{name}: frozenset = frozenset(\"\"\"")
    end = text.index(f'""".split({sep}))', start) + len(f'""".split({sep}))')
    body = "\n".join(" ".join(words[i:i + 14]) for i in range(0, len(words), 14))
    return text[:start] + f'{name}: frozenset = frozenset("""\n{body}\n""".split({sep}))' + text[end:]


#: Month names, so that a phrase made only of dates can be recognised.
_MONTHS = frozenset("""
    january february march april may june july august september october
    november december jan feb mar apr jun jul aug sep sept oct nov dec
""".split())


def _date_only(phrase: str) -> bool:
    """Is this phrase nothing but a date?

    "2026" was learned as a request phrase, and every query that ends in a
    year was then split there — the year became the request shape and the
    question lost it. A date is part of the subject, never a request.
    """
    words = [w for w in phrase.split() if w]
    if not words:
        return True
    for word in words:
        low = word.strip(".,;:!?()[]\"'").lower()
        if low in _MONTHS:
            continue
        if any(ch.isdigit() for ch in low):
            continue
        return False
    return True


def main() -> int:
    learned_markers, learned_shapes, learned_phrases = _learned()
    markers = sorted(set(HAND_MARKERS) | set(learned_markers))
    shapes = sorted(set(HAND_SHAPE_WORDS) | set(learned_shapes))
    phrases = sorted({p.strip() for p in HAND_PHRASES if p.strip()}
                     | {p.strip() for p in learned_phrases if not _date_only(p)})

    text = MODULE.read_text(encoding="utf-8")
    text = _write(text, "REQUEST_MARKERS", markers, "")
    text = _write(text, "SHAPE_WORDS", shapes, "")
    text = _write(text, "REQUEST_PHRASES", phrases, '"|"')
    MODULE.write_text(text, encoding="utf-8")
    print(f"shipped {len(markers)} markers, {len(shapes)} shape words, "
          f"{len(phrases)} phrases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
