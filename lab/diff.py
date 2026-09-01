"""Applying an edited ORBAT without unseating anybody.

Graduated into `utils/orbat.py` — see lab/parser.py for why this is a re-export
rather than a second copy.
"""

from utils.orbat import (  # noqa: F401
    OrbatDiff,
    SlotChange,
    SquadChange,
    build_diff,
    summarise,
)
