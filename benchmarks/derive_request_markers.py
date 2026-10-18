#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Learn the request-marker lexicon from the audit corpus, and test it honestly.

The frozen corpus is `<topic> + <template suffix>`, and it records the
topic. That is supervised data for exactly the split the `focus` query
variant depends on. The lexicon of request words is learned from it — but
only from a **training half**, and the accuracy is reported on a **held-out
half** and on the 50 hand-authored calibration queries, which no training
signal touches. A lexicon fitted on the whole corpus and then measured on
it would be a number about nothing.

    python benchmarks/derive_request_markers.py
    python benchmarks/derive_request_markers.py --write   # print the lexicon
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import List, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from iralens.search.queryfocus import (  # noqa: E402
    CONNECTIVES, REQUEST_MARKERS, REQUEST_PHRASES, SHAPE_WORDS, _is_date_like,
    _words_with_case, split_subject,
)


def suffix_of(row: dict) -> Tuple[str, str]:
    """(topic, request shape) for a templated corpus row."""
    topic = str(row.get("topic") or "")
    query = str(row.get("query") or "")
    if not topic:
        return "", ""
    if query.lower().startswith(topic.lower()):
        return topic, query[len(topic):].strip()
    return topic, ""


def learn(train: List[dict]) -> Tuple[Set[str], Set[str]]:
    """Learn the request lexicon from labelled rows.

    Two tiers:

    * **markers** — words that begin a request shape. A marker is a word
      that appears in request shapes and *never* in a subject.
    * **shape words** — words that may sit inside a request shape. A word
      qualifies if it appears in request shapes far more often than in
      subjects; a subject word that happens to appear in shapes once or
      twice does not disqualify it.

    A subject is a topic, and a topic word that also occurs in shapes
    ("examples" in "Simpson paradox examples" and in "minimal reproducible
    example") must not be mistaken for a marker — hence the ratio.
    """
    shape_words: Counter = Counter()
    topic_words: Counter = Counter()
    phrases: Set[str] = set()
    for row in train:
        topic, shape = suffix_of(row)
        if not shape:
            continue
        words = [w.lower() for w in _words_with_case(shape)]
        if words:
            phrases.add(" ".join(words))
        for word in words:
            shape_words[word] += 1
        for word in _words_with_case(topic):
            topic_words[word.lower()] += 1
    markers: Set[str] = set()
    shape_vocab: Set[str] = set()
    for word, count in shape_words.items():
        if count < 2:
            continue
        if word in CONNECTIVES or _is_date_like(word):
            continue
        topics = topic_words.get(word, 0)
        if topics == 0:
            markers.add(word)
            shape_vocab.add(word)
        elif count >= 4 and topics * 4 <= count:
            # Mostly a request word, occasionally a subject word.
            shape_vocab.add(word)
    return markers, shape_vocab, phrases


def learn_phrases(train: List[dict]) -> Set[str]:
    """The request shapes themselves, as normalised word sequences."""
    seen: Counter = Counter()
    for row in train:
        _topic, shape = suffix_of(row)
        if not shape:
            continue
        words = [w.lower() for w in _words_with_case(shape)]
        if words:
            seen[" ".join(words)] += 1
    return {phrase for phrase, count in seen.items() if count >= 2}


def score(markers: Set[str], rows: List[dict],
          shape_words: Set[str] | None = None,
          phrases: Set[str] | None = None) -> Tuple[int, int, List[Tuple[str, str, str]]]:
    """How many rows does the splitter recover the topic for?"""
    saved_marker = set(REQUEST_MARKERS)
    saved_shape = set(SHAPE_WORDS)
    saved_phrases = set(REQUEST_PHRASES)
    import iralens.search.queryfocus as qf
    qf.REQUEST_MARKERS = frozenset(markers)
    qf.SHAPE_WORDS = frozenset(shape_words if shape_words is not None else markers)
    if phrases is not None:
        qf.REQUEST_PHRASES = frozenset(phrases)
    exact = 0
    misses: List[Tuple[str, str, str]] = []
    try:
        for row in rows:
            topic, _shape = suffix_of(row)
            if not topic:
                continue
            subject, _got = split_subject(str(row["query"]))
            if subject.strip().lower() == topic.strip().lower():
                exact += 1
            elif len(misses) < 12:
                misses.append((str(row.get("id")), topic, subject))
    finally:
        qf.REQUEST_MARKERS = frozenset(saved_marker)
        qf.SHAPE_WORDS = frozenset(saved_shape)
        qf.REQUEST_PHRASES = frozenset(saved_phrases)
    total = sum(1 for row in rows if suffix_of(row)[0])
    return exact, total, misses


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="print the learned lexicon, ready to paste")
    ap.add_argument("--seed", type=int, default=20261018)
    args = ap.parse_args()

    corpus = REPO / "benchmarks" / "audit3000" / "queries.jsonl"
    rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    templated = [r for r in rows if r.get("topic")]
    hand = [r for r in rows if not r.get("topic")]

    rng = random.Random(args.seed)
    shuffled = list(templated)
    rng.shuffle(shuffled)
    half = len(shuffled) // 2
    train, test = shuffled[:half], shuffled[half:]

    print("=" * 74)
    print("REQUEST-MARKER LEXICON — learned, with a held-out test")
    print("=" * 74)
    print(f"train {len(train)}  test {len(test)}  hand-authored {len(hand)}")
    print()

    learned, learned_shape, learned_phrases = learn(train)
    print(f'learned {len(learned_phrases)} request phrases from the training half')
    print(f"learned {len(learned)} marker words and {len(learned_shape)} shape "
          f"words from the training half")
    print()

    combos = (
        ("hand-written (shipped)", set(REQUEST_MARKERS), set(SHAPE_WORDS), None),
        ("learned from train half", learned, learned_shape, learned_phrases),
        ("learned + hand-written", learned | set(REQUEST_MARKERS),
         learned_shape | set(SHAPE_WORDS), learned_phrases),
    )
    for label, markers, shape_words, phrases in combos:
        e, t, _m = score(markers, test, shape_words, phrases)
        print(f"  {label:<26} held-out {e:>4}/{t} = {e / t * 100:5.2f}%")

    print()
    best = learned | set(REQUEST_MARKERS)
    best_shape = learned_shape | set(SHAPE_WORDS)
    e, t, misses = score(best, test, best_shape, learned_phrases)
    print(f"held-out misses with the combined lexicon ({len(misses)} shown):")
    for qid, topic, subject in misses:
        print(f"  [{qid}] want {topic!r}")
        print(f"          got  {subject!r}")
    print()
    print("hand-authored calibration queries (never used for learning):")
    for row in hand[:10]:
        subject, shape = split_subject(str(row["query"]))
        print(f"  {row['id']}  subject={subject!r}")
        print(f"           shape  ={shape!r}")

    if args.write:
        print()
        print("LEXICON:")
        print(" ".join(sorted(best)))
        print()
        print("SHAPE_WORDS:")
        print(" ".join(sorted(best_shape)))
        print()
        print("REQUEST_PHRASES:")
        for ph in sorted(learned_phrases):
            print(f'"{ph}|"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
