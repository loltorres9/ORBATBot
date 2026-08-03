"""Running the web UI inside the bot's process.

uvicorn is driven programmatically rather than from a second start command, so
one Railway service (and one `docker compose up`) still runs everything, and the
web layer can reach the live `bot` object directly.
"""

import asyncio

import uvicorn

from web.app import create_app
from web.config import WebConfig


class _BotOwnedServer(uvicorn.Server):
    """uvicorn installs SIGINT/SIGTERM handlers on serve(); here the bot owns the
    process, so they are deliberately left alone."""

    def install_signal_handlers(self) -> None:
        pass


class WebServer:
    def __init__(self, bot, config: WebConfig):
        self.bot = bot
        self.config = config
        self._server = None
        self._task = None

    async def start(self) -> None:
        app = create_app(self.bot, self.config)
        settings = uvicorn.Config(
            app,
            host=self.config.host,
            port=self.config.port,
            log_level='info',
            access_log=False,
            # Railway and most proxies terminate TLS in front of us.
            proxy_headers=True,
            forwarded_allow_ips='*',
        )
        self._server = _BotOwnedServer(settings)
        self._task = asyncio.create_task(self._server.serve(), name='orbat-web')

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._server = self._task = None
