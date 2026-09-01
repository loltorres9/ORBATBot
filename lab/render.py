"""The board, and what Discord would do to it.

Graduated into `utils/orbat.py` — see lab/parser.py for why this is a re-export
rather than a second copy.
"""

from utils.orbat import (  # noqa: F401
    MAX_EMBED_CHARS,
    MAX_FIELD_NAME,
    MAX_FIELD_VALUE,
    MAX_FIELDS,
    MAX_ROWS,
    build_board,
    check_limits,
)
