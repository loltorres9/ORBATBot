import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import database
from cogs.admin import UNIT_LEADER_ROLE
from cogs.slots import UNIT_ROLES

# Roles that gate access to slot commands must never become self-assignable —
# a member could otherwise grant themselves approval rights.
PROTECTED_ROLE_NAMES = UNIT_ROLES | {UNIT_LEADER_ROLE}

# Discord allows at most 25 options in a select menu.
MAX_GAME_ROLES = 25


def _is_renderable_emoji(raw: str) -> bool:
    """True if Discord will accept *raw* as a component emoji.

    PartialEmoji.from_str() happily turns plain text like "minecraft" into a
    named emoji, which Discord then rejects — taking the whole select menu with
    it — so anything containing ASCII letters is treated as not an emoji.
    """
    try:
        parsed = discord.PartialEmoji.from_str(raw)
    except Exception:
        return False
    if parsed.id is not None:
        return True
    return not any(c.isascii() and c.isalpha() for c in raw)


def _parse_emoji(bot: commands.Bot, raw: str) -> Optional[str]:
    """Validate an emoji for use on a select option. Raises ValueError if unusable."""
    raw = raw.strip()
    if not raw:
        return None
    if len(raw) > 32:
        raise ValueError("That emoji is too long. Paste a single emoji, e.g. ⛏️.")

    if not _is_renderable_emoji(raw):
        raise ValueError(f"`{raw}` isn't an emoji. Paste an actual emoji, e.g. ⛏️.")

    parsed = discord.PartialEmoji.from_str(raw)
    if parsed.id is not None:
        if bot.get_emoji(parsed.id) is None:
            raise ValueError(
                "I can't use that custom emoji — it has to come from a server I'm also in."
            )
        return str(parsed)
    return raw


def _role_rejection(guild: discord.Guild, role: discord.Role) -> Optional[str]:
    """Return a reason why *role* cannot be a game role, or None if it's fine."""
    if role.is_default():
        return "`@everyone` can't be a game role."
    if role.managed:
        return (
            f"**{role.name}** is managed by an integration (a bot or a boost role), "
            "so I can't hand it out."
        )
    if role.permissions.value != 0:
        return (
            f"**{role.name}** grants server permissions. Game roles must grant none — "
            "clear its permissions in **Server Settings → Roles** first, then run this again."
        )
    if role.name in PROTECTED_ROLE_NAMES:
        return (
            f"**{role.name}** controls access to slot commands, so it can't be self-assignable."
        )
    if role >= guild.me.top_role:
        return (
            f"**{role.name}** sits above my highest role, so Discord won't let me assign it. "
            "Move my role above it in **Server Settings → Roles**."
        )
    return None


def _select_option(row, role: discord.Role, is_default: bool) -> discord.SelectOption:
    option = discord.SelectOption(
        label=row['name'][:100],
        value=str(role.id),
        description=row['description'][:100] if row['description'] else None,
        default=is_default,
    )
    # A bad emoji makes Discord reject the entire select menu, so drop it rather
    # than let one stale entry break the picker for everyone.
    if row['emoji'] and _is_renderable_emoji(row['emoji']):
        option.emoji = row['emoji']
    return option


async def _resolve_game_roles(guild: discord.Guild) -> list:
    """Return [(db_row, discord.Role)] for this guild, dropping registrations
    whose Discord role has since been deleted."""
    entries = []
    for row in await database.get_game_roles(str(guild.id)):
        role = guild.get_role(int(row['role_id']))
        if role is None:
            await database.remove_game_role(str(guild.id), row['role_id'])
            continue
        entries.append((row, role))
    return entries


def _build_panel_embed(entries: list, with_button: bool = True) -> discord.Embed:
    how = (
        "Press the button below (or run `/game-roles`) to change your selection at any time."
        if with_button else
        "Members pick these with `/game-roles`, or from the panel posted by `/game-role-panel`."
    )
    embed = discord.Embed(
        title='🎮 Game Roles',
        description=(
            "Pick the games you play. These roles grant **no permissions** — they only "
            f"let people @mention everyone who plays a game.\n\n{how}\n\n"
            "Missing something? If you have any suggestions for games or genres to add, "
            "please contact the @admins."
        ),
        color=discord.Color.blurple(),
    )
    if entries:
        lines = []
        for row, role in entries:
            prefix = f"{row['emoji']} " if row['emoji'] else ''
            suffix = f" — {row['description']}" if row['description'] else ''
            lines.append(f"{prefix}{role.mention}{suffix}")
        value = '\n'.join(lines)
        embed.add_field(
            name='Available roles',
            value=value[:1021] + '...' if len(value) > 1024 else value,
            inline=False,
        )
    else:
        embed.add_field(
            name='Available roles',
            value='None yet — an admin can add one with `/game-role-add`.',
            inline=False,
        )
    embed.timestamp = discord.utils.utcnow()
    embed.set_footer(text='Last updated')
    return embed


async def _update_game_role_panel(bot: commands.Bot, guild: discord.Guild):
    """Refresh the stored game-role panel message so it lists the current roles."""
    stored = await database.get_game_role_panel(str(guild.id))
    if not stored:
        return

    channel = guild.get_channel(int(stored['channel_id']))
    if channel is None:
        return
    try:
        msg = await channel.fetch_message(int(stored['message_id']))
    except (discord.NotFound, discord.Forbidden):
        return

    entries = await _resolve_game_roles(guild)
    try:
        await msg.edit(embed=_build_panel_embed(entries), view=GameRolePanelView(bot))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def _send_role_picker(interaction: discord.Interaction, bot: commands.Bot):
    """Send the ephemeral game-role picker, pre-ticked with the member's roles."""
    await interaction.response.defer(ephemeral=True)

    entries = await _resolve_game_roles(interaction.guild)
    if not entries:
        await interaction.followup.send(
            "ℹ️ No game roles have been set up yet. "
            "An admin can add one with `/game-role-add`.",
            ephemeral=True,
        )
        return

    if not interaction.guild.me.guild_permissions.manage_roles:
        await interaction.followup.send(
            "❌ I need the **Manage Roles** permission to hand out game roles. "
            "Ask an admin to grant it.",
            ephemeral=True,
        )
        return

    view = GameRoleSelectView(interaction.user, entries)
    held = [role.name for _, role in entries if role in interaction.user.roles]
    current = ', '.join(f"**{n}**" for n in held) if held else '_none_'
    await interaction.followup.send(
        content=(
            "🎮 **Your Game Roles**\n"
            f"Currently assigned: {current}\n\n"
            "Tick every game you play and untick the ones you don't — "
            "your roles are set to exactly what you leave selected."
        ),
        view=view,
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# Self-assign views
# ---------------------------------------------------------------------------

class GameRoleSelectView(discord.ui.View):
    """Ephemeral multi-select. The member's current roles are pre-selected, so
    submitting replaces their game roles with exactly what's ticked."""

    def __init__(self, member: discord.Member, entries: list):
        super().__init__(timeout=180)
        entries = entries[:MAX_GAME_ROLES]
        self.roles_by_id = {str(role.id): role for _, role in entries}

        options = [
            _select_option(row, role, is_default=role in member.roles)
            for row, role in entries
        ]
        select = discord.ui.Select(
            placeholder='Select the games you play…',
            options=options,
            min_values=0,
            max_values=len(options),
        )
        select.callback = self._selected
        self.add_item(select)

    async def _selected(self, interaction: discord.Interaction):
        chosen = set(interaction.data.get('values', []))
        held = set(interaction.user.roles)

        to_add = [r for rid, r in self.roles_by_id.items() if rid in chosen and r not in held]
        to_remove = [r for rid, r in self.roles_by_id.items() if rid not in chosen and r in held]

        if not to_add and not to_remove:
            await interaction.response.edit_message(
                content="ℹ️ Nothing changed — your game roles are already set that way.",
                view=None,
            )
            return

        try:
            if to_add:
                await interaction.user.add_roles(*to_add, reason='Self-assigned game role')
            if to_remove:
                await interaction.user.remove_roles(*to_remove, reason='Self-removed game role')
        except discord.Forbidden:
            await interaction.response.edit_message(
                content=(
                    "❌ Discord wouldn't let me change your roles. My role has to sit "
                    "**above** the game roles and I need **Manage Roles** — ask an admin to check."
                ),
                view=None,
            )
            return
        except discord.HTTPException as e:
            await interaction.response.edit_message(
                content=f"❌ Could not update your roles: `{e}`", view=None
            )
            return

        lines = []
        if to_add:
            lines.append("✅ Added: " + ', '.join(f"**{r.name}**" for r in to_add))
        if to_remove:
            lines.append("➖ Removed: " + ', '.join(f"**{r.name}**" for r in to_remove))
        await interaction.response.edit_message(content='\n'.join(lines), view=None)


class GameRolePanelView(discord.ui.View):
    """Persistent panel button — opens the ephemeral picker for whoever clicks it."""

    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label='🎮 Choose your game roles',
        style=discord.ButtonStyle.primary,
        custom_id='game_roles_open',
    )
    async def open_picker(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await _send_role_picker(interaction, self.bot)
        except Exception as e:
            try:
                await interaction.followup.send(f"❌ Unexpected error: `{e}`", ephemeral=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class GameRolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name='game-role-add',
        description='Add a self-assignable game role, e.g. Minecraft or DCS (Admin only)',
    )
    @app_commands.guild_only()
    @app_commands.describe(
        name='Role name, e.g. Minecraft. An existing role with this exact name is reused.',
        emoji='Emoji shown next to the role in the picker (optional)',
        description='Short note shown under the role in the picker (optional)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def game_role_add(
        self,
        interaction: discord.Interaction,
        name: str,
        emoji: str = None,
        description: str = None,
    ):
        await interaction.response.defer(ephemeral=True)

        name = name.strip()
        if not name:
            await interaction.followup.send("❌ Give the role a name.", ephemeral=True)
            return
        if len(name) > 100:
            await interaction.followup.send(
                "❌ Role names can be at most 100 characters.", ephemeral=True
            )
            return

        if not interaction.guild.me.guild_permissions.manage_roles:
            await interaction.followup.send(
                "❌ I need the **Manage Roles** permission to create or assign game roles.",
                ephemeral=True,
            )
            return

        try:
            emoji_str = _parse_emoji(self.bot, emoji) if emoji else None
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        existing = await _resolve_game_roles(interaction.guild)
        already = discord.utils.find(lambda entry: entry[1].name == name, existing)
        if already is None and len(existing) >= MAX_GAME_ROLES:
            await interaction.followup.send(
                f"❌ You already have {MAX_GAME_ROLES} game roles, which is as many as a "
                "Discord picker can show. Remove one with `/game-role-remove` first.",
                ephemeral=True,
            )
            return

        role = discord.utils.get(interaction.guild.roles, name=name)
        created = False
        if role is None:
            try:
                role = await interaction.guild.create_role(
                    name=name,
                    permissions=discord.Permissions.none(),
                    mentionable=True,
                    reason=f'Game role added by {interaction.user} via /game-role-add',
                )
                created = True
            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ Discord wouldn't let me create that role — check that I have **Manage Roles**.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ Could not create the role: `{e}`", ephemeral=True)
                return
        else:
            rejection = _role_rejection(interaction.guild, role)
            if rejection:
                await interaction.followup.send(f"❌ {rejection}", ephemeral=True)
                return

        await database.add_game_role(
            str(interaction.guild_id), str(role.id), name, emoji_str,
            description.strip() if description else None,
        )

        notes = []
        if created:
            notes.append("Created a new role with **no permissions**, mentionable by everyone.")
        else:
            notes.append(f"Reused the existing {role.mention} role (it grants no permissions).")
            if not role.mentionable:
                notes.append(
                    "ℹ️ It isn't mentionable — enable *Allow anyone to @mention this role* "
                    "in its settings if you want to ping it."
                )
        notes.append("Members can now pick it with `/game-roles` or the `/game-role-panel` button.")

        await interaction.followup.send(
            f"✅ **{name}** is now a self-assignable game role.\n" + '\n'.join(notes),
            ephemeral=True,
        )
        asyncio.create_task(_update_game_role_panel(self.bot, interaction.guild))

    @app_commands.command(
        name='game-role-remove',
        description='Stop a role from being self-assignable (Admin only)',
    )
    @app_commands.guild_only()
    @app_commands.describe(
        role='The game role to unregister',
        delete_role='Also delete the Discord role itself, removing it from everyone',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def game_role_remove(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        delete_role: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)

        removed = await database.remove_game_role(str(interaction.guild_id), str(role.id))
        if not removed:
            await interaction.followup.send(
                f"ℹ️ {role.mention} isn't a game role. See `/game-role-list`.", ephemeral=True
            )
            return

        note = ''
        if delete_role:
            try:
                await role.delete(reason=f'Game role deleted by {interaction.user}')
                note = "\nThe Discord role was deleted, so everyone who had it has lost it."
            except (discord.Forbidden, discord.HTTPException) as e:
                note = f"\n⚠️ Unregistered, but I couldn't delete the Discord role: `{e}`"
        else:
            note = "\nThe Discord role still exists — members who have it keep it."

        await interaction.followup.send(
            f"✅ **{role.name}** is no longer self-assignable.{note}", ephemeral=True
        )
        asyncio.create_task(_update_game_role_panel(self.bot, interaction.guild))

    @app_commands.command(
        name='game-role-list',
        description='Show every self-assignable game role on this server',
    )
    @app_commands.guild_only()
    async def game_role_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        entries = await _resolve_game_roles(interaction.guild)
        if not entries:
            await interaction.followup.send(
                "ℹ️ No game roles yet. An admin can add one with `/game-role-add`.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=_build_panel_embed(entries, with_button=False), ephemeral=True
        )

    @app_commands.command(
        name='game-role-panel',
        description='Post the game-role self-assign panel to a channel (Admin only)',
    )
    @app_commands.guild_only()
    @app_commands.describe(channel='Channel to post in (defaults to the current channel)')
    @app_commands.default_permissions(manage_guild=True)
    async def game_role_panel(
        self, interaction: discord.Interaction, channel: discord.TextChannel = None
    ):
        await interaction.response.defer(ephemeral=True)

        target = channel or interaction.channel
        entries = await _resolve_game_roles(interaction.guild)

        try:
            msg = await target.send(
                embed=_build_panel_embed(entries), view=GameRolePanelView(self.bot)
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ I don't have permission to post in {target.mention}.", ephemeral=True
            )
            return

        await database.save_game_role_panel(
            str(interaction.guild_id), str(target.id), str(msg.id)
        )
        await interaction.followup.send(
            f"✅ Game-role panel posted to {target.mention}. "
            "It updates itself whenever you add or remove a game role.",
            ephemeral=True,
        )

    @app_commands.command(
        name='game-roles',
        description='Choose which game roles you want, e.g. Minecraft or DCS',
    )
    @app_commands.guild_only()
    async def game_roles(self, interaction: discord.Interaction):
        await _send_role_picker(interaction, self.bot)


async def setup(bot: commands.Bot):
    await bot.add_cog(GameRolesCog(bot))
