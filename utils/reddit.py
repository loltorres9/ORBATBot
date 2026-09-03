"""Reading a Reddit user's or a subreddit's public feed.

Reddit publishes both as an Atom feed — `/user/<name>/submitted.rss` and
`/r/<name>/new.rss` — which needs no API registration, no OAuth and no client
secret. The one thing it does need is a User-Agent that identifies the caller;
without one Reddit answers 429 to everything.

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

_ATOM = '{http://www.w3.org/2005/Atom}'


class FeedError(Exception):
    """A feed couldn't be read. The message is meant to be shown to a person."""


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


def feed_url(kind: str, source: str) -> str:
    """The Atom feed for one watch.

    `.rss` rather than `.json`: the JSON endpoint is the API surface Reddit
    rate-limits per OAuth client, while the feed is the same public page a
    browser gets.
    """
    if clean_kind(kind) == 'subreddit':
        return f"https://www.reddit.com/r/{source}/new.rss"
    return f"https://www.reddit.com/user/{source}/submitted.rss"


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


def parse_feed(xml_text: str) -> list:
    """The feed's entries, newest first — the order Reddit returns them in.

    Each is a plain dict: `id`, `title`, `url`, `author`, `subreddit`,
    `published`. An entry without an id or a link is skipped rather than raising:
    one malformed entry must not cost the rest of the feed.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as e:
        raise FeedError(f"That didn't come back as a feed ({e}).")

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
            subreddit = (category.get('label') or category.get('term') or '')
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


async def fetch(kind: str, source: str, *, timeout: int = 15) -> list:
    """Read one feed. Raises `FeedError` with something a person can act on.

    `aiohttp` is imported here rather than at the top so the rest of this module
    stays importable without it — the same reason `utils/sheets.py` only reaches
    for its credentials when a sheet is actually touched.
    """
    import aiohttp

    url = feed_url(kind, source)
    headers = {'User-Agent': user_agent()}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
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
                    raise FeedError(
                        "Reddit is rate-limiting us. It sorts itself out; "
                        "the next check will try again."
                    )
                if response.status != 200:
                    raise FeedError(f"Reddit answered {response.status}.")
                body = await response.text()
    except FeedError:
        raise
    except aiohttp.ClientError as e:
        raise FeedError(f"Couldn't reach Reddit: {e}")
    except TimeoutError:
        raise FeedError("Reddit didn't answer in time.")

    return parse_feed(body)


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
