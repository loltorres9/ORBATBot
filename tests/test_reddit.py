"""`utils/reddit.py` is pure and imports nothing but the standard library, which
makes it cheap to test — and worth testing, because the two things it gets wrong
are silent: a name it can't read means a watch that never fires, and a template
it mangles goes out to a channel."""

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
