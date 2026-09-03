"""The tab bar, built once here instead of three times in Jinja.

The site grew past the point where one flat row of tabs reads as anything: by
the eighth tab nobody can see that Operation, Slot Approvals and ORBATs are
three views of the same thing. So the tabs are a two-level structure, and it is
built in Python because the shape of it — which groups exist, what each one
lands on, who may see it — is the kind of thing that goes wrong when it is
spread across template conditionals.

`build()` returns everything `_nav.html` needs:

    groups    the top row, in order
    items     group key → the second row for that group
    group_of  page key → the group it belongs to, so a page only has to name
              itself and the nav works out where that sits
"""


def _item(key: str, label: str, href: str) -> dict:
    return {'key': key, 'label': label, 'href': href}


def build(guild_id, *, is_admin: bool, may_action_slots: bool) -> dict:
    """The tab structure for one member of one guild.

    Permissions decide what is *in* the structure rather than what the template
    hides, so a group whose every page is out of reach is not rendered at all —
    a Unit Leader who is not an admin sees Operations with one page under it.
    """
    base = f"/g/{guild_id}"

    # The slot system: one operation, its queue, and the rosters it runs on.
    # Three pages that are always about the same evening.
    operations = []
    if is_admin:
        operations.append(_item('operation', 'Operation', f"{base}/operation"))
    if may_action_slots:
        operations.append(_item('slots', 'Slot Approvals', f"{base}/slots"))
    if is_admin:
        operations.append(_item('orbats', 'ORBATs', f"{base}/orbats"))
        operations.append(_item('opsettings', 'Settings',
                                f"{base}/operation/settings"))

    groups = [_item('events', '📅 Events', base)]
    if operations:
        # The group's own link is its first page, which is the one the viewer
        # is allowed to open — an admin lands on Operation, a Unit Leader on
        # the queue.
        groups.append({'key': 'ops', 'label': '🎖️ Operations',
                       'href': operations[0]['href']})
    groups.append(_item('roles', '🎮 Game roles', f"{base}/roles"))
    groups.append(_item('voice', '🔊 Voice time', f"{base}/voice"))
    if is_admin:
        groups.append(_item('embeds', '📝 Embeds', f"{base}/embeds"))
        groups.append(_item('logs', '📋 Member log', f"{base}/logs"))
        groups.append(_item('reddit', '📣 Reddit', f"{base}/reddit"))

    items = {'ops': operations}
    group_of = {
        item['key']: group_key
        for group_key, group_items in items.items()
        for item in group_items
    }
    return {'groups': groups, 'items': items, 'group_of': group_of}
