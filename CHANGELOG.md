# Changelog

## Unreleased

- Retained the current tracked layout and all audit evidence, benchmark fixtures, notices, licenses, and limitations; no files were removed because the required pre-cleanup archive branch cannot be created from this fixed-branch session.
- Verified the baseline offline suite: 428 passed and 14 skipped. The skips are defined by existing test conditions; live/network behavior was not validated here.
- Verified mypy against `iralens`: no issues in 67 source files. Ruff currently reports 1,201 findings across the repository, so the requested clean-lint gate is not met.
- Safer release decision: did not alter the default branch, merge other branches, trigger workflows, tag a release, or claim a complete feature audit without GitHub administration access and successful end-to-end verification.
