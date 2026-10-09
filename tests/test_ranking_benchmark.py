# -*- coding: utf-8 -*-
"""Guard rails for the ranking benchmark (REPLACEMENT benchmark, synthetic).

The numbers below were measured on this branch with
`python scripts/ranking_benchmark.py`; they are asserted as floors so a
future change cannot silently regress ranking quality. If a change improves
the metrics, raise the floors and record the new measurement in DECISIONS.md.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "scripts"))

from ranking_benchmark import CASES, legacy_relevance, metrics  # noqa: E402
from iralens.search.ranking import relevance as new_relevance  # noqa: E402


def test_benchmark_has_twenty_cases():
    assert len(CASES) == 20


def test_new_scorer_does_not_regress_on_benchmark():
    legacy = metrics(legacy_relevance)
    new = metrics(new_relevance)
    # Measured at introduction: legacy MRR=0.8750, nDCG@5=0.9077;
    # new MRR=0.9750 (+0.1000), nDCG@5=0.9815 (+0.0738).
    assert new["MRR"] >= legacy["MRR"]
    assert new["nDCG@5"] >= legacy["nDCG@5"]
    assert new["MRR"] >= 0.975
    assert new["nDCG@5"] >= 0.981
