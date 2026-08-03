"""Discord OAuth2 login and cookie sessions.

There is no session table: the session *is* a signed cookie, which keeps the
schema untouched and survives a redeploy for free. It only ever holds the
Discord user id, a display name, an avatar hash and a CSRF token — every
permission decision is re-made from the bot's live view of the guild on each
request, so a cookie can't carry stale rights.
"""

import secrets
from typing import Optional
from urllib.parse import urlencode

import aiohttp
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from web.config import AUTHORIZE_URL, OAUTH_SCOPES, TOKEN_URL, USER_URL, WebConfig

SESSION_COOKIE = 'orbat_session'
STATE_COOKIE = 'orbat_oauth_state'
FLASH_COOKIE = 'orbat_flash'

_SESSION_SALT = 'orbat-session'
_STATE_SALT = 'orbat-oauth-state'
_FLASH_SALT = 'orbat-flash'

# The OAuth round trip is a browser redirect; ten minutes is plenty.
STATE_MAX_AGE = 600


class NotAuthenticated(Exception):
    """Raised by the request helpers; the app turns it into a redirect to /login."""

    def __init__(self, next_path: str = '/'):
        self.next_path = next_path
        super().__init__('not authenticated')


class Forbidden(Exception):
    """Signed in, but not allowed to do this. Rendered as a 403 page."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _serializer(config: WebConfig, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.secret_key, salt=salt)


# ---------------------------------------------------------------------------
# Session cookie
# ---------------------------------------------------------------------------

def read_session(request, config: WebConfig) -> Optional[dict]:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        data = _serializer(config, _SESSION_SALT).loads(
            raw, max_age=config.session_max_age
        )
    except (BadSignature, SignatureExpired):
        return None
    return data if isinstance(data, dict) and data.get('id') else None


def write_session(response, config: WebConfig, user: dict) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        _serializer(config, _SESSION_SALT).dumps(user),
        max_age=config.session_max_age,
        httponly=True,
        samesite='lax',
        secure=config.cookie_secure,
        path='/',
    )


def clear_session(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path='/')


def new_session(profile: dict) -> dict:
    """Build the session payload from a Discord /users/@me response."""
    return {
        'id': str(profile['id']),
        'name': profile.get('global_name') or profile.get('username') or 'Unknown',
        'avatar': profile.get('avatar'),
        'csrf': secrets.token_urlsafe(24),
    }


def avatar_url(session: dict) -> str:
    if session.get('avatar'):
        return (
            f"https://cdn.discordapp.com/avatars/{session['id']}/"
            f"{session['avatar']}.png?size=64"
        )
    # The default avatar for the post-discriminator username scheme.
    index = (int(session['id']) >> 22) % 6
    return f"https://cdn.discordapp.com/embed/avatars/{index}.png"


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def check_csrf(session: dict, token: str) -> None:
    if not token or not secrets.compare_digest(str(token), str(session.get('csrf', ''))):
        raise Forbidden(
            "That form was stale or came from somewhere else. Reload the page and try again."
        )


# ---------------------------------------------------------------------------
# One-shot flash messages
# ---------------------------------------------------------------------------

def set_flash(response, config: WebConfig, kind: str, text: str) -> None:
    response.set_cookie(
        FLASH_COOKIE,
        _serializer(config, _FLASH_SALT).dumps({'kind': kind, 'text': text}),
        max_age=60,
        httponly=True,
        samesite='lax',
        secure=config.cookie_secure,
        path='/',
    )


def read_flash(request, config: WebConfig) -> Optional[dict]:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    try:
        return _serializer(config, _FLASH_SALT).loads(raw, max_age=120)
    except (BadSignature, SignatureExpired):
        return None


# ---------------------------------------------------------------------------
# OAuth2 flow
# ---------------------------------------------------------------------------

def safe_next(raw: Optional[str]) -> str:
    """Only ever redirect back to a path on this site, never to another host."""
    if not raw or not raw.startswith('/') or raw.startswith('//'):
        return '/'
    return raw


def authorize_url(config: WebConfig, request, next_path: str) -> tuple:
    """Return (url, state_nonce). The nonce goes into a short-lived cookie and is
    compared on the way back, so a stray callback can't start a session."""
    nonce = secrets.token_urlsafe(16)
    state = _serializer(config, _STATE_SALT).dumps({'n': safe_next(next_path), 'x': nonce})
    query = urlencode({
        'client_id': config.client_id,
        'redirect_uri': config.redirect_uri(request),
        'response_type': 'code',
        'scope': OAUTH_SCOPES,
        'state': state,
        'prompt': 'none',
    })
    return f"{AUTHORIZE_URL}?{query}", state


def read_state(config: WebConfig, state: str) -> dict:
    try:
        data = _serializer(config, _STATE_SALT).loads(state, max_age=STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise Forbidden("That login link expired. Start again from the front page.")
    if not isinstance(data, dict):
        raise Forbidden("That login link was malformed. Start again from the front page.")
    return data


async def exchange_code(config: WebConfig, request, code: str) -> dict:
    """Swap the authorization code for a token, then read the user's profile.

    The access token is deliberately not stored: everything the site needs about
    the user afterwards comes from the bot's own guild data.
    """
    payload = {
        'client_id': config.client_id,
        'client_secret': config.client_secret,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': config.redirect_uri(request),
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            TOKEN_URL, data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        ) as resp:
            token = await resp.json()
            if resp.status != 200 or 'access_token' not in token:
                description = token.get('error_description') or token.get('error') or resp.status
                raise Forbidden(f"Discord refused the login: {description}")

        async with session.get(
            USER_URL, headers={'Authorization': f"Bearer {token['access_token']}"}
        ) as resp:
            if resp.status != 200:
                raise Forbidden("Discord accepted the login but wouldn't tell me who you are.")
            return await resp.json()
