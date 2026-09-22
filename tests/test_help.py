"""`utils/help.py` is the only copy of the how-tos, so this suite guards two
different things.

The first is the usual one: the module's own promises — unique keys, resolvable
cross-references, a Markdown render that splices back into the README where it
is meant to.

The second is the reason the module exists. `test_every_slash_command_is_
documented` reads the command names straight out of `cogs/` and fails when one
of them has no how-to. Adding a command to the bot and forgetting to write it up
is therefore a red test rather than something somebody notices a year later — in
a repository whose documentation is otherwise only as current as the last person
to remember it, that is the whole point.

Reading the cogs with a regex rather than importing them is deliberate:
`cogs/slots.py` pulls in discord.py, asyncpg and gspread, and this suite is
stdlib-only like everything else here.
"""

import re
from pathlib import Path

import pytest

from utils import help as helpmod


ROOT = Path(__file__).resolve().parent.parent
COMMAND_RE = re.compile(r"""app_commands\.command\(\s*name=['"]([^'"]+)['"]""")
NAV_KEY_RE = re.compile(r"""_item\(\s*['"]([a-z]+)['"]""")


def shipped_commands() -> set[str]:
    """Every slash command the cogs actually register."""
    names: set[str] = set()
    for path in sorted((ROOT / 'cogs').glob('*.py')):
        names |= set(COMMAND_RE.findall(path.read_text(encoding='utf-8')))
    return names


def nav_page_keys() -> set[str]:
    """Every page key `web/nav.py` builds a tab for."""
    source = (ROOT / 'web' / 'nav.py').read_text(encoding='utf-8')
    return set(NAV_KEY_RE.findall(source))


# --- the coverage guarantee ----------------------------------------------

def test_the_cogs_actually_register_commands():
    """A regex that silently matches nothing would make the next test pass
    for the wrong reason, which is the one failure mode a coverage test has."""
    assert len(shipped_commands()) > 20


def test_every_slash_command_is_documented():
    missing = shipped_commands() - helpmod.commands()
    assert not missing, f"slash commands with no how-to: {sorted(missing)}"


def test_no_topic_documents_a_command_that_does_not_exist():
    """The other direction: a command that was renamed or removed leaves a
    how-to telling people to run something that no longer works."""
    stale = helpmod.commands() - shipped_commands()
    assert not stale, f"how-tos for unknown commands: {sorted(stale)}"


def test_every_web_page_has_a_how_to():
    """Every tab in the nav can answer "how do I use this?" with its own `?`."""
    documented = {item.page for item in helpmod.TOPICS if item.page}
    missing = nav_page_keys() - documented
    assert not missing, f"nav pages with no how-to: {sorted(missing)}"


def test_topics_only_point_at_pages_the_nav_has():
    unknown = {item.page for item in helpmod.TOPICS if item.page} - nav_page_keys()
    assert not unknown, f"how-tos for unknown pages: {sorted(unknown)}"


# --- the module's own promises -------------------------------------------

def test_keys_are_unique():
    keys = [item.key for item in helpmod.TOPICS]
    assert len(keys) == len(set(keys))


def test_keys_are_url_safe():
    for item in helpmod.TOPICS:
        assert re.fullmatch(r'[a-z0-9-]+', item.key), item.key


def test_every_topic_is_in_a_real_group():
    known = {group.key for group in helpmod.GROUPS}
    for item in helpmod.TOPICS:
        assert item.group in known, item.key


def test_every_group_has_topics():
    """An empty shelf renders as a heading with nothing under it."""
    for shelf in helpmod.catalog():
        assert shelf['topics'], shelf['group'].key


def test_related_keys_all_resolve():
    known = {item.key for item in helpmod.TOPICS}
    for item in helpmod.TOPICS:
        for key in item.related:
            assert key in known, f"{item.key} points at unknown {key}"


def test_no_topic_points_at_itself():
    for item in helpmod.TOPICS:
        assert item.key not in item.related, item.key


def test_every_topic_has_a_summary_and_steps():
    for item in helpmod.TOPICS:
        assert item.summary.strip(), item.key
        assert item.steps, item.key


def test_topic_lookup():
    assert helpmod.topic('welcome-message').title == 'Welcome new members'
    assert helpmod.topic('no-such-topic') is None


def test_for_page_finds_the_pages_how_tos():
    keys = {item.key for item in helpmod.for_page('logs')}
    assert 'welcome-message' in keys
    assert helpmod.for_page('nothing-here') == []


def test_related_drops_an_unknown_key_rather_than_raising():
    broken = helpmod.Topic(
        key='x', title='X', group='server', audience=helpmod.ADMIN,
        summary='s', steps=('a',), related=('welcome-message', 'nope'))
    assert [item.key for item in helpmod.related(broken)] == ['welcome-message']


# --- search ---------------------------------------------------------------

def test_search_matches_every_word():
    assert helpmod.search('welcome')
    assert not helpmod.search('welcome zzzzz')


def test_search_finds_a_command_by_name():
    keys = {item.key for item in helpmod.search('/purge')}
    assert 'purge-messages' in keys


def test_search_prefers_a_title_hit():
    hits = helpmod.search('welcome')
    assert hits[0].key == 'welcome-message'


def test_empty_search_finds_nothing():
    assert helpmod.search('   ') == []


# --- the README half ------------------------------------------------------

def test_markdown_holds_every_topic():
    text = helpmod.render_markdown()
    for item in helpmod.TOPICS:
        assert f"#### {item.title}" in text, item.key


def test_markdown_is_wrapped_in_the_markers():
    text = helpmod.render_markdown()
    assert text.startswith(helpmod.README_START)
    assert text.rstrip().endswith(helpmod.README_END)


def test_splice_replaces_only_the_marked_section():
    readme = f"before\n\n{helpmod.README_START}\nSTALE_SECTION\n{helpmod.README_END}\n\nafter\n"
    out = helpmod.splice_readme(readme)
    assert out.startswith('before\n')
    assert out.endswith('\n\nafter\n')
    assert 'STALE_SECTION' not in out
    assert '#### Welcome new members' in out


def test_splice_is_idempotent():
    readme = f"a\n{helpmod.README_START}\n{helpmod.README_END}\nb\n"
    once = helpmod.splice_readme(readme)
    assert helpmod.splice_readme(once) == once


def test_splice_refuses_a_readme_without_markers():
    with pytest.raises(ValueError):
        helpmod.splice_readme('no markers here')


def test_splice_refuses_markers_the_wrong_way_round():
    readme = f"{helpmod.README_END}\nx\n{helpmod.README_START}\n"
    with pytest.raises(ValueError):
        helpmod.splice_readme(readme)


def test_anchors_are_unique_so_the_contents_links_land():
    anchors = [helpmod._anchor(item) for item in helpmod.TOPICS]
    assert len(anchors) == len(set(anchors))


# --- the inline markup ----------------------------------------------------

def test_inline_html_renders_code_bold_and_italic():
    assert helpmod.inline_html('`/purge`') == '<code>/purge</code>'
    assert helpmod.inline_html('**go**') == '<strong>go</strong>'
    assert helpmod.inline_html('*Steam*') == '<em>Steam</em>'


def test_inline_html_escapes_before_it_marks_up():
    out = helpmod.inline_html('`<Insert Name>` & **co**')
    assert out == '<code>&lt;Insert Name&gt;</code> &amp; <strong>co</strong>'
    assert '<Insert' not in out


def test_inline_html_leaves_a_script_tag_inert():
    assert '<script' not in helpmod.inline_html('<script>alert(1)</script>')


def test_code_spans_keep_their_asterisks():
    assert helpmod.inline_html('`mil_*`') == '<code>mil_*</code>'


def test_bold_wins_over_italic():
    assert helpmod.inline_html('**a**') == '<strong>a</strong>'


def test_plain_text_is_untouched():
    assert helpmod.inline_html('just words') == 'just words'


def test_every_topic_renders_without_stray_markup():
    """An unbalanced ** or ` in a topic shows as a literal asterisk on the
    page, which is the kind of thing nobody notices in 41 how-tos."""
    for item in helpmod.TOPICS:
        for line in (item.summary, item.where, *item.steps, *item.notes):
            rendered = helpmod.inline_html(line)
            assert '**' not in rendered, (item.key, line)
            assert '`' not in rendered, (item.key, line)
