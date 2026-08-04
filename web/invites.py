"""Labelling invite links, so a join can say where the link was published.

The join message shows the label next to the code; without one it shows the bare
code, exactly as before.
"""

import discord

from utils import database

MAX_LABEL = 60


async def overview(guild: discord.Guild) -> dict:
    """The guild's invites with their labels, plus labels whose invite is gone.

    Reading the invite list needs Manage Server. Without it the stored labels are
    still shown and editable — they just can't be matched against live codes.
    """
    labels = await database.get_invite_labels(str(guild.id))

    invites, can_read = [], True
    try:
        for invite in sorted(await guild.invites(), key=lambda i: -(i.uses or 0)):
            invites.append({
                'code': invite.code,
                'uses': invite.uses or 0,
                'url': invite.url,
                'inviter': invite.inviter.display_name if invite.inviter else '',
                'channel': invite.channel.name if invite.channel else '',
                'label': labels.get(invite.code, ''),
            })
    except (discord.Forbidden, discord.HTTPException):
        can_read = False

    live = {item['code'] for item in invites}
    orphans = [
        {'code': code, 'label': label}
        for code, label in sorted(labels.items()) if code not in live
    ]
    return {'invites': invites, 'orphans': orphans, 'can_read_invites': can_read}


def read_form(form) -> tuple:
    """(labels to store, codes to forget). An emptied field drops the label."""
    labels, remove = {}, []
    for code in form.getlist('code'):
        code = (code or '').strip()
        if not code:
            continue
        label = (form.get(f'label_{code}') or '').strip()[:MAX_LABEL]
        if label:
            labels[code] = label
        else:
            remove.append(code)
    return labels, remove
