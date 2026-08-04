"""Rich messages built in the web UI — the Discord side of them.

A row in `embeds` (plus its `embed_fields`) is a stored description of a message;
this module turns one into a `discord.Embed` and keeps the posted message in
sync. The web layer owns the forms, never the Discord objects.
"""

import re

import discord

from utils import database

# Discord's own limits. They are enforced here rather than in the form so the
# rules hold wherever an embed is built from.
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FOOTER = 2048
MAX_AUTHOR = 256
MAX_TOTAL = 6000
# Discord allows 25 fields; 10 is as many as the builder form offers.
MAX_FIELDS = 10

DEFAULT_COLOR = '#5865F2'

_HEX = re.compile(r'^#?([0-9a-fA-F]{6})$')


def parse_color(raw: str) -> discord.Color:
    """`#5865F2` or `5865f2` → a Color. Anything else falls back to the default,
    since a stored value can only have come from this same parser."""
    match = _HEX.match((raw or '').strip())
    if not match:
        match = _HEX.match(DEFAULT_COLOR)
    return discord.Color(int(match.group(1), 16))


def clean_color(raw: str) -> str:
    """Normalise a submitted colour to `#rrggbb`, or raise for garbage."""
    raw = (raw or '').strip()
    if not raw:
        return DEFAULT_COLOR
    match = _HEX.match(raw)
    if not match:
        raise ValueError(f"'{raw}' isn't a colour. Use a hex value like #5865F2.")
    return f"#{match.group(1).lower()}"


def _text(value: str):
    return (value or '').strip() or None


def build_embed(record, fields: list) -> discord.Embed:
    """Turn a stored embed and its fields into the message Discord will show."""
    embed = discord.Embed(
        title=_text(record['title']),
        description=_text(record['description']),
        url=_text(record['url']),
        color=parse_color(record['color']),
    )
    if _text(record['author_name']):
        embed.set_author(
            name=record['author_name'].strip(),
            icon_url=_text(record['author_icon_url']),
        )
    if _text(record['thumbnail_url']):
        embed.set_thumbnail(url=record['thumbnail_url'].strip())
    if _text(record['image_url']):
        embed.set_image(url=record['image_url'].strip())
    if _text(record['footer_text']) or _text(record['footer_icon_url']):
        embed.set_footer(
            text=_text(record['footer_text']) or '​',
            icon_url=_text(record['footer_icon_url']),
        )
    if record['show_timestamp']:
        embed.timestamp = discord.utils.utcnow()

    for field in fields[:MAX_FIELDS]:
        embed.add_field(
            name=field['name'], value=field['value'], inline=bool(field['inline'])
        )
    return embed


def validate(values: dict, fields: list) -> None:
    """Check an embed against Discord's limits before it is stored.

    Discord rejects the whole message when any of these is exceeded, and a
    rejected send leaves a saved-but-unpostable embed behind — so it is caught
    on the way in, where the user is looking at the form.
    """
    checks = (
        ('Title', values.get('title'), MAX_TITLE),
        ('Description', values.get('description'), MAX_DESCRIPTION),
        ('Footer text', values.get('footer_text'), MAX_FOOTER),
        ('Author name', values.get('author_name'), MAX_AUTHOR),
    )
    for label, value, limit in checks:
        if value and len(value) > limit:
            raise ValueError(f"{label} is too long — Discord allows {limit} characters.")

    for index, field in enumerate(fields, start=1):
        if len(field['name']) > MAX_FIELD_NAME:
            raise ValueError(f"Field {index}'s name is too long (max {MAX_FIELD_NAME}).")
        if len(field['value']) > MAX_FIELD_VALUE:
            raise ValueError(f"Field {index}'s text is too long (max {MAX_FIELD_VALUE}).")

    total = sum(len(value or '') for value in (
        values.get('title'), values.get('description'),
        values.get('footer_text'), values.get('author_name'),
    )) + sum(len(f['name']) + len(f['value']) for f in fields)
    if total > MAX_TOTAL:
        raise ValueError(
            f"The whole embed is {total} characters; Discord's limit across all of it "
            f"is {MAX_TOTAL}. Shorten it or split it into two messages."
        )

    has_content = any((
        _text(values.get('title')), _text(values.get('description')),
        _text(values.get('image_url')), _text(values.get('author_name')), fields,
    ))
    if not has_content:
        raise ValueError(
            "An embed needs at least a title, a description, an image or one field — "
            "Discord won't accept an empty one."
        )


async def post(bot, channel, embed_id: int) -> discord.Message:
    """Send the embed as a new message and remember where it landed."""
    record = await database.get_embed(embed_id)
    fields = await database.get_embed_fields(embed_id)
    message = await channel.send(
        content=_text(record['content']),
        embed=build_embed(record, fields),
    )
    await database.save_embed_message(embed_id, str(channel.id), str(message.id))
    return message


async def edit_posted(bot, record) -> str:
    """Update the message an embed was posted as.

    Returns '' on success, or a reason the message could not be updated. A
    message that has since been deleted clears `message_id`, so the next send
    posts a fresh one rather than failing the same way forever.
    """
    if not (record['channel_id'] and record['message_id']):
        return 'not posted'

    channel = bot.get_channel(int(record['channel_id']))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(record['channel_id']))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return 'that channel is gone'

    try:
        message = await channel.fetch_message(int(record['message_id']))
    except discord.NotFound:
        await database.clear_embed_message(record['id'])
        return 'the message was deleted'
    except (discord.Forbidden, discord.HTTPException) as e:
        return f'{e}'

    fields = await database.get_embed_fields(record['id'])
    try:
        await message.edit(
            content=_text(record['content']), embed=build_embed(record, fields)
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        return f'{e}'
    return ''
