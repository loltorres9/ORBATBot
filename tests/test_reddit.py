"""`utils/reddit.py` is pure and imports nothing but the standard library, which
makes it cheap to test — and worth testing, because the two things it gets wrong
are silent: a name it can't read means a watch that never fires, and a template
it mangles goes out to a channel."""

import asyncio

from utils import reddit


# -- names ------------------------------------------------------------------

def test_a_bare_name_is_taken_as_is():
    assert reddit.clean_source('TaskForcePhalanx') == 'TaskForcePhalanx'


def test_the_prefixed_forms_are_accepted():
    for raw in ('u/TaskForcePhalanx', '/u/TaskForcePhalanx',
                'user/TaskForcePhalanx', '/user/TaskForcePhalanx/'):
        assert reddit.clean_source(raw) == 'TaskForcePhalanx', raw


def test_a_pasted_url_is_accepted():
    assert reddit.clean_source(
        'https://www.reddit.com/user/TaskForcePhalanx/'
    ) == 'TaskForcePhalanx'
    assert reddit.clean_source('https://old.reddit.com/r/arma/new/') == 'arma'


def test_something_that_is_not_a_name_is_rejected():
    for raw in ('', '   ', 'not a name', 'https://example.com/', 'a'):
        assert reddit.clean_source(raw) == '', raw


def test_the_url_wins_over_the_rest_of_the_line():
    # Someone pastes the whole address bar, trailing query string and all.
    assert reddit.clean_source(
        'https://www.reddit.com/r/arma/comments/abc/some_title/?utm_source=share'
    ) == 'arma'


# -- feed addresses ---------------------------------------------------------

def test_each_kind_has_its_own_feed():
    assert reddit.feed_url('user', 'Someone').endswith('/user/Someone/submitted.rss')
    assert reddit.feed_url('subreddit', 'arma').endswith('/r/arma/new.rss')


def test_an_unknown_kind_falls_back_to_a_user():
    assert '/user/' in reddit.feed_url('nonsense', 'Someone')


# -- parsing ----------------------------------------------------------------

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>posts by u/TaskForcePhalanx</title>
  <entry>
    <author><name>/u/TaskForcePhalanx</name></author>
    <category term="arma" label="r/arma"/>
    <id>t3_newest</id>
    <link href="https://www.reddit.com/r/arma/comments/newest/title/"/>
    <published>2026-01-02T10:00:00+00:00</published>
    <updated>2026-01-03T11:00:00+00:00</updated>
    <title>Operation Nightfall — sign-ups open</title>
  </entry>
  <entry>
    <author><name>/u/TaskForcePhalanx</name></author>
    <category term="arma" label="r/arma"/>
    <id>t3_older</id>
    <link href="https://www.reddit.com/r/arma/comments/older/title/"/>
    <updated>2026-01-01T09:00:00+00:00</updated>
    <title>After action report</title>
  </entry>
</feed>
"""


def test_entries_come_back_newest_first():
    posts = reddit.parse_feed(FEED)
    assert [p['id'] for p in posts] == ['t3_newest', 't3_older']


def test_an_entry_carries_what_a_template_needs():
    post = reddit.parse_feed(FEED)[0]
    assert post['title'] == 'Operation Nightfall — sign-ups open'
    assert post['url'].endswith('/comments/newest/title/')
    assert post['author'] == 'TaskForcePhalanx'
    assert post['subreddit'] == 'arma'


def test_published_beats_updated():
    # An edit moves `updated` and leaves `published` alone; treating an edit as
    # a new post would announce the same thing twice.
    assert reddit.parse_feed(FEED)[0]['published'].day == 2


def test_updated_is_used_when_there_is_no_published():
    assert reddit.parse_feed(FEED)[1]['published'].day == 1


def test_an_entry_without_an_id_or_a_link_is_skipped_not_fatal():
    broken = FEED.replace('<id>t3_older</id>', '')
    assert [p['id'] for p in reddit.parse_feed(broken)] == ['t3_newest']


def test_something_that_is_not_a_feed_raises():
    try:
        reddit.parse_feed('<html>404</html>oops')
    except reddit.FeedError:
        return
    raise AssertionError('a broken feed should raise FeedError')


# -- rendering --------------------------------------------------------------

def test_every_placeholder_is_substituted():
    post = reddit.parse_feed(FEED)[0]
    text = reddit.render('{author} · {subreddit} · {title} · {url}', post)
    assert text == (
        'TaskForcePhalanx · arma · Operation Nightfall — sign-ups open · '
        + post['url']
    )


def test_an_empty_template_falls_back_to_the_default():
    post = reddit.parse_feed(FEED)[0]
    assert reddit.render('   ', post) == reddit.render(reddit.DEFAULT_TEMPLATE, post)


def test_a_stray_brace_is_left_alone_rather_than_raising():
    # A template is text somebody typed, not a format string.
    post = reddit.parse_feed(FEED)[0]
    assert reddit.render('100% {sure} {{tonight}} — {url}', post) == (
        f"100% {{sure}} {{{{tonight}}}} — {post['url']}"
    )


def test_a_very_long_title_is_trimmed():
    post = dict(reddit.parse_feed(FEED)[0], title='x' * 500)
    assert len(reddit.render('{title}', post)) == reddit.MAX_TITLE


# -- being refused ----------------------------------------------------------
#
# Reddit turns a hosting provider's address away with 429 whatever the
# User-Agent says, so how a refusal is handled matters more than it looks: the
# second host is what usually gets through, and standing down afterwards is what
# keeps a passing throttle from becoming a standing one.

class FakeResponse:
    def __init__(self, status, body='', headers=None):
        self.status = status
        self.headers = headers or {}
        self._body = body.encode() if isinstance(body, str) else body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def read(self):
        # Bytes, like aiohttp's — the XML declaration is what says how to
        # decode them, so `fetch()` deliberately never decodes them itself.
        return self._body


class FakeSession:
    def __init__(self, answers):
        self.answers = list(answers)
        self.urls = []

    def get(self, url, headers=None):
        self.urls.append(url)
        return self.answers.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def fetch_with(monkeypatch, *answers):
    """Run fetch() against canned responses. Returns (result-or-error, urls)."""
    import aiohttp
    session = FakeSession(answers)
    monkeypatch.setattr(aiohttp, 'ClientSession', lambda **kwargs: session)
    try:
        return asyncio.run(reddit.fetch('user', 'Someone')), session.urls
    except reddit.FeedError as e:
        return e, session.urls


def test_a_refusal_is_retried_on_the_other_host(monkeypatch):
    result, urls = fetch_with(
        monkeypatch, FakeResponse(429), FakeResponse(200, FEED)
    )
    assert [p['id'] for p in result] == ['t3_newest', 't3_older']
    assert urls == [
        'https://www.reddit.com/user/Someone/submitted.rss',
        'https://old.reddit.com/user/Someone/submitted.rss',
    ]


def test_only_one_request_when_the_first_host_answers(monkeypatch):
    _, urls = fetch_with(monkeypatch, FakeResponse(200, FEED))
    assert len(urls) == 1


def test_refused_by_both_hosts_says_how_long_to_wait(monkeypatch):
    error, urls = fetch_with(
        monkeypatch,
        FakeResponse(429, headers={'Retry-After': '3600'}),
        FakeResponse(429),
    )
    assert isinstance(error, reddit.RateLimited)
    assert len(urls) == 2
    # The longest wait either host asked for, so the one host that named one
    # isn't undercut by the other's silence.
    assert error.retry_after == 3600


def test_a_refusal_without_a_wait_gets_the_default(monkeypatch):
    error, _ = fetch_with(monkeypatch, FakeResponse(429), FakeResponse(429))
    assert error.retry_after == reddit.DEFAULT_RETRY_AFTER


def test_an_absurd_wait_is_bounded(monkeypatch):
    error, _ = fetch_with(
        monkeypatch,
        FakeResponse(429, headers={'Retry-After': '999999'}),
        FakeResponse(429),
    )
    assert error.retry_after == reddit.MAX_RETRY_AFTER


def test_a_missing_feed_is_not_asked_about_twice(monkeypatch):
    # A 404 says the same thing from either host — only a refusal aimed at us
    # is worth asking the other one about.
    error, urls = fetch_with(monkeypatch, FakeResponse(404), FakeResponse(200, FEED))
    assert isinstance(error, reddit.FeedError)
    assert not isinstance(error, reddit.RateLimited)
    assert len(urls) == 1


def test_a_post_on_the_author_s_own_profile_names_a_real_place():
    """`/user/X/submitted.rss` carries everything X submits, profile posts
    included — and those live in r/u_X, which is where the default template's
    `r/{subreddit}` has to point."""
    profile = FEED.replace(
        '<category term="arma" label="r/arma"/>',
        '<category term="u_TaskForcePhalanx" label="u/TaskForcePhalanx"/>',
        1,
    )
    post = reddit.parse_feed(profile)[0]
    assert post['subreddit'] == 'u_TaskForcePhalanx'
    assert 'r/u_TaskForcePhalanx' in reddit.render('r/{subreddit}', post)


# -- a body that isn't a feed -----------------------------------------------
#
# "That didn't come back as a feed" is the least actionable thing this module
# can say, so what it says next matters: the fragment it choked on tells a
# mis-encoded byte, a stray character and a page-instead-of-a-feed apart at a
# glance, none of which the line and column do.

def test_bytes_are_parsed_by_the_declaration_they_carry():
    # An XML document says how to decode itself, and that beats the HTTP header
    # or any guess made from the bytes — which is why fetch() never decodes.
    post = reddit.parse_feed(FEED.encode('utf-8'))[0]
    assert post['title'] == 'Operation Nightfall — sign-ups open'


def test_a_character_xml_forbids_is_dropped_rather_than_fatal():
    # XML 1.0 has no way to write a control character, so no valid feed can
    # contain one — and one stray byte in a title must not cost the whole feed.
    broken = FEED.replace('Operation Nightfall', 'Operation \x0cNightfall')
    assert reddit.parse_feed(broken)[0]['title'].startswith('Operation Nightfall')


def test_a_body_that_wont_parse_says_what_it_choked_on():
    try:
        reddit.parse_feed(b'<?xml version="1.0"?>\n<feed><title>Tom & Jerry</title></feed>')
    except reddit.NotAFeed as e:
        assert 'Tom & Jerry' in str(e)
        return
    raise AssertionError('a body that is not XML should raise NotAFeed')


def test_a_web_page_is_a_refusal_in_disguise():
    # Reddit serves its block page with a 200, so it has to be read as the 429
    # it means: the other host is tried, and the watch stands down.
    try:
        reddit.parse_feed('<!DOCTYPE html>\n<html><body>blocked</body></html>')
    except reddit.RateLimited:
        return
    raise AssertionError('an HTML body should be treated as a refusal')


def test_a_body_that_isnt_a_feed_is_asked_of_the_other_host_too(monkeypatch):
    result, urls = fetch_with(
        monkeypatch,
        FakeResponse(200, 'not xml at all'),
        FakeResponse(200, FEED),
    )
    assert [p['id'] for p in result] == ['t3_newest', 't3_older']
    assert len(urls) == 2


def test_both_hosts_serving_rubbish_is_reported_not_swallowed(monkeypatch):
    error, urls = fetch_with(
        monkeypatch, FakeResponse(200, 'nope'), FakeResponse(200, 'nope')
    )
    assert isinstance(error, reddit.NotAFeed)
    assert len(urls) == 2
