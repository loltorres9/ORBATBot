#!/usr/bin/env python3
"""Write the how-tos from `utils/help.py` into the README.

    python scripts/gen_help.py          # rewrite README.md in place
    python scripts/gen_help.py --check  # fail if it is out of date

The `--check` form is the useful one: it is what tells you the README has
fallen behind the topics without touching the file, so it can be run before a
commit or from CI if this repository ever grows one.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import help as helpmod  # noqa: E402


README = Path(__file__).resolve().parent.parent / 'README.md'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='exit 1 if the README is out of date')
    parser.add_argument('--path', type=Path, default=README)
    args = parser.parse_args()

    current = args.path.read_text(encoding='utf-8')
    try:
        updated = helpmod.splice_readme(current)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    if current == updated:
        print(f"✅ {args.path.name} is up to date "
              f"({len(helpmod.TOPICS)} how-tos)")
        return 0

    if args.check:
        print(f"❌ {args.path.name} is out of date — run "
              f"`python scripts/gen_help.py`", file=sys.stderr)
        return 1

    args.path.write_text(updated, encoding='utf-8')
    print(f"✅ wrote {len(helpmod.TOPICS)} how-tos into {args.path.name}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
