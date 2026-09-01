"""The ORBAT text format — now shared with the bot.

The prototype's parser graduated into `utils/orbat.py` when the editor moved
into the real web UI. This module stays as the lab's entry point so the
standalone playground keeps working, but there is only one implementation:
two copies of this parser would drift, and a drifting parser silently loses
slots.
"""

from utils.orbat import (  # noqa: F401
    MAX_SLOT_NAME,
    MAX_SLOTS,
    MAX_SQUAD_NAME,
    MAX_SQUADS,
    MAX_NET_CHANNEL,
    MAX_NET_NAME,
    MAX_NETS,
    NetsResult,
    ParsedNet,
    ParsedSlot,
    ParsedSquad,
    ParseResult,
    assign_columns,
    nets_to_text,
    parse,
    parse_nets,
    to_text,
)
