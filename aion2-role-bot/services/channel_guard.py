import discord
from config import ALLOWED_CHANNEL_ID


class ChannelLockedView(discord.ui.View):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if ALLOWED_CHANNEL_ID and interaction.channel_id != ALLOWED_CHANNEL_ID:
            await interaction.response.send_message(
                f"Use the roles in <#{ALLOWED_CHANNEL_ID}>.", ephemeral=True
            )
            return False
        return True