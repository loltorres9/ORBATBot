"""Reading a Reddit user's or a subreddit's public feed.

Reddit publishes both as an Atom feed — `/user/<name>/submitted.rss` and
`/r/<name>/new.rss` — which needs no API registration, no OAuth and no client
secret. It does want a User-Agent that identifies the caller; without one Reddit
answers 429 to everything.

Identifying yourself is not always enough, though, and this is the thing to know
before changing anything here: Reddit also refuses **where** the request comes
from. A hosting provider's address is turned away with 429 however politely it
asks and however rarely, which is why `fetch()` tries `old.reddit.com` after
`www` — the legacy renderer is markedly less fussy — and why a refusal comes
back as `RateLimited` carrying a wait, rather than as a plain error the caller
would retry on its next tick.

**This announces a post. It never asks anyone to vote on one.** Organising votes
off-platform is what Reddit's content policy calls vote manipulation, and it
costs the poster *and* the people who answer the call their accounts — a group
that reliably votes minutes after the same author posts is exactly the pattern
that is detected. So the message template is a notification, and the counts on a
post are deliberately not read, rendered or referred to anywhere in here.

Nothing in this module talks to Discord or to the database, and everything
except `fetch()` uses only the standard library — `aiohttp` is imported inside
that one function. That is what keeps the parsing and rendering half testable on
its own (`tests/test_reddit.py`).
"""

import os
import re
from datetime import datetime, timezone
from xml.etree import ElementTree

# What a watch can point at: the label is what the web form shows, the prefix is
# how Reddit itself writes the name.
FEED_KINDS = (
    ('user', 'Reddit user', 'u/'),
    ('subreddit', 'Subreddit', 'r/'),
)
KIND_KEYS = tuple(key for key, _, _ in FEED_KINDS)
DEFAULT_KIND = 'user'

# Reddit's own limits: 3-20 characters for a user, 3-21 for a subreddit. The
# range is widened by one at each end rather than enforced exactly, because a
# name this rejects is a name the feed can never be read for — and Reddit is the
# authority on which of its own names exist.
_NAME = re.compile(r'^[A-Za-z0-9_-]{2,24}$')

# Pulls the name out of anything someone is likely to paste: a profile URL, a
# subreddit URL, `u/name`, `/r/name`, or the bare name.
_FROM_URL = re.compile(
    r'reddit\.com/(?:u|user|r)/([A-Za-z0-9_-]+)', re.IGNORECASE
)
_PREFIXED = re.compile(r'^/?(?:u|user|r)/([A-Za-z0-9_-]+)/?$', re.IGNORECASE)

# The placeholders a message template may use. `render()` substitutes exactly
# these and leaves every other brace alone, so a template is never a format
# string somebody can crash with a stray `{`.
PLACEHOLDERS = ('title', 'url', 'author', 'subreddit')

DEFAULT_TEMPLATE = 'New post by u/{author} in r/{subreddit}\n**{title}**\n{url}'

MAX_TEMPLATE = 1500

# Reddit caps a title at 300 characters; trimming here keeps one long title from
# pushing the link out of a 2000-character Discord message.
MAX_TITLE = 240

# Identifies the bot to Reddit. Anything descriptive is accepted; an empty or
# generic agent (the default of most HTTP clients) is rate-limited to nothing.
DEFAULT_USER_AGENT = 'orbatbot-feed/1.0 (Discord post notifier)'

# The same feed, from two hosts. `www` is the one that carries Reddit's bot
# detection and answers a request from a hosting provider's address with 429
# however politely it identifies itself; `old` is the legacy renderer and is
# markedly less fussy. Tried in this order, and only ever both on a check that
# has already been refused.
FEED_HOSTS = ('https://www.reddit.com', 'https://old.reddit.com')

# How long a rate-limited feed is left alone when Reddit doesn't say itself.
# Generous on purpose: retrying a refusal every few minutes is what keeps a
# throttle warm, and a watch on somebody who posts twice a week loses nothing
# by waiting half an hour.
DEFAULT_RETRY_AFTER = 1800
MAX_RETRY_AFTER = 6 * 3600

_ATOM = '{http://www.w3.org/2005/Atom}'


class FeedError(Exception):
    """A feed couldn't be read. The message is meant to be shown to a person."""


class RateLimited(FeedError):
    """Reddit refused *us*, rather than saying anything about the feed.

    It carries how long to wait, so the caller can stand the watch down instead
    of asking again on the next tick — which is what turns a passing throttle
    into a standing one.
    """

    def __init__(self, message: str, retry_after: int = DEFAULT_RETRY_AFTER):
        super().__init__(message)
        self.retry_after = retry_after


def user_agent() -> str:
    return (os.getenv('REDDIT_USER_AGENT') or '').strip() or DEFAULT_USER_AGENT


def clean_kind(raw) -> str:
    return raw if raw in KIND_KEYS else DEFAULT_KIND


def kind_prefix(kind: str) -> str:
    return next((prefix for key, _, prefix in FEED_KINDS if key == kind), '')


def clean_source(raw: str) -> str:
    """The bare name out of whatever was pasted, or '' if it isn't one.

    Accepts `TaskForcePhalanx`, `u/TaskForcePhalanx`, `/user/TaskForcePhalanx`
    and a full profile or subreddit URL — all four are what people have to hand
    when they come to set this up.
    """
    text = (raw or '').strip()
    if not text:
        return ''
    for pattern in (_FROM_URL, _PREFIXED):
        found = pattern.search(text)
        if found:
            text = found.group(1)
            break
    text = text.strip('/')
    return text if _NAME.match(text) else ''


def feed_url(kind: str, source: str, host: str = FEED_HOSTS[0]) -> str:
    """The Atom feed for one watch, on one of `FEED_HOSTS`.

    `.rss` rather than `.json`: the JSON endpoint is the API surface Reddit
    rate-limits per OAuth client, while the feed is the same public page a
    browser gets.
    """
    if clean_kind(kind) == 'subreddit':
        return f"{host}/r/{source}/new.rss"
    return f"{host}/user/{source}/submitted.rss"


def page_url(kind: str, source: str) -> str:
    """Where a person would go to look at the same thing."""
    if clean_kind(kind) == 'subreddit':
        return f"https://www.reddit.com/r/{source}/new/"
    return f"https://www.reddit.com/user/{source}/submitted/"


def _text(entry, tag: str) -> str:
    found = entry.find(f"{_ATOM}{tag}")
    return (found.text or '').strip() if found is not None else ''


def _when(entry):
    """The entry's timestamp as aware UTC, or None.

    `published` is preferred over `updated`: an edited post keeps the first and
    moves the second, and a feed watch should not treat an edit as a new post.
    """
    for tag in ('published', 'updated'):
        raw = _text(entry, tag)
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            continue
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return None


class NotAFeed(FeedError):
    """Something came back with a 200, but it wasn't a feed.

    Treated like a refusal rather than like a missing feed: the other host is
    asked too, because a body that isn't the feed is usually about who is
    asking, not about what was asked for.
    """


# Characters XML 1.0 forbids outright. No valid feed can contain one, so
# dropping them cannot change a well-formed document — and it rescues the one
# way a single stray byte in a post title otherwise costs the whole feed.
_ILLEGAL_BYTES = re.compile(rb'[\x00-\x08\x0b\x0c\x0e-\x1f]')
_ILLEGAL_TEXT = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def _strip_illegal(body):
    """The body without the characters XML refuses to see. Same type back."""
    if isinstance(body, bytes):
        return _ILLEGAL_BYTES.sub(b'', body)
    return _ILLEGAL_TEXT.sub('', body or '')


def _as_text(body) -> str:
    """The body as text, for looking at rather than for parsing."""
    if isinstance(body, bytes):
        return body.decode('utf-8', errors='replace')
    return body or ''


def _fragment(body, position, width: int = 70) -> str:
    """What the parser choked on, as `repr` so an invisible character shows.

    A parse error's line and column mean nothing to whoever is reading a flash
    message; the actual characters are the whole diagnosis — mis-decoded text, a
    control character, or a page that isn't a feed all look completely different
    here.
    """
    try:
        line_no, column = position
        line = _as_text(body).splitlines()[line_no - 1]
    except (IndexError, TypeError, ValueError):
        return ''
    start = max(0, column - width // 2)
    return repr(line[start:start + width])


def parse_feed(body) -> list:
    """The feed's entries, newest first — the order Reddit returns them in.

    Takes the raw bytes, or text. **Bytes are what `fetch()` passes**: an XML
    document declares its own encoding, and that declaration is the authority —
    not the HTTP header, and not a guess made from the bytes, either of which
    turns one accented character in a post title into a parse error two hundred
    columns into line 20.

    Each entry is a plain dict: `id`, `title`, `url`, `author`, `subreddit`,
    `published`. An entry without an id or a link is skipped rather than raising:
    one malformed entry must not cost the rest of the feed.
    """
    if _as_text(body).lstrip()[:200].lower().startswith(('<!doctype html', '<html')):
        # Reddit serves its "are you a robot" and block pages with a 200, so an
        # HTML body is a refusal that didn't say so — which is the same thing a
        # 429 says, and is handled the same way.
        raise RateLimited(
            "Reddit answered with a web page instead of the feed, which is how "
            "it turns a request away without saying so."
        )

    body = _strip_illegal(body)
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as e:
        where = _fragment(body, getattr(e, 'position', None))
        raise NotAFeed(
            f"That didn't come back as a feed ({e})."
            + (f" It reads {where} there." if where else '')
        )

    posts = []
    for entry in root.findall(f"{_ATOM}entry"):
        link = entry.find(f"{_ATOM}link")
        url = (link.get('href') or '').strip() if link is not None else ''
        post_id = _text(entry, 'id')
        if not post_id or not url:
            continue

        author = ''
        author_el = entry.find(f"{_ATOM}author")
        if author_el is not None:
            # Reddit writes it as `/u/name`.
            author = _text(author_el, 'name').lstrip('/').removeprefix('u/')

        subreddit = ''
        category = entry.find(f"{_ATOM}category")
        if category is not None:
            # `term` over `label`: Reddit writes them as term="arma"
            # label="r/arma", and for a post made on somebody's own profile as
            # term="u_Name" label="u/Name". The term is already what belongs
            # after an `r/` in both cases — a profile post really does live in
            # r/u_Name — while the label would render as `r/u/Name`, which is
            # not a place.
            subreddit = (category.get('term') or category.get('label') or '')
            subreddit = subreddit.removeprefix('r/')

        posts.append({
            'id': post_id,
            'title': _text(entry, 'title'),
            'url': url,
            'author': author,
            'subreddit': subreddit,
            'published': _when(entry),
        })
    return posts


async def _read(session, url: str, headers: dict, kind: str, source: str) -> str:
    """One request. Raises `FeedError` for anything that isn't a feed."""
    async with session.get(url, headers=headers) as response:
        if response.status == 404:
            raise FeedError(
                f"Reddit has no {kind_prefix(kind)}{source} — check the spelling."
            )
        if response.status == 403:
            raise FeedError(
                f"{kind_prefix(kind)}{source} is private or suspended, "
                "so its feed can't be read."
            )
        if response.status == 429:
            raise RateLimited(
                "Reddit is rate-limiting this server.",
                _retry_after(response.headers.get('Retry-After')),
            )
        if response.status != 200:
            raise FeedError(f"Reddit answered {response.status}.")
        return await response.read()


def _retry_after(header) -> int:
    """Reddit's own wait, when it gives one, bounded to something sensible."""
    try:
        seconds = int((header or '').strip())
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER
    return max(60, min(seconds, MAX_RETRY_AFTER))


async def fetch(kind: str, source: str, *, timeout: int = 15) -> list:
    """Read one feed. Raises `FeedError` with something a person can act on.

    Both hosts are tried, because being rate-limited is not a property of the
    feed but of who is asking: `www` carries the bot detection that answers a
    cloud host's request with 429, and `old` is the legacy renderer, which the
    same request often gets through. The second request only happens on a check
    that has already failed, so it costs nothing in the normal case.

    `aiohttp` is imported here rather than at the top so the rest of this module
    stays importable without it — the same reason `utils/sheets.py` only reaches
    for its credentials when a sheet is actually touched.
    """
    import aiohttp

    headers = {
        'User-Agent': user_agent(),
        # Ask for the feed rather than for a page: an `Accept: */*` can be
        # answered with Reddit's HTML interstitial, which is not a feed and
        # would come back as a parse error rather than as what it is.
        'Accept': 'application/atom+xml, application/rss+xml;q=0.9, */*;q=0.8',
    }

    limited, last = None, None
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout)
    ) as session:
        for host in FEED_HOSTS:
            try:
                return parse_feed(
                    await _read(session, feed_url(kind, source, host),
                                headers, kind, source)
                )
            except RateLimited as e:
                # The wait taken is the longest either host asked for, so an
                # explicit Retry-After is never shortened by the other host's
                # silence.
                if limited is None or e.retry_after > limited.retry_after:
                    limited = e
            except NotAFeed as e:
                # A body that isn't the feed is about who is asking rather than
                # about what was asked for, so the other host is worth trying —
                # but it is not a refusal, so it doesn't stand the watch down.
                last = e
            except FeedError:
                # A 404 or a 403 is about the feed itself and says the same
                # thing from either host; only something aimed at *us* is worth
                # asking the other one about.
                raise
            except aiohttp.ClientError as e:
                last = FeedError(f"Couldn't reach Reddit: {e}")
            except TimeoutError:
                last = FeedError("Reddit didn't answer in time.")

    raise limited or last


def render(template: str, post: dict) -> str:
    """The announcement text for one post.

    Only the placeholders in `PLACEHOLDERS` are substituted, one literal replace
    each — a template is text somebody typed, not a format string, so a stray
    brace in it has to be harmless.
    """
    text = template if (template or '').strip() else DEFAULT_TEMPLATE
    values = {
        'title': (post.get('title') or '')[:MAX_TITLE],
        'url': post.get('url') or '',
        'author': post.get('author') or '',
        'subreddit': post.get('subreddit') or '',
    }
    for name in PLACEHOLDERS:
        text = text.replace('{' + name + '}', values[name])
    return text.strip()
