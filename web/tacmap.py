"""The tactical map behind the browser pages.

What a map *is* — the symbols, the document, the drawing — lives in
`utils/tacmap.py`, which imports nothing. This module is the part that knows
about guilds, share links and Discord: it translates form fields and saved
documents into that library's calls and back, and a `ValueError` raised here is
a message meant for the person looking at the page.

### The share link is the whole credential

`/m/{token}` is reachable without signing in, which is the point — the people
who need tonight's plan are in Discord, not necessarily on this site, and
plan-ops-style links are what everyone already expects. So the token is long,
random and revocable: regenerating it is what un-shares a map that has been
forwarded further than intended, and `share_mode` decides whether that link may
draw or only look.
"""

import secrets

import discord

from utils import database, tacmap
from web.guilds import postable_channels

MAX_NAME = 120
MAX_DESCRIPTION = 300

SHARE_MODES = {
    'off': 'Not shared — only people signed in here can open it',
    'view': 'Anyone with the link can look at it',
    'edit': 'Anyone with the link can draw on it',
}

# Long enough that guessing one is not a thing anybody attempts.
TOKEN_BYTES = 24


def _clean(raw, limit: int, what: str) -> str:
    value = (raw or '').strip()
    if len(value) > limit:
        raise ValueError(f'{what} is too long — {limit} characters at most.')
    return value


def _name(raw) -> str:
    name = _clean(raw, MAX_NAME, 'The name')
    if not name:
        raise ValueError('Give the map a name.')
    return name


async def create(guild, member, name, description, background) -> int:
    doc = tacmap.blank_doc()
    doc['background']['url'] = (background or '').strip()
    checked = tacmap.parse(doc)
    return await database.create_tac_map(
        str(guild.id), _name(name),
        _clean(description, MAX_DESCRIPTION, 'The description') or None,
        tacmap.dumps(checked.doc), str(member.id), member.display_name,
    )


async def rename(record, name, description) -> None:
    await database.rename_tac_map(
        record['id'], _name(name),
        _clean(description, MAX_DESCRIPTION, 'The description') or None,
    )


async def duplicate(record, member, name) -> int:
    return await database.duplicate_tac_map(
        record['id'], _name(name), str(member.id), member.display_name
    )


def load(record) -> dict:
    """The stored document, checked on the way out as well as in.

    A row written by a newer version, or by hand, is still only allowed to
    produce something this renderer is willing to draw — the parse is what makes
    that true, so nothing else has to trust the column.
    """
    return tacmap.parse(record['doc']).doc


async def save(record, raw_doc: str, member_name: str = None) -> list:
    """Store what the editor sent. Returns the notes worth telling the person."""
    checked = tacmap.parse(raw_doc)
    if not checked.ok:
        raise ValueError(' '.join(checked.errors))
    await database.save_tac_map_doc(
        record['id'], tacmap.dumps(checked.doc), member_name
    )
    return checked.warnings


async def share(record, mode: str) -> str:
    """Turn sharing on, change what the link may do, or take it away."""
    if mode not in SHARE_MODES:
        raise ValueError('Pick what the link may do.')
    if mode == 'off':
        await database.set_tac_map_share(record['id'], None, 'off')
        return 'The share link is off — it stops working straight away.'

    # Keep the link stable while only the rights change, so a mode change does
    # not silently break every copy of it somebody has already sent out.
    token = record['share_token'] or secrets.token_urlsafe(TOKEN_BYTES)
    await database.set_tac_map_share(record['id'], token, mode)
    return ('Anyone with the link can draw on it now.' if mode == 'edit'
            else 'Anyone with the link can look at it now.')


async def regenerate(record) -> str:
    if record['share_mode'] == 'off':
        raise ValueError('This map is not shared, so there is no link to replace.')
    await database.set_tac_map_share(
        record['id'], secrets.token_urlsafe(TOKEN_BYTES), record['share_mode']
    )
    return 'New link — the old one no longer opens this map.'


def arma_prefix(record) -> str:
    """The marker prefix this map owns, and no other map does.

    Every exported marker is named after it and the script deletes that set
    before it draws, so pasting a corrected plan replaces the old one in the
    running mission instead of laying a second copy over it. Two maps sharing a
    prefix would delete each other's markers, hence the row id.
    """
    return f"map{record['id']}"


def sqf(record) -> str:
    return tacmap.to_sqf(load(record), prefix=arma_prefix(record),
                         title=record['name'])


def share_path(record) -> str:
    return f"/m/{record['share_token']}" if record['share_token'] else ''


def map_url(origin: str, guild_id, record) -> str:
    """Where to send somebody: the share link when there is one, the page otherwise."""
    path = share_path(record) or f'/g/{guild_id}/maps/{record["id"]}'
    return f"{origin.rstrip('/')}{path}"


def _channel(guild: discord.Guild, channel_id: str):
    for channel in postable_channels(guild):
        if str(channel.id) == str(channel_id):
            return channel
    raise ValueError('Pick a channel I can post in.')


async def post(guild: discord.Guild, record, channel_id: str, url: str,
               member) -> str:
    """Announce the map in a channel, as a link.

    A link rather than a picture: the map is an SVG built in the browser, and
    Discord renders neither SVG attachments nor anything this bot could turn one
    into without a drawing library it does not have. The link is also the thing
    people actually want — it stays current while the plan is edited.
    """
    channel = _channel(guild, channel_id)
    doc = load(record)
    embed = discord.Embed(
        title=f"🗺️ {record['name']}",
        description=record['description'] or None,
        url=url,
        colour=discord.Colour.blurple(),
    )
    embed.add_field(name='On the map', value=tacmap.summarise(doc), inline=True)
    embed.add_field(
        name='Link',
        value=('Anyone with it can draw' if record['share_mode'] == 'edit'
               else 'Anyone with it can look' if record['share_mode'] == 'view'
               else 'Sign-in needed'),
        inline=True,
    )
    embed.set_footer(text=f'Posted by {member.display_name}')

    try:
        message = await channel.send(embed=embed)
    except discord.Forbidden:
        raise ValueError(f"I'm not allowed to post in #{channel.name}.")
    except discord.HTTPException as e:
        raise ValueError(f'Discord rejected the message: {e}.')

    await database.save_tac_map_message(record['id'], str(channel.id), str(message.id))
    return f'Posted in #{channel.name}.'


async def delete(record) -> str:
    await database.delete_tac_map(record['id'])
    return f"Deleted “{record['name']}”."
