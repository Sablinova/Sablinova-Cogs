import discord
from typing import Callable, List, Optional


class SnipePaginationView(discord.ui.View):
    """Modern Discord UI pagination view for browsing sniped messages."""

    def __init__(
        self,
        author_id: int,
        pages: List[discord.Embed],
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.pages = pages
        self.current_page = 0
        self.message: Optional[discord.Message] = None
        self._update_button_states()

    def _update_button_states(self) -> None:
        total = len(self.pages)
        self.prev_button.disabled = self.current_page <= 0
        self.next_button.disabled = self.current_page >= total - 1
        self.page_indicator.label = f"{self.current_page + 1}/{total}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the user who invoked this command can navigate pages.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._update_button_states()
            await interaction.response.edit_message(
                embed=self.pages[self.current_page],
                view=self,
            )

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.primary, disabled=True)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self._update_button_states()
            await interaction.response.edit_message(
                embed=self.pages[self.current_page],
                view=self,
            )

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.danger)
    async def dismiss_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass
        self.stop()

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass
