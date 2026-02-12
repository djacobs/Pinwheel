"""Discord UI views — buttons and modals for governance interactions.

ProposalConfirmView: Confirm/Revise/Cancel for AI-interpreted proposals.
TradeOfferView: Accept/Reject for token trades.
StrategyConfirmView: Confirm/Cancel for team strategy.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from pinwheel.discord.embeds import (
    build_interpretation_embed,
    build_strategy_embed,
    build_trade_offer_embed,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from pinwheel.config import Settings
    from pinwheel.discord.helpers import GovernorInfo
    from pinwheel.models.governance import RuleInterpretation
    from pinwheel.models.tokens import Trade

logger = logging.getLogger(__name__)


class ProposalConfirmView(discord.ui.View):
    """Confirm/Revise/Cancel buttons for an AI-interpreted proposal."""

    def __init__(
        self,
        *,
        original_user_id: int,
        raw_text: str,
        interpretation: RuleInterpretation,
        tier: int,
        token_cost: int,
        tokens_remaining: int,
        governor_info: GovernorInfo,
        engine: AsyncEngine,
        settings: Settings,
    ) -> None:
        super().__init__(timeout=300)
        self.original_user_id = original_user_id
        self.raw_text = raw_text
        self.interpretation = interpretation
        self.tier = tier
        self.token_cost = token_cost
        self.tokens_remaining = tokens_remaining
        self.governor_info = governor_info
        self.engine = engine
        self.settings = settings

    async def _check_user(
        self, interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Only the proposer can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    def _disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(
        label="Confirm", style=discord.ButtonStyle.green, emoji="\u2705",
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if not await self._check_user(interaction):
            return

        from pinwheel.core.governance import (
            confirm_proposal,
            submit_proposal,
        )
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository
        from pinwheel.models.rules import RuleSet

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)
                season = await repo.get_season(
                    self.governor_info.season_id,
                )
                ruleset = RuleSet(
                    **(season.current_ruleset if season else {}),
                )

                proposal = await submit_proposal(
                    repo=repo,
                    governor_id=self.governor_info.player_id,
                    team_id=self.governor_info.team_id,
                    season_id=self.governor_info.season_id,
                    window_id="",
                    raw_text=self.raw_text,
                    interpretation=self.interpretation,
                    ruleset=ruleset,
                )
                await confirm_proposal(repo, proposal)
                await session.commit()

            self._disable_all()
            embed = discord.Embed(
                title="Proposal Submitted",
                description=(
                    f'"{self.raw_text}"\n\n'
                    "Your proposal is now on the governance floor "
                    "and open for voting."
                ),
                color=0x2ECC71,
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.response.edit_message(
                embed=embed, view=self,
            )
        except Exception:
            logger.exception("proposal_confirm_failed")
            await interaction.response.send_message(
                "Something went wrong confirming the proposal.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Revise",
        style=discord.ButtonStyle.blurple,
        emoji="\u270f\ufe0f",
    )
    async def revise(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if not await self._check_user(interaction):
            return
        modal = ReviseProposalModal(parent_view=self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="Cancel", style=discord.ButtonStyle.red, emoji="\u274c",
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if not await self._check_user(interaction):
            return
        self._disable_all()
        embed = discord.Embed(
            title="Proposal Cancelled",
            description=f'"{self.raw_text}"\n\nNo tokens spent.',
            color=0x95A5A6,
        )
        embed.set_footer(text="Pinwheel Fates")
        await interaction.response.edit_message(
            embed=embed, view=self,
        )


class ReviseProposalModal(discord.ui.Modal, title="Revise Your Proposal"):
    """Text input popup for revising a proposal before submission."""

    revised_text = discord.ui.TextInput(
        label="Revised proposal text",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your rule change...",
        max_length=500,
    )

    def __init__(self, *, parent_view: ProposalConfirmView) -> None:
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(
        self, interaction: discord.Interaction,
    ) -> None:
        new_text = self.revised_text.value
        if not new_text or not new_text.strip():
            await interaction.response.send_message(
                "Proposal text cannot be empty.", ephemeral=True,
            )
            return

        from pinwheel.ai.interpreter import (
            interpret_proposal,
            interpret_proposal_mock,
        )
        from pinwheel.core.governance import detect_tier, token_cost_for_tier
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository
        from pinwheel.models.rules import RuleSet

        try:
            async with get_session(self.parent_view.engine) as session:
                repo = Repository(session)
                season = await repo.get_season(
                    self.parent_view.governor_info.season_id,
                )
                ruleset = RuleSet(
                    **(season.current_ruleset if season else {}),
                )

            api_key = self.parent_view.settings.anthropic_api_key
            if api_key:
                interpretation = await interpret_proposal(
                    new_text, ruleset, api_key,
                )
            else:
                interpretation = interpret_proposal_mock(
                    new_text, ruleset,
                )

            tier = detect_tier(interpretation, ruleset)
            cost = token_cost_for_tier(tier)

            # Update parent view
            self.parent_view.raw_text = new_text
            self.parent_view.interpretation = interpretation
            self.parent_view.tier = tier
            self.parent_view.token_cost = cost

            embed = build_interpretation_embed(
                raw_text=new_text,
                interpretation=interpretation,
                tier=tier,
                token_cost=cost,
                tokens_remaining=self.parent_view.tokens_remaining,
                governor_name=interaction.user.display_name,
            )
            await interaction.response.edit_message(
                embed=embed, view=self.parent_view,
            )
        except Exception:
            logger.exception("proposal_revise_failed")
            await interaction.response.send_message(
                "Something went wrong re-interpreting.",
                ephemeral=True,
            )


class TradeOfferView(discord.ui.View):
    """Accept/Reject buttons for a token trade offer."""

    def __init__(
        self,
        *,
        trade: Trade,
        target_user_id: int,
        from_name: str,
        to_name: str,
        season_id: str,
        engine: AsyncEngine,
    ) -> None:
        super().__init__(timeout=3600)
        self.trade = trade
        self.target_user_id = target_user_id
        self.from_name = from_name
        self.to_name = to_name
        self.season_id = season_id
        self.engine = engine

    @discord.ui.button(
        label="Accept", style=discord.ButtonStyle.green, emoji="\u2705",
    )
    async def accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message(
                "Only the recipient can accept.", ephemeral=True,
            )
            return

        from pinwheel.core.tokens import accept_trade
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)
                await accept_trade(
                    repo, self.trade, self.season_id,
                )
                await session.commit()

            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

            embed = build_trade_offer_embed(
                self.trade, self.from_name, self.to_name,
            )
            embed.title = "Trade Accepted"
            embed.color = 0x2ECC71
            await interaction.response.edit_message(
                embed=embed, view=self,
            )
        except Exception:
            logger.exception("trade_accept_failed")
            await interaction.response.send_message(
                "Something went wrong accepting the trade.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Reject", style=discord.ButtonStyle.red, emoji="\u274c",
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message(
                "Only the recipient can reject.", ephemeral=True,
            )
            return

        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)
                await repo.append_event(
                    event_type="trade.rejected",
                    aggregate_id=self.trade.id,
                    aggregate_type="trade",
                    season_id=self.season_id,
                    governor_id=self.trade.to_governor,
                    payload={"trade_id": self.trade.id},
                )
                await session.commit()

            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

            embed = build_trade_offer_embed(
                self.trade, self.from_name, self.to_name,
            )
            embed.title = "Trade Rejected"
            embed.color = 0xE74C3C
            await interaction.response.edit_message(
                embed=embed, view=self,
            )
        except Exception:
            logger.exception("trade_reject_failed")
            await interaction.response.send_message(
                "Something went wrong rejecting the trade.",
                ephemeral=True,
            )


class StrategyConfirmView(discord.ui.View):
    """Confirm/Cancel buttons for a team strategy override."""

    def __init__(
        self,
        *,
        original_user_id: int,
        raw_text: str,
        team_name: str,
        governor_info: GovernorInfo,
        engine: AsyncEngine,
    ) -> None:
        super().__init__(timeout=300)
        self.original_user_id = original_user_id
        self.raw_text = raw_text
        self.team_name = team_name
        self.governor_info = governor_info
        self.engine = engine

    @discord.ui.button(
        label="Confirm", style=discord.ButtonStyle.green, emoji="\u2705",
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Only the strategist can confirm.",
                ephemeral=True,
            )
            return

        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)
                await repo.append_event(
                    event_type="strategy.set",
                    aggregate_id=self.governor_info.team_id,
                    aggregate_type="team_strategy",
                    season_id=self.governor_info.season_id,
                    governor_id=self.governor_info.player_id,
                    team_id=self.governor_info.team_id,
                    payload={
                        "raw_text": self.raw_text,
                        "team_id": self.governor_info.team_id,
                    },
                )
                await session.commit()

            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

            embed = build_strategy_embed(
                self.raw_text, self.team_name,
            )
            embed.title = f"Strategy Active -- {self.team_name}"
            embed.color = 0x2ECC71
            await interaction.response.edit_message(
                embed=embed, view=self,
            )
        except Exception:
            logger.exception("strategy_confirm_failed")
            await interaction.response.send_message(
                "Something went wrong setting the strategy.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Cancel", style=discord.ButtonStyle.red, emoji="\u274c",
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Only the strategist can cancel.",
                ephemeral=True,
            )
            return

        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        embed = discord.Embed(
            title="Strategy Cancelled",
            description="No changes made.",
            color=0x95A5A6,
        )
        embed.set_footer(text="Pinwheel Fates")
        await interaction.response.edit_message(
            embed=embed, view=self,
        )
