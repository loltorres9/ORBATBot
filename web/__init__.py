"""Optional web UI for the bot.

Nothing in here is imported by the bot's core: `bot.py` loads it inside a
try/except and skips it when the dependencies or the configuration are absent,
so an existing deployment keeps running exactly as before until it is
configured.
"""

from web.config import WebConfig, load_config
from web.server import WebServer

__all__ = ['WebConfig', 'load_config', 'WebServer']
