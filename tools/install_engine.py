#!/usr/bin/env python3
"""Install the IraLens browser engine binary (prebuilt release).

Usage:
    python tools/install_engine.py            # into ~/.cache/iralens/engine
    python tools/install_engine.py --dir DIR  # into a specific directory
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iralens.engine.install import install_engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=None, help="target directory")
    args = parser.parse_args()
    path = install_engine(Path(args.dir) if args.dir else None)
    print(f"engine installed: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
