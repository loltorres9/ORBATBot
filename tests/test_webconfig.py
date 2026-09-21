"""`web/config.py` reads the environment and nothing else, which is what makes
it testable — and worth testing, because two of the things it decides are only
noticed when they are wrong: an OAuth callback that sends somebody to the other
domain, and a `Secure` cookie set over http that never comes back."""

from web import config as webconfig


class _URL:
    def __init__(self, scheme='https'):
        self.scheme = scheme

    def __str__(self):
        return 'http://testserver/'


class _Request:
    """As much of a request as the config reads: headers and a scheme."""

    def __init__(self, host=None, forwarded=None, proto=None, scheme='https'):
        self.headers = {}
        if host:
            self.headers['host'] = host
        if forwarded:
            self.headers['x-forwarded-host'] = forwarded
        if proto:
            self.headers['x-forwarded-proto'] = proto
        self.url = _URL(scheme)
        self.base_url = 'http://testserver/'


def _config(raw):
    return webconfig.WebConfig(origins=webconfig._origins(raw),
                               base_url=(webconfig._origins(raw) or [''])[0])


def test_one_origin_reads_as_it_always_did():
    config = _config('https://bot.example.com')
    assert config.base_url == 'https://bot.example.com'
    assert config.redirect_uri(_Request(host='bot.example.com')) == (
        'https://bot.example.com/auth/callback')


def test_a_trailing_slash_and_stray_spacing_are_taken_off():
    assert webconfig._origins('  https://a.example/ ,, https://b.example  ') == (
        'https://a.example', 'https://b.example')
    assert webconfig._origins('https://a.example https://a.example') == (
        'https://a.example',)
    assert webconfig._origins(None) == ()


def test_each_name_keeps_whoever_is_looking_at_it():
    """The callback and every link stay on the domain the request came in on."""
    config = _config('https://old.example.com https://new.example.com')
    assert config.redirect_uri(_Request(host='new.example.com')) == (
        'https://new.example.com/auth/callback')
    assert config.redirect_uri(_Request(host='old.example.com')) == (
        'https://old.example.com/auth/callback')


def test_the_proxys_forwarded_host_wins_over_the_internal_one():
    config = _config('https://new.example.com')
    request = _Request(host='orbat.railway.internal',
                       forwarded='new.example.com')
    assert config.request_origin(request) == 'https://new.example.com'


def test_a_host_nobody_configured_is_not_echoed_back():
    """The Host header belongs to whoever sent the request, so an unknown one
    falls back to the canonical origin rather than into a redirect or a link."""
    config = _config('https://bot.example.com')
    request = _Request(host='attacker.example')
    assert config.request_origin(request) == 'https://bot.example.com'
    assert config.redirect_uri(request) == 'https://bot.example.com/auth/callback'


def test_secure_follows_the_origin_that_was_asked_for():
    """A Secure cookie set over http never comes back, so it is per request."""
    config = _config('https://bot.example.com http://127.0.0.1:8080')
    assert config.cookie_secure_for(_Request(host='bot.example.com')) is True
    assert config.cookie_secure_for(_Request(host='127.0.0.1:8080')) is False
    # With no request to read, the canonical origin decides.
    assert config.cookie_secure is True


def test_nothing_configured_still_derives_from_the_request():
    config = _config('')
    request = _Request(host='127.0.0.1:8080', proto='http')
    assert config.request_origin(request) == 'http://127.0.0.1:8080'
    assert config.cookie_secure_for(request) is False
