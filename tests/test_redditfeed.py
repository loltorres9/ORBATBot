"""What `check_feed()` promises: the first read announces nothing, a post is
announced once, and a burst is spread rather than dropped.

It needs discord.py for the message objects, but no database and no network —
both are stubbed, since what is being tested is the bookkeeping around them.
"""

import asyncio
import datetime

import pytest

from cogs import redditfeed
from utils import database, reddit


class FakeChannel:
    def __init__(self):
        self.sent = []

    def permissions_for(self, _member):
        return type('P', (), {'send_messages': True})()

    async def send(self, content, allowed_mentions=None):
        self.sent.append((content, allowed_mentions))


class FakeGuild:
    def __init__(self, channel):
        self.me = object()
        self._channel = channel

    def get_channel(self, _id):
        return self._channel


class FakeBot:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, _id):
        return self._guild


def make_feed(**over):
    feed = {
        'id': 1, 'guild_id': '1', 'kind': 'user', 'source': 'Someone',
        'channel_id': '2', 'template': '{title} {url}', 'mention_role_id': None,
        'mention_user_id': None, 'enabled': 1, 'seen_ids': None,
    }
    feed.update(over)
    return feed


def make_posts(count):
    """Newest first, the order the feed itself comes back in."""
    return [
        {
            'id': f't3_{n}', 'title': f'Post {n}',
            'url': f'https://example.com/{n}',
            'author': 'Someone', 'subreddit': 'arma',
            'published': datetime.datetime(2026, 1, n + 1, tzinfo=datetime.timezone.utc),
        }
        for n in range(count, 0, -1)
    ]


@pytest.fixture
def run(monkeypatch):
    """Run one check against a stubbed feed, returning (messages, stored row)."""
    def runner(feed, posts):
        channel = FakeChannel()
        bot = FakeBot(FakeGuild(channel))
        stored = {}

        async def fake_fetch(kind, source, **kwargs):
            return posts

        async def fake_record(feed_id, **values):
            stored.update(values)

        monkeypatch.setattr(reddit, 'fetch', fake_fetch)
        monkeypatch.setattr(database, 'record_reddit_read', fake_record)
        result = asyncio.run(redditfeed.check_feed(bot, feed))
        return channel.sent, stored, result
    return runner


def test_the_first_read_announces_nothing(run):
    sent, stored, result = run(make_feed(seen_ids=None), make_posts(3))
    assert sent == []
    assert result['seeded'] == 3
    # Everything on the feed is remembered, so only later posts are announced.
    assert stored['seen_ids'] == 't3_3,t3_2,t3_1'


def test_a_new_post_is_announced_once(run):
    sent, stored, _ = run(make_feed(seen_ids='t3_1'), make_posts(2))
    assert [content for content, _ in sent] == ['Post 2 https://example.com/2']
    assert stored['seen_ids'] == 't3_2,t3_1'

    # Same feed again, with what the first check stored.
    sent, _, _ = run(make_feed(seen_ids=stored['seen_ids']), make_posts(2))
    assert sent == []


def test_a_burst_is_spread_rather_than_dropped(run):
    sent, stored, result = run(make_feed(seen_ids='t3_1'), make_posts(6))
    assert len(sent) == redditfeed.MAX_PER_CHECK
    assert result['waiting'] == 5 - redditfeed.MAX_PER_CHECK
    # Oldest first, so a burst arrives in the order it was written.
    assert [content for content, _ in sent] == [
        'Post 2 https://example.com/2',
        'Post 3 https://example.com/3',
        'Post 4 https://example.com/4',
    ]
    # Only what actually went out is marked seen; the rest follow next time.
    assert stored['seen_ids'] == 't3_4,t3_3,t3_2,t3_1'


def test_only_the_watch_s_own_mentions_are_allowed(run):
    # A post title is quoted verbatim, so it must not be able to ping the server.
    feed = make_feed(seen_ids='', template='@everyone {title}', mention_role_id='9')
    sent, _, _ = run(feed, make_posts(1))
    content, allowed = sent[0]
    assert content.startswith('<@&9>\n')
    assert allowed.everyone is False
    assert [role.id for role in allowed.roles] == [9]


def test_a_feed_that_cannot_be_read_records_the_reason(run, monkeypatch):
    async def broken(kind, source, **kwargs):
        raise reddit.FeedError('Reddit answered 404.')

    monkeypatch.setattr(reddit, 'fetch', broken)
    stored = {}

    async def fake_record(feed_id, **values):
        stored.update(values)

    monkeypatch.setattr(database, 'record_reddit_read', fake_record)
    bot = FakeBot(FakeGuild(FakeChannel()))
    with pytest.raises(reddit.FeedError):
        asyncio.run(redditfeed.check_feed(bot, make_feed(seen_ids='')))
    assert stored == {'error': 'Reddit answered 404.'}
    # Nothing was forgotten: seen_ids is untouched on a failed read.
    assert 'seen_ids' not in stored
