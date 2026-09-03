"""What `check_feed()` promises: the first read announces nothing, a post is
announced once, and a burst is spread rather than dropped.

It needs discord.py for the message objects, but no database and no network —
both are stubbed, since what is being tested is the bookkeeping around them.
"""

import asyncio
import datetime
import time

import pytest

from cogs import redditfeed
from utils import database, reddit


class FakeChannel:
    name = 'announcements'

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


def test_being_refused_stands_the_watch_down(monkeypatch):
    """A rate limit records when to try again, and forgets nothing.

    Asking again on the next tick is what turns a passing throttle into a
    standing one, so the row carries a `retry_at` that `get_due_reddit_feeds()`
    filters on.
    """
    async def refused(kind, source, **kwargs):
        raise reddit.RateLimited('Reddit is rate-limiting this server.', 900)

    stored = {}

    async def fake_record(feed_id, **values):
        stored.update(values)

    monkeypatch.setattr(reddit, 'fetch', refused)
    monkeypatch.setattr(database, 'record_reddit_read', fake_record)
    bot = FakeBot(FakeGuild(FakeChannel()))

    with pytest.raises(reddit.RateLimited):
        asyncio.run(redditfeed.check_feed(bot, make_feed(seen_ids='t3_1')))

    assert 'seen_ids' not in stored          # nothing is forgotten
    ahead = stored['retry_at'] - datetime.datetime.utcnow()
    assert 890 < ahead.total_seconds() <= 900
    assert stored['retry_at'].tzinfo is None, 'stored times are naive UTC'


def test_a_read_that_gets_through_clears_the_stand_down(run):
    # `retry_at` defaults to None on every recorded read, so the watch is back
    # in the rotation as soon as one succeeds.
    _, stored, _ = run(make_feed(seen_ids='t3_1'), make_posts(2))
    assert stored.get('retry_at') is None


# -- catching a post up by hand ---------------------------------------------

@pytest.fixture
def catch_up(monkeypatch):
    """Announce one post by hand. Returns (messages, stored row)."""
    def runner(feed, posts, post_id):
        channel = FakeChannel()
        bot = FakeBot(FakeGuild(channel))
        stored = {}

        async def fake_fetch(kind, source, **kwargs):
            return posts

        async def fake_record(feed_id, **values):
            stored.update(values)

        monkeypatch.setattr(reddit, 'fetch', fake_fetch)
        monkeypatch.setattr(database, 'record_reddit_read', fake_record)
        post = asyncio.run(redditfeed.announce_post(bot, feed, post_id))
        return channel.sent, stored, post
    return runner


def test_a_post_that_never_went_out_can_be_announced(catch_up):
    # `check_feed()` marks a post Discord refused as announced on purpose, so
    # this is the only way it reaches the channel.
    feed = make_feed(seen_ids='t3_2,t3_1')
    sent, stored, post = catch_up(feed, make_posts(2), 't3_2')
    assert [content for content, _ in sent] == ['Post 2 https://example.com/2']
    assert post['id'] == 't3_2'
    # Already seen, and it stays seen — announcing again doesn't queue it up
    # for the next scheduled check.
    assert stored['seen_ids'].split(',') == ['t3_2', 't3_1']


def test_catching_one_up_marks_it_seen(catch_up):
    feed = make_feed(seen_ids='t3_1')
    _, stored, _ = catch_up(feed, make_posts(3), 't3_3')
    assert stored['seen_ids'].startswith('t3_3,')
    assert 't3_1' in stored['seen_ids']
    # t3_2 was not chosen, so it is still waiting for the next check.
    assert 't3_2' not in stored['seen_ids']


def test_catching_up_on_a_never_read_watch_seeds_the_rest(catch_up):
    # Otherwise announcing one post by hand would make the next scheduled check
    # announce the other twenty-four.
    feed = make_feed(seen_ids=None)
    sent, stored, _ = catch_up(feed, make_posts(4), 't3_2')
    assert len(sent) == 1
    assert sorted(stored['seen_ids'].split(',')) == ['t3_1', 't3_2', 't3_3', 't3_4']


def test_a_post_the_feed_no_longer_carries_says_so(catch_up):
    with pytest.raises(ValueError, match="isn't on the feed"):
        catch_up(make_feed(seen_ids=''), make_posts(2), 't3_99')


def test_a_refused_send_is_not_marked_announced(monkeypatch):
    # The point of the button is to get the post out; if it didn't go out,
    # nothing should be recorded as though it had.
    import discord

    class RefusingChannel(FakeChannel):
        async def send(self, content, allowed_mentions=None):
            raise discord.Forbidden(
                type('R', (), {'status': 403, 'reason': 'Forbidden'})(),
                'no',
            )

    async def fake_fetch(kind, source, **kwargs):
        return make_posts(1)

    stored = {}

    async def fake_record(feed_id, **values):
        stored.update(values)

    monkeypatch.setattr(reddit, 'fetch', fake_fetch)
    monkeypatch.setattr(database, 'record_reddit_read', fake_record)
    bot = FakeBot(FakeGuild(RefusingChannel()))
    with pytest.raises(ValueError):
        asyncio.run(redditfeed.announce_post(bot, make_feed(seen_ids=''), 't3_1'))
    assert stored == {}


# -- where a post lives -----------------------------------------------------

def test_a_profile_post_is_named_for_what_it_is():
    """A post on somebody's own profile lives in `u_Name`, which renders as
    `r/u_Name` — a real place, but one that reads like a bug next to `r/arma`
    and hides the question of whether the watch only sees profile posts."""
    from web.reddit import where
    assert where({'subreddit': 'arma'}) == 'r/arma'
    assert where({'subreddit': 'u_TaskForcePhalanx'}) == (
        "u/TaskForcePhalanx — the author's own profile"
    )
    # A feed that carried only the label, without the term behind it.
    assert where({'subreddit': 'u/TaskForcePhalanx'}).startswith('u/TaskForcePhalanx')
    assert where({'subreddit': ''}) == ''


# -- marking without announcing ---------------------------------------------

@pytest.fixture
def mark(monkeypatch):
    """Mark posts as announced. Returns (what was written, result).

    Nothing is stubbed for Reddit on purpose: marking must not read the feed,
    so a test that reaches the network would fail rather than pass quietly.
    """
    def runner(feed, post_ids, feed_ids=None):
        stored = {}

        async def fake_set(feed_id, seen_ids):
            stored['seen_ids'] = seen_ids

        async def must_not_run(*args, **kwargs):
            raise AssertionError('marking must not read the feed')

        monkeypatch.setattr(database, 'set_reddit_feed_seen', fake_set)
        monkeypatch.setattr(reddit, 'fetch', must_not_run)
        monkeypatch.setattr(reddit, 'fetch_from', must_not_run)
        result = asyncio.run(redditfeed.mark_announced(feed, post_ids, feed_ids))
        return stored, result
    return runner


def ids(count):
    """The ids a page would have listed, newest first."""
    return [f't3_{n}' for n in range(count, 0, -1)]


def test_marking_the_listed_posts_reads_nothing_and_posts_nothing(mark):
    # The flood stopper: a feed that suddenly fills up would otherwise go out
    # three posts at a time until it had all been announced.
    listed = ids(5)
    stored, result = mark(make_feed(seen_ids='t3_1'), listed, listed)
    assert result['marked'] == 4
    assert sorted(stored['seen_ids'].split(',')) == [
        't3_1', 't3_2', 't3_3', 't3_4', 't3_5'
    ]


def test_ticking_some_leaves_the_others_queued(mark):
    stored, result = mark(make_feed(seen_ids='t3_1'), ['t3_3'], ids(4))
    assert result['marked'] == 1
    assert stored['seen_ids'].split(',') == ['t3_3', 't3_1']


def test_marking_something_already_marked_changes_nothing(mark):
    stored, result = mark(make_feed(seen_ids='t3_2,t3_1'), ['t3_2'], ids(2))
    assert result['marked'] == 0
    assert stored['seen_ids'].split(',') == ['t3_2', 't3_1']


def test_marking_on_a_never_read_watch_marks_everything_listed(mark):
    # Its first check would have done exactly that and announced nothing, so
    # this changes no outcome — while marking only the ticked post would leave
    # the rest of the feed queued up, which is the flood being prevented.
    stored, result = mark(make_feed(seen_ids=None), ['t3_2'], ids(3))
    assert result['seeded'] is True
    assert sorted(stored['seen_ids'].split(',')) == ['t3_1', 't3_2', 't3_3']


def test_ticking_nothing_says_so(mark):
    with pytest.raises(ValueError, match='Nothing was ticked'):
        mark(make_feed(seen_ids='t3_1'), [], ids(2))


def test_marking_keeps_the_remembered_window_capped(mark):
    feed = make_feed(seen_ids=','.join(f't3_old{n}' for n in range(redditfeed.MAX_SEEN)))
    listed = ids(3)
    stored, _ = mark(feed, listed, listed)
    assert len(stored['seen_ids'].split(',')) == redditfeed.MAX_SEEN


# -- what the page read, reused by its own buttons --------------------------

def test_announcing_reuses_what_the_page_read(monkeypatch):
    """Pressing a button on the catch-up page is the second half of one
    interaction; going back to Reddit for it fails at the one moment it
    matters, when Reddit is refusing us."""
    channel = FakeChannel()
    bot = FakeBot(FakeGuild(channel))
    feed = make_feed(seen_ids='')
    posts = make_posts(2)

    async def must_not_run(*args, **kwargs):
        raise AssertionError('the page already read this')

    async def fake_set(feed_id, seen_ids):
        pass

    async def fake_record(feed_id, **values):
        pass

    redditfeed.remember_posts(feed, posts)
    monkeypatch.setattr(reddit, 'fetch', must_not_run)
    monkeypatch.setattr(database, 'record_reddit_read', fake_record)
    monkeypatch.setattr(database, 'set_reddit_feed_seen', fake_set)

    post = asyncio.run(redditfeed.announce_post(bot, feed, 't3_2'))
    assert post['title'] == 'Post 2'
    assert len(channel.sent) == 1


def test_a_stale_read_is_not_reused(monkeypatch):
    feed = make_feed(seen_ids='')
    redditfeed.remember_posts(feed, make_posts(1))
    # Bound before patching: `redditfeed.time` is the module itself, so the
    # replacement would otherwise call itself.
    real = time.monotonic
    monkeypatch.setattr(redditfeed.time, 'monotonic',
                        lambda: real() + redditfeed.RECENT_TTL + 1)
    assert redditfeed.recall_posts(feed) is None
