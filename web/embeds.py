"""The embed builder behind the browser forms.

Translates form fields into a stored embed and keeps the posted message in step.
The limits, the colour parsing and the Discord objects live in `utils/embeds.py`,
so an embed built here and one built anywhere else are the same thing.
"""

import discord
from discord.ext import commands

from utils import database, embeds as embedlib
from web.guilds import postable_channels

MAX_NAME = 100
FIELD_SLOTS = embedlib.MAX_FIELDS


def _text(raw, limit: int = None):
    value = (raw or '').strip()
    if not value:
        return None
    return value[:limit] if limit else value


def _url(raw, label: str):
    """Discord silently drops a malformed URL and rejects some outright, which
    looks like the builder losing the value — so it is checked here instead."""
    value = (raw or '').strip()
    if not value:
        return None
    if not value.startswith(('http://', 'https://')):
        raise ValueError(f"{label} has to be a full URL starting with https://")
    return value


def read_form(form: dict) -> tuple:
    """Turn the builder form into (column values, fields). Raises ValueError."""
    name = _text(form.get('name'), MAX_NAME)
    if not name:
        raise ValueError("Give this embed a name — it's only used to find it again here.")

    values = {
        'name': name,
        'content': _text(form.get('content')),
        'title': _text(form.get('title')),
        'description': _text(form.get('description')),
        'url': _url(form.get('url'), 'The title link'),
        'color': embedlib.clean_color(form.get('color')),
        'author_name': _text(form.get('author_name')),
        'author_icon_url': _url(form.get('author_icon_url'), "The author's icon"),
        'thumbnail_url': _url(form.get('thumbnail_url'), 'The thumbnail'),
        'image_url': _url(form.get('image_url'), 'The image'),
        'footer_text': _text(form.get('footer_text')),
        'footer_icon_url': _url(form.get('footer_icon_url'), "The footer's icon"),
        'show_timestamp': 1 if form.get('show_timestamp') else 0,
    }

    fields = []
    for index in range(FIELD_SLOTS):
        field_name = _text(form.get(f'field_name_{index}'))
        field_value = _text(form.get(f'field_value_{index}'))
        if not field_name and not field_value:
            continue
        if not (field_name and field_value):
            raise ValueError(f"Field {index + 1} needs both a heading and a text.")
        fields.append({
            'name': field_name,
            'value': field_value,
            'inline': 1 if form.get(f'field_inline_{index}') else 0,
        })

    embedlib.validate(values, fields)
    return values, fields


def _channel(guild: discord.Guild, raw):
    if not (raw or '').strip():
        raise ValueError("Pick a channel.")
    channel = guild.get_channel(int(raw)) if str(raw).isdigit() else None
    if channel is None or channel not in postable_channels(guild):
        raise ValueError("I can't post in that channel — pick another one.")
    return channel


async def create(guild: discord.Guild, member: discord.Member, form: dict) -> int:
    # The channel is deliberately not part of the draft — it is chosen when the
    # embed is sent, which is also what records where it ended up.
    values, fields = read_form(form)
    embed_id = await database.create_embed(
        str(guild.id), str(member.id), member.display_name, values
    )
    await database.set_embed_fields(embed_id, fields)
    return embed_id


async def save(bot: commands.Bot, record, form: dict) -> list:
    """Store the edit and, if the embed is already posted, update that message."""
    values, fields = read_form(form)
    await database.update_embed(record['id'], values)
    await database.set_embed_fields(record['id'], fields)

    notes = []
    if record['message_id']:
        updated = await database.get_embed(record['id'])
        problem = await embedlib.edit_posted(bot, updated)
        if problem == 'the message was deleted':
            notes.append(
                "The message it was posted as is gone, so this is a draft again — "
                "send it to post a new one."
            )
        elif problem:
            notes.append(f"Saved, but the posted message couldn't be updated: {problem}.")
        else:
            notes.append("The posted message was updated too.")
    return notes


async def send(bot: commands.Bot, guild: discord.Guild, record, channel_id: str) -> list:
    """Post the embed as a new message."""
    channel = _channel(guild, channel_id)
    previous = record['message_id']

    try:
        await embedlib.post(bot, channel, record['id'])
    except discord.Forbidden:
        raise ValueError(f"I'm not allowed to post in #{channel.name}.")
    except discord.HTTPException as e:
        raise ValueError(
            f"Discord rejected the message: {e}. Check the image and icon URLs — "
            "they have to point straight at an image."
        )

    notes = [f"Posted in #{channel.name}."]
    if previous:
        notes.append(
            "The message it was posted as before is now unlinked — it is still in "
            "its channel until you delete it there."
        )
    return notes


async def delete(bot: commands.Bot, record, delete_message: bool) -> list:
    notes = []
    if delete_message and record['message_id']:
        channel = bot.get_channel(int(record['channel_id'])) if record['channel_id'] else None
        try:
            if channel is None:
                raise discord.NotFound(None, 'channel gone')
            message = await channel.fetch_message(int(record['message_id']))
            await message.delete()
            notes.append("Its message was deleted from Discord.")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
            notes.append("I couldn't delete its message — it may already be gone.")

    await database.delete_embed(record['id'])
    return notes


def form_values(record, fields: list) -> dict:
    """Prefill the builder form from a stored embed."""
    values = {name: record[name] or '' for name in (
        'name', 'content', 'title', 'description', 'url', 'color', 'author_name',
        'author_icon_url', 'thumbnail_url', 'image_url', 'footer_text', 'footer_icon_url',
    )}
    values['show_timestamp'] = record['show_timestamp']
    for index in range(FIELD_SLOTS):
        field = fields[index] if index < len(fields) else None
        values[f'field_name_{index}'] = field['name'] if field else ''
        values[f'field_value_{index}'] = field['value'] if field else ''
        values[f'field_inline_{index}'] = field['inline'] if field else 0
    return values
