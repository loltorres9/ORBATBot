"""Configuration for the web UI.

Every setting is read from the environment, and the whole feature is opt-in:
with nothing configured `load_config()` returns a config that reports itself as
not ready, `bot.py` prints why and opens no HTTP listener at all.
"""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

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
    # Every name the site answers on, the canonical one first. `base_url` is
    # that first entry and is what anything without a request in hand uses.
    origins: tuple = ()
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

    def request_origin(self, request=None) -> str:
        """Which of the configured origins this request came in on.

        The site can answer on more than one name — a Railway subdomain and
        the unit's own domain, say — and every absolute URL it writes has to
        stay on the name the person is actually looking at. An OAuth callback
        to the other domain signs them in somewhere they did not start, and a
        share link is forwarded to people who may only know one of the two.

        **A host that is not configured is not trusted**, because the `Host`
        header belongs to whoever sent the request: it falls back to the
        canonical origin rather than being echoed into a link or a redirect.
        """
        host = _request_host(request)
        for origin in self.origins:
            if urlsplit(origin).netloc.lower() == host:
                return origin
        if self.base_url:
            return self.base_url
        if request is None:
            return ''
        # Nothing configured at all: derive it, which means trusting the proxy
        # to report its own scheme. That is a convenience for local runs.
        scheme = (request.headers.get('x-forwarded-proto')
                  or request.url.scheme).split(',')[0].strip()
        return f"{scheme}://{host}" if host else str(request.base_url).rstrip('/')

    def cookie_secure_for(self, request=None) -> bool:
        """Whether a cookie set on *this* request may be marked Secure.

        Per request rather than per deployment, because the origins can differ
        in scheme — a production domain on https alongside a local run on
        http, and a Secure cookie set over http never comes back.
        """
        if request is None:
            return self.cookie_secure
        return self.request_origin(request).startswith('https://')

    def redirect_uri(self, request=None) -> str:
        """The OAuth2 callback URL, which has to match a Developer Portal entry.

        Every origin needs its own entry registered there: Discord compares the
        one sent here against its list exactly, so a domain that is configured
        in the bot and missing in the portal fails the login rather than
        silently falling back.
        """
        origin = self.request_origin(request)
        if origin:
            return f"{origin}/auth/callback"
        raise RuntimeError('WEB_BASE_URL is not set and no request to derive it from')


def load_config() -> WebConfig:
    config = WebConfig(
        client_id=(os.getenv('DISCORD_CLIENT_ID') or '').strip(),
        client_secret=(os.getenv('DISCORD_CLIENT_SECRET') or '').strip(),
        secret_key=(os.getenv('WEB_SECRET_KEY') or '').strip(),
        origins=_origins(os.getenv('WEB_BASE_URL')),
        brand=(os.getenv('WEB_BRAND') or '').strip() or DEFAULT_BRAND,
        host=(os.getenv('WEB_HOST') or '0.0.0.0').strip(),
        # Railway injects PORT; 8080 is only the local default.
        port=int(os.getenv('PORT') or os.getenv('WEB_PORT') or 8080),
        disabled=(os.getenv('WEB_ENABLED') or '').strip().lower() in _FALSEY,
    )
    config.base_url = config.origins[0] if config.origins else ''
    config._missing = [
        name for name, value in (
            ('DISCORD_CLIENT_ID', config.client_id),
            ('DISCORD_CLIENT_SECRET', config.client_secret),
            ('WEB_SECRET_KEY', config.secret_key),
        ) if not value
    ]
    return config


def _origins(raw) -> tuple:
    """WEB_BASE_URL as a list: one origin per name the site answers on.

    Written with commas or whitespace between them, canonical first — that one
    is what a link built without a request in hand uses, and what the `Secure`
    flag follows when there is no request to read.
    """
    entries = []
    for chunk in (raw or '').replace(',', ' ').split():
        origin = chunk.strip().rstrip('/')
        if origin and origin not in entries:
            entries.append(origin)
    return tuple(entries)


def _request_host(request) -> str:
    """The host a request came in on, as the proxy in front of us reports it."""
    if request is None:
        return ''
    header = (request.headers.get('x-forwarded-host')
              or request.headers.get('host') or '')
    return header.split(',')[0].strip().lower()
