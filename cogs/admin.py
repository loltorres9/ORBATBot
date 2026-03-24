import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from utils import database, sheets
from cogs.slots import _build_orbat_embed

ORBAT_CHANNEL_NAME = 'orbat'


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name='setup-slots',
        description='Load slots from a Google Sheet for the current operation (Admin only)',
    )
    @app_commands.describe(sheet_url='Full Google Sheets URL for this operation')
    @app_commands.default_permissions(manage_guild=True)
    async def setup_slots(self, interaction: discord.Interaction, sheet_url: str):
        await interaction.response.defer(ephemeral=True)

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, sheets.load_slots, sheet_url)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to read the sheet. Make sure you've shared it with the service account.\n`{e}`",
                ephemeral=True,
            )
            return

        await database.create_operation(
            guild_id=str(interaction.guild_id),
            name=data['operation_name'],
            sheet_url=sheet_url,
            sheet_id=data['sheet_id'],
            squad_col=data['squad_col'],
            role_col=data['role_col'],
            status_col=data['status_col'],
            assigned_col=data['assigned_col'],
        )

        slot_count = len(data['slots'])
        confirm_embed = discord.Embed(
            title='✅ Operation Loaded',
            description=(
                f"**{data['operation_name']}**\n"
                f"Found **{slot_count}** available slot(s).\n\n"
                f"Members can now use `/request-slot` to sign up."
            ),
            color=discord.Color.green(),
        )

        # Auto-post ORBAT to #orbat (create channel if needed)
        orbat_channel = discord.utils.get(
            interaction.guild.text_channels, name=ORBAT_CHANNEL_NAME
        )
        if not orbat_channel:
            try:
                orbat_channel = await interaction.guild.create_text_channel(
                    ORBAT_CHANNEL_NAME,
                    topic='Live ORBAT for the current operation',
                )
            except discord.Forbidden:
                orbat_channel = None

        if orbat_channel:
            try:
                op = await database.get_active_operation(str(interaction.guild_id))
                loop = asyncio.get_event_loop()
                all_data = await loop.run_in_executor(None, sheets.load_all_slots, sheet_url)
                pending_rows = set(await database.get_pending_slots(op['id']))
                orbat_embed = _build_orbat_embed(all_data['operation_name'], all_data['slots'], pending_rows)
                msg = await orbat_channel.send(embed=orbat_embed)
                await database.save_orbat_message(
                    str(interaction.guild_id), str(orbat_channel.id), str(msg.id)
                )
                confirm_embed.description += f"\n\n📋 ORBAT posted to {orbat_channel.mention}."
            except Exception:
                pass  # ORBAT post failure is non-fatal

        await interaction.followup.send(embed=confirm_embed, ephemeral=True)

    @app_commands.command(
        name='current-operation',
        description='Show which operation is currently active',
    )
    async def current_operation(self, interaction: discord.Interaction):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message(
                "No active operation. An admin can load one with `/setup-slots`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title='🎖️ Current Operation',
            description=f"**{op['name']}**\n[View Sheet]({op['sheet_url']})",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(
        name='clear-requests',
        description='Cancel all pending slot requests for the current operation (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def clear_requests(self, interaction: discord.Interaction):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message(
                "❌ No active operation.", ephemeral=True
            )
            return

        count = await database.clear_pending_requests(op['id'])
        await interaction.response.send_message(
            f"✅ Cleared **{count}** pending request(s) for **{op['name']}**.",
            ephemeral=True,
        )


    @app_commands.command(
        name='sync',
        description='Force-sync slash commands with Discord (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        synced = await self.bot.tree.sync(guild=interaction.guild)
        await interaction.followup.send(
            f"✅ Synced **{len(synced)}** command(s) to this server.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
