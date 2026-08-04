"""Configuration for the web UI.

Every setting is read from the environment, and the whole feature is opt-in:
with nothing configured `load_config()` returns a config that reports itself as
not ready, `bot.py` prints why and opens no HTTP listener at all.
"""

import os
from dataclasses import dataclass, field

# Discord's OAuth2 endpoints. `identify` is the only scope asked for — guild
# membership and roles come from the bot's own connection instead of the user's
# token, so the consent screen stays as small as possible.
AUTHORIZE_URL = 'https://discord.com/oauth2/authorize'
TOKEN_URL = 'https://discord.com/api/v10/oauth2/token'
USER_URL = 'https://discord.com/api/v10/users/@me'
OAUTH_SCOPES = 'identify'

_FALSEY = ('0', 'false', 'no', 'off')

# Shown in the top-left corner, in the browser tab and in the footer. Override
# with WEB_BRAND to rename the site without touching the templates.
DEFAULT_BRAND = 'TFP BOT'

# A logo dropped into web/static under one of these names is picked up
# automatically — in the header next to the name, and as the favicon.
LOGO_NAMES = ('logo.png', 'logo.webp', 'logo.svg', 'logo.jpg', 'logo.jpeg')


@dataclass
class WebConfig:
    client_id: str = ''
    client_secret: str = ''
    secret_key: str = ''
    base_url: str = ''
    brand: str = DEFAULT_BRAND
    host: str = '0.0.0.0'
    port: int = 8080
    disabled: bool = False
    # Session lifetime; a week is long enough that nobody re-logs in mid-op.
    session_max_age: int = 7 * 24 * 3600
    _missing: list = field(default_factory=list)

    @property
    def missing(self) -> list:
        return list(self._missing)

    @property
    def ready(self) -> bool:
        return not self.disabled and not self._missing

    @property
    def cookie_secure(self) -> bool:
        # Only mark cookies Secure when the site is actually served over TLS,
        # otherwise a local http:// run would never see its own session back.
        return self.base_url.startswith('https://')

    def redirect_uri(self, request=None) -> str:
        """The OAuth2 callback URL, which has to match the Developer Portal entry.

        WEB_BASE_URL is authoritative. Falling back to the request means a
        deployment behind a proxy has to be trusted to report its own scheme, so
        it is only a convenience for local runs.
        """
        if self.base_url:
            return f"{self.base_url}/auth/callback"
        if request is None:
            raise RuntimeError('WEB_BASE_URL is not set and no request to derive it from')
        scheme = request.headers.get('x-forwarded-proto', request.url.scheme)
        host = request.headers.get('x-forwarded-host') or request.headers.get('host')
        return f"{scheme}://{host}/auth/callback"


def load_config() -> WebConfig:
    config = WebConfig(
        client_id=(os.getenv('DISCORD_CLIENT_ID') or '').strip(),
        client_secret=(os.getenv('DISCORD_CLIENT_SECRET') or '').strip(),
        secret_key=(os.getenv('WEB_SECRET_KEY') or '').strip(),
        base_url=(os.getenv('WEB_BASE_URL') or '').strip().rstrip('/'),
        brand=(os.getenv('WEB_BRAND') or '').strip() or DEFAULT_BRAND,
        host=(os.getenv('WEB_HOST') or '0.0.0.0').strip(),
        # Railway injects PORT; 8080 is only the local default.
        port=int(os.getenv('PORT') or os.getenv('WEB_PORT') or 8080),
        disabled=(os.getenv('WEB_ENABLED') or '').strip().lower() in _FALSEY,
    )
    config._missing = [
        name for name, value in (
            ('DISCORD_CLIENT_ID', config.client_id),
            ('DISCORD_CLIENT_SECRET', config.client_secret),
            ('WEB_SECRET_KEY', config.secret_key),
        ) if not value
    ]
    return config
