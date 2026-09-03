"""Managing the Reddit watches from the browser.

Like the rest of `web/`, this owns no rules of its own: it translates form
fields, validates them, and calls into `cogs/redditfeed.py` and
`utils/database.py`. A `ValueError` raised here is a message for the person at
the form.

What a watch may say is the one thing this module is opinionated about. The
template help offered on the page is announcement wording — "new post", "have a
read", "join the discussion". It is not wording that asks the channel to go and
upvote: that is vote manipulation under Reddit's content policy, and it gets the
poster and the people who act on it banned, so it isn't something to offer as a
convenience.
"""

from datetime import datetime

import discord
from discord.ext import commands

from cogs.redditfeed import (
    announce_post,
    build_message,
    check_feed,
    mention_ids,
)
from utils import database, reddit
from web.guilds import postable_channels

MAX_MENTION_ROLES = 10
MAX_MENTION_USERS = 10

# What a template may say, offered next to the field. Each is a starting point
# somebody edits, and every one of them is an announcement.
TEMPLATE_EXAMPLES = (
    ('Plain', 'New post by u/{author}\n**{title}**\n{url}'),
    ('With a nudge to discuss', 'New on r/{subreddit} — worth a read and a comment:\n'
                                '**{title}**\n{url}'),
    ('Short', '**{title}**\n{url}'),
)


def _channel(guild: discord.Guild, raw):
    """The channel to announce in. Empty is allowed — a watch with no channel is
    simply idle, which is how one is parked without losing its text."""
    raw = (raw or '').strip()
    if not raw:
        return None
    channel = guild.get_channel(int(raw)) if raw.isdigit() else None
    if channel is None or channel not in postable_channels(guild):
        raise ValueError("I can't post in that channel — pick another one.")
    return str(channel.id)


def _roles(guild: discord.Guild, role_ids) -> tuple:
    """The ping-role checkboxes as roles, dropping any that have gone."""
    roles, unknown = [], []
    for role_id in role_ids or []:
        role = guild.get_role(int(role_id)) if str(role_id).isdigit() else None
        if role is None:
            unknown.append(str(role_id))
        elif role not in roles:
            roles.append(role)
    if len(roles) > MAX_MENTION_ROLES:
        raise ValueError(f"That's more than {MAX_MENTION_ROLES} ping roles.")
    return roles, unknown


async def _users(guild: discord.Guild, raw: str) -> tuple:
    """People to ping, out of a free-text field.

    A text box rather than a dropdown for the same reason the assign form on the
    slot queue uses one: `Intents.default()` cannot list a guild's members, so
    there is no list to offer. Ids and `<@…>` mentions are both accepted —
    right-click a member in Discord and *Copy User ID* gives the first, typing
    the mention and copying the text gives the second.
    """
    tokens = [
        part.strip('<@!>').strip()
        for part in (raw or '').replace(',', ' ').split()
    ]
    ids, unknown = [], []
    for token in tokens:
        if not token.isdigit():
            unknown.append(token)
            continue
        if token in ids:
            continue
        if len(ids) >= MAX_MENTION_USERS:
            raise ValueError(f"That's more than {MAX_MENTION_USERS} people to ping.")
        member = guild.get_member(int(token))
        if member is None:
            try:
                member = await guild.fetch_member(int(token))
            except discord.NotFound:
                unknown.append(token)
                continue
            except (discord.Forbidden, discord.HTTPException):
                # Couldn't check. Keep it: an id the admin pasted is more likely
                # right than a REST call is likely to be working.
                pass
        ids.append(token)
    return ids, unknown


def _template(raw) -> str:
    text = (raw or '').strip()
    if not text:
        return None                       # NULL means reddit.DEFAULT_TEMPLATE
    if len(text) > reddit.MAX_TEMPLATE:
        raise ValueError(
            f"The message is longer than {reddit.MAX_TEMPLATE} characters."
        )
    return text


async def read_form(guild: discord.Guild, form: dict) -> tuple:
    """(values to store, warnings). Raises `ValueError` on anything unusable."""
    kind = reddit.clean_kind(form.get('kind'))
    source = reddit.clean_source(form.get('source'))
    if not source:
        raise ValueError(
            "That isn't a Reddit name I can read. Give the name itself "
            "(TaskForcePhalanx), the u/ or r/ form, or paste the page's URL."
        )

    roles, unknown_roles = _roles(guild, form.get('mention_ids'))
    users, unknown_users = await _users(guild, form.get('mention_users'))

    warnings = []
    not_pingable = [r.name for r in roles if not r.mentionable]
    if not_pingable:
        warnings.append(
            f"{', '.join(not_pingable)} isn't mentionable, so it shows as text "
            "without notifying anyone unless the bot has Mention All Roles."
        )
    if unknown_roles:
        warnings.append(f"{len(unknown_roles)} ping role(s) no longer exist and were dropped.")
    if unknown_users:
        warnings.append(
            "Dropped, because it isn't a user id or a member of this server: "
            + ', '.join(unknown_users[:5])
        )

    values = {
        'kind': kind,
        'source': source,
        'channel_id': _channel(guild, form.get('channel_id')),
        'template': _template(form.get('template')),
        'mention_role_id': ','.join(str(r.id) for r in roles) or None,
        'mention_user_id': ','.join(users) or None,
        'enabled': 1 if form.get('enabled') else 0,
    }
    if values['channel_id'] is None and values['enabled']:
        raise ValueError("Pick a channel to announce in, or switch the watch off.")
    return values, warnings


async def create(guild: discord.Guild, member: discord.Member, form: dict) -> tuple:
    """Add a watch. Returns (id, warnings)."""
    values, warnings = await read_form(guild, form)
    feed_id = await database.create_reddit_feed(
        str(guild.id), values,
        created_by=str(member.id), created_by_name=member.display_name,
    )
    return feed_id, warnings


async def save(guild: discord.Guild, feed, form: dict) -> list:
    """Change a watch. Returns the warnings.

    Pointing it at a different source resets what has been announced, so the new
    one is seeded on its next read instead of announcing its whole front page.
    """
    values, warnings = await read_form(guild, form)
    moved = (values['kind'] != feed['kind']
             or values['source'].lower() != (feed['source'] or '').lower())
    await database.save_reddit_feed(feed['id'], values)
    if moved:
        await database.reset_reddit_feed_seen(feed['id'])
        warnings.append(
            "It now points somewhere else, so the next check starts from that "
            "feed's current posts rather than announcing its history."
        )
    return warnings


async def check_now(bot: commands.Bot, feed) -> str:
    """Run the check the loop would run. Returns what to flash."""
    if not feed['enabled']:
        # The button does announce, so a watch somebody has deliberately parked
        # must not go out through it either.
        raise ValueError("This watch is switched off. Turn it on first.")
    try:
        result = await check_feed(bot, feed)
    except reddit.RateLimited as e:
        # Worth spelling out, because the obvious reading of "rate-limited" is
        # "we ask too often" — which is not what this is.
        raise ValueError(
            f"{e} Both reddit.com and old.reddit.com turned this server's "
            "address away, so it is about where the bot is hosted rather than "
            "about the feed or how often it is checked. Trying again in "
            f"{e.retry_after // 60} minutes."
        )
    except (reddit.FeedError, ValueError) as e:
        # A feed that couldn't be read is a message for the person who pressed
        # the button, like everything else `web/` raises.
        raise ValueError(str(e))

    if result.get('seeded'):
        return (f"Read {result['seeded']} post(s) and noted them as already seen — "
                "from now on only new ones are announced.")
    parts = []
    if result['posted']:
        parts.append(f"Announced {result['posted']} new post(s).")
    else:
        parts.append("Nothing new since the last check.")
    if result.get('waiting'):
        parts.append(f"{result['waiting']} more will follow on the next check.")
    if result.get('error'):
        parts.append(result['error'])
    return ' '.join(parts)


async def preview(feed) -> dict:
    """The newest post and the message it would produce — without posting it or
    marking anything as seen, so it is safe to press while working on the text."""
    posts = await reddit.fetch(feed['kind'], feed['source'])
    if not posts:
        raise ValueError(
            f"{reddit.kind_prefix(feed['kind'])}{feed['source']} has no posts to show."
        )
    post = posts[0]
    return {'post': post, 'message': build_message(feed, post)}


async def recent(feed) -> list:
    """The feed's posts, each saying whether it has been announced.

    What the catch-up page is built from: the bot can only announce what the
    feed still lists, so this is exactly the set of posts that can be caught up.
    """
    posts = await reddit.fetch(feed['kind'], feed['source'])
    seen = {part for part in (feed['seen_ids'] or '').split(',') if part}
    never_read = feed['seen_ids'] is None
    return [
        {**post,
         # A watch that has never been read has announced nothing, whatever the
         # empty seen set would otherwise imply.
         'announced': not never_read and post['id'] in seen,
         'message': build_message(feed, post)}
        for post in posts
    ]


async def catch_up(bot: commands.Bot, feed, post_id: str) -> str:
    """Announce one post by hand. Returns what to flash."""
    if not (post_id or '').strip():
        raise ValueError("No post was chosen.")
    try:
        post = await announce_post(bot, feed, post_id.strip())
    except reddit.RateLimited as e:
        raise ValueError(
            f"{e} The post can't be read to announce it — try again in "
            f"{e.retry_after // 60} minutes."
        )
    except reddit.FeedError as e:
        raise ValueError(str(e))
    return f"Announced “{post['title'][:80]}”."


def _future(when):
    """A stored time, if it hasn't passed yet."""
    if when is None:
        return None
    return when if when > datetime.utcnow() else None


def view_models(guild: discord.Guild, feeds) -> list:
    """One row per watch, with everything the list page shows."""
    rows = []
    for feed in feeds:
        channel = (guild.get_channel(int(feed['channel_id']))
                   if feed['channel_id'] else None)
        roles = [guild.get_role(int(i)) for i in mention_ids(feed, 'mention_role_id')]
        rows.append({
            'feed': feed,
            'label': f"{reddit.kind_prefix(feed['kind'])}{feed['source']}",
            'url': reddit.page_url(feed['kind'], feed['source']),
            'channel': channel,
            'lost_channel': bool(feed['channel_id']) and channel is None,
            'roles': [r.name for r in roles if r],
            'people': len(mention_ids(feed, 'mention_user_id')),
            'never_read': feed['seen_ids'] is None,
            # Set while a refusal is being waited out, so the list says why
            # nothing is happening rather than looking simply broken.
            'paused_until': _future(feed['retry_at']),
        })
    return rows
