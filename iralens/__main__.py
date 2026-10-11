# -*- coding: utf-8 -*-
"""`python -m iralens` — the same entry point as the `iralens` console script.

Agents and tests that cannot rely on the console script being on PATH (the
common case inside a virtualenv that was not activated) can run the CLI as
`python -m iralens`. Behavior and exit codes are identical; `main()` is the one
implementation (D-079).
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
