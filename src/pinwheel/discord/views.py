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
    from pinwheel.models.governance import ProposalInterpretation, RuleInterpretation
    from pinwheel.models.tokens import HooperTrade, Trade

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
        interpretation_v2: ProposalInterpretation | None = None,
        token_already_spent: bool = False,
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
        self.interpretation_v2 = interpretation_v2
        self.token_already_spent = token_already_spent

    async def _check_user(
        self,
        interaction: discord.Interaction,
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
        label="Confirm",
        style=discord.ButtonStyle.green,
        emoji="\u2705",
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
                ruleset_data = (season.current_ruleset if season else None) or {}
                ruleset = RuleSet(**ruleset_data)

                proposal = await submit_proposal(
                    repo=repo,
                    governor_id=self.governor_info.player_id,
                    team_id=self.governor_info.team_id,
                    season_id=self.governor_info.season_id,
                    window_id="",
                    raw_text=self.raw_text,
                    interpretation=self.interpretation,
                    ruleset=ruleset,
                    token_already_spent=self.token_already_spent,
                )
                await confirm_proposal(repo, proposal)
                await session.commit()

            self._disable_all()

            from pinwheel.core.governance import (
                _needs_admin_review,
                vote_threshold_for_tier,
            )

            is_wild = _needs_admin_review(proposal)

            # Always show green "Proposal Submitted" embed
            wild_note = " (Wild -- Admin may veto)" if is_wild else ""
            embed = discord.Embed(
                title=f"Proposal Submitted{wild_note}",
                description=(
                    f'"{self.raw_text}"\n\nYour proposal is now on the Floor and open for voting.'
                ),
                color=0x2ECC71,
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.response.edit_message(
                embed=embed,
                view=self,
            )

            # Post public announcement to the channel
            from pinwheel.discord.embeds import (
                build_proposal_announcement_embed,
            )

            threshold = vote_threshold_for_tier(self.tier)
            announcement = build_proposal_announcement_embed(
                proposal_text=self.raw_text,
                parameter=(self.interpretation.parameter if self.interpretation else None),
                old_value=(self.interpretation.old_value if self.interpretation else None),
                new_value=(self.interpretation.new_value if self.interpretation else None),
                tier=self.tier,
                threshold=threshold,
                wild=is_wild,
            )
            if interaction.channel is not None:
                import contextlib

                with contextlib.suppress(
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    await interaction.channel.send(embed=announcement)

            # Wild proposals also notify admin via DM for potential veto
            if is_wild:
                await _notify_admin_for_review(
                    interaction,
                    proposal,
                    self.settings,
                    governor_name=interaction.user.display_name,
                )
        except Exception:
            logger.exception("proposal_confirm_failed")
            await interaction.response.send_message(
                "Your proposal could not be submitted right now. "
                "This might be a temporary database issue -- "
                "try clicking Confirm again, or use `/propose` to start over.",
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
        label="Cancel",
        style=discord.ButtonStyle.red,
        emoji="\u274c",
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if not await self._check_user(interaction):
            return

        # Refund the token if it was already spent at propose-time
        if self.token_already_spent:
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            try:
                async with get_session(self.engine) as session:
                    repo = Repository(session)
                    await repo.append_event(
                        event_type="token.regenerated",
                        aggregate_id=self.governor_info.player_id,
                        aggregate_type="token",
                        season_id=self.governor_info.season_id,
                        governor_id=self.governor_info.player_id,
                        payload={
                            "token_type": "propose",
                            "amount": self.token_cost,
                            "reason": "cancel_refund",
                        },
                    )
                    await session.commit()
            except Exception:
                logger.exception("proposal_cancel_refund_failed")

        self._disable_all()
        refund_note = "Token refunded." if self.token_already_spent else "No tokens spent."
        embed = discord.Embed(
            title="Proposal Cancelled",
            description=f'"{self.raw_text}"\n\n{refund_note}',
            color=0x95A5A6,
        )
        embed.set_footer(text="Pinwheel Fates")
        await interaction.response.edit_message(
            embed=embed,
            view=self,
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
        self,
        interaction: discord.Interaction,
    ) -> None:
        new_text = self.revised_text.value
        if not new_text or not new_text.strip():
            await interaction.response.send_message(
                "Proposal text cannot be empty.",
                ephemeral=True,
            )
            return

        from pinwheel.ai.interpreter import (
            interpret_proposal_v2,
            interpret_proposal_v2_mock,
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
                ruleset_data = (season.current_ruleset if season else None) or {}
                ruleset = RuleSet(**ruleset_data)

            api_key = self.parent_view.settings.anthropic_api_key
            interpretation_v2 = None
            if api_key:
                from pinwheel.ai.classifier import classify_injection
                from pinwheel.evals.injection import store_injection_classification
                from pinwheel.models.governance import (
                    ProposalInterpretation as PI,
                )
                from pinwheel.models.governance import (
                    RuleInterpretation as RI,
                )

                classification = await classify_injection(new_text, api_key)

                # Store classification result for dashboard visibility
                async with get_session(self.parent_view.engine) as cls_session:
                    cls_repo = Repository(cls_session)
                    await store_injection_classification(
                        repo=cls_repo,
                        season_id=self.parent_view.governor_info.season_id,
                        proposal_text=new_text,
                        result=classification,
                        governor_id=self.parent_view.governor_info.player_id,
                        source="discord_views",
                    )
                    await cls_session.commit()

                if classification.classification == "injection" and classification.confidence > 0.8:
                    interpretation = RI(
                        confidence=0.0,
                        injection_flagged=True,
                        rejection_reason=classification.reason,
                        impact_analysis="Proposal flagged as potential prompt injection.",
                    )
                    interpretation_v2 = PI(
                        confidence=0.0,
                        injection_flagged=True,
                        rejection_reason=classification.reason,
                        impact_analysis="Proposal flagged as potential prompt injection.",
                        original_text_echo=new_text,
                    )
                else:
                    interpretation_v2 = await interpret_proposal_v2(
                        new_text,
                        ruleset,
                        api_key,
                    )
                    interpretation = interpretation_v2.to_rule_interpretation()
                    if classification.classification == "suspicious":
                        interpretation.impact_analysis = (
                            f"[Suspicious: {classification.reason}] "
                            + interpretation.impact_analysis
                        )
                        interpretation_v2.impact_analysis = (
                            f"[Suspicious: {classification.reason}] "
                            + interpretation_v2.impact_analysis
                        )
            else:
                interpretation_v2 = interpret_proposal_v2_mock(
                    new_text,
                    ruleset,
                )
                interpretation = interpretation_v2.to_rule_interpretation()

            tier = detect_tier(interpretation, ruleset)
            cost = token_cost_for_tier(tier)

            # Update parent view
            self.parent_view.raw_text = new_text
            self.parent_view.interpretation = interpretation
            self.parent_view.interpretation_v2 = interpretation_v2
            self.parent_view.tier = tier
            self.parent_view.token_cost = cost

            embed = build_interpretation_embed(
                raw_text=new_text,
                interpretation=interpretation,
                tier=tier,
                token_cost=cost,
                tokens_remaining=self.parent_view.tokens_remaining,
                governor_name=interaction.user.display_name,
                interpretation_v2=interpretation_v2,
            )
            await interaction.response.edit_message(
                embed=embed,
                view=self.parent_view,
            )
        except Exception:
            logger.exception("proposal_revise_failed")
            await interaction.response.send_message(
                "Your revised proposal could not be re-interpreted right now. "
                "This might be a temporary issue with the AI interpreter -- "
                "try clicking Revise again, or use `/propose` to start fresh.",
                ephemeral=True,
            )


class AmendConfirmView(discord.ui.View):
    """Confirm/Cancel buttons for an amendment to an existing proposal."""

    def __init__(
        self,
        *,
        original_user_id: int,
        proposal_id: str,
        proposal_raw_text: str,
        amendment_text: str,
        interpretation: RuleInterpretation,
        amendment_number: int,
        max_amendments: int,
        governor_info: GovernorInfo,
        engine: AsyncEngine,
        interpretation_v2: ProposalInterpretation | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.original_user_id = original_user_id
        self.proposal_id = proposal_id
        self.proposal_raw_text = proposal_raw_text
        self.amendment_text = amendment_text
        self.interpretation = interpretation
        self.amendment_number = amendment_number
        self.max_amendments = max_amendments
        self.governor_info = governor_info
        self.engine = engine
        self.interpretation_v2 = interpretation_v2

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Only the amender can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    def _disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(
        label="Confirm Amendment",
        style=discord.ButtonStyle.green,
        emoji="\u2705",
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if not await self._check_user(interaction):
            return

        from pinwheel.core.governance import amend_proposal
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository
        from pinwheel.models.governance import Proposal

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)

                # Reconstruct proposal from submitted event
                submitted = await repo.get_events_by_type(
                    season_id=self.governor_info.season_id,
                    event_types=["proposal.submitted"],
                )
                proposal_data = None
                for evt in submitted:
                    if evt.aggregate_id == self.proposal_id:
                        proposal_data = evt.payload
                        break

                if not proposal_data:
                    await interaction.response.send_message(
                        "The proposal could not be found. It may have been removed.",
                        ephemeral=True,
                    )
                    return

                proposal = Proposal(**proposal_data)
                # Restore current status (may have been amended before)
                confirmed_events = await repo.get_events_by_type(
                    season_id=self.governor_info.season_id,
                    event_types=["proposal.confirmed", "proposal.amended"],
                )
                for evt in confirmed_events:
                    if evt.aggregate_id == self.proposal_id:
                        if evt.event_type == "proposal.amended":
                            proposal.status = "amended"
                        elif proposal.status not in ("amended",):
                            proposal.status = "confirmed"

                await amend_proposal(
                    repo=repo,
                    proposal=proposal,
                    governor_id=self.governor_info.player_id,
                    team_id=self.governor_info.team_id,
                    amendment_text=self.amendment_text,
                    new_interpretation=self.interpretation,
                )
                await session.commit()

            self._disable_all()

            embed = discord.Embed(
                title="Amendment Submitted",
                description=(
                    f"Your amendment to "
                    f'"{self.proposal_raw_text[:80]}" has been submitted.\n\n'
                    f"Amendment: \"{self.amendment_text[:200]}\"\n\n"
                    "The proposal's interpretation has been updated. "
                    "Existing votes stand."
                ),
                color=0x2ECC71,
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.response.edit_message(
                embed=embed,
                view=self,
            )

            # Post public announcement to the channel
            if interaction.channel is not None:
                import contextlib

                announcement = discord.Embed(
                    title="Proposal Amended",
                    description=(
                        f'Original: "{self.proposal_raw_text[:200]}"\n\n'
                        f'Amendment: "{self.amendment_text[:200]}"\n\n'
                        f"Amendment {self.amendment_number} of {self.max_amendments}"
                    ),
                    color=0x3498DB,
                )
                if self.interpretation.parameter:
                    announcement.add_field(
                        name="New Interpretation",
                        value=(
                            f"`{self.interpretation.parameter}`: "
                            f"{self.interpretation.old_value} -> "
                            f"{self.interpretation.new_value}"
                        ),
                        inline=False,
                    )
                announcement.set_footer(text="Use /vote to cast your vote on the amended version")
                with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                    await interaction.channel.send(embed=announcement)

        except Exception:
            logger.exception("amendment_confirm_failed")
            await interaction.response.send_message(
                "Your amendment could not be submitted right now. "
                "This might be a temporary database issue -- "
                "try clicking Confirm again, or use `/amend` to start over.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.red,
        emoji="\u274c",
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
            title="Amendment Cancelled",
            description="No changes made. Your AMEND token was not spent.",
            color=0x95A5A6,
        )
        embed.set_footer(text="Pinwheel Fates")
        await interaction.response.edit_message(
            embed=embed,
            view=self,
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
        label="Accept",
        style=discord.ButtonStyle.green,
        emoji="\u2705",
    )
    async def accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message(
                "Only the recipient can accept.",
                ephemeral=True,
            )
            return

        from pinwheel.core.tokens import accept_trade
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)
                await accept_trade(
                    repo,
                    self.trade,
                    self.season_id,
                )
                await session.commit()

            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

            embed = build_trade_offer_embed(
                self.trade,
                self.from_name,
                self.to_name,
            )
            embed.title = "Trade Accepted"
            embed.color = 0x2ECC71
            await interaction.response.edit_message(
                embed=embed,
                view=self,
            )
        except Exception:
            logger.exception("trade_accept_failed")
            await interaction.response.send_message(
                "The trade could not be accepted right now. "
                "This might be a temporary database issue -- "
                "try clicking Accept again. If it keeps failing, let an admin know.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.red,
        emoji="\u274c",
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message(
                "Only the recipient can reject.",
                ephemeral=True,
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
                self.trade,
                self.from_name,
                self.to_name,
            )
            embed.title = "Trade Rejected"
            embed.color = 0xE74C3C
            await interaction.response.edit_message(
                embed=embed,
                view=self,
            )
        except Exception:
            logger.exception("trade_reject_failed")
            await interaction.response.send_message(
                "The trade could not be rejected right now. "
                "This might be a temporary database issue -- "
                "try clicking Reject again. If it keeps failing, let an admin know.",
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
        api_key: str = "",
    ) -> None:
        super().__init__(timeout=300)
        self.original_user_id = original_user_id
        self.raw_text = raw_text
        self.team_name = team_name
        self.governor_info = governor_info
        self.engine = engine
        self.api_key = api_key

    @discord.ui.button(
        label="Confirm",
        style=discord.ButtonStyle.green,
        emoji="\u2705",
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
            # Interpret strategy into structured parameters
            from pinwheel.ai.interpreter import interpret_strategy, interpret_strategy_mock

            if self.api_key:
                interpreted = await interpret_strategy(self.raw_text, self.api_key)
            else:
                interpreted = interpret_strategy_mock(self.raw_text)

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
                # Store interpreted strategy for simulation to read
                await repo.append_event(
                    event_type="strategy.interpreted",
                    aggregate_id=self.governor_info.team_id,
                    aggregate_type="team_strategy",
                    season_id=self.governor_info.season_id,
                    governor_id=self.governor_info.player_id,
                    team_id=self.governor_info.team_id,
                    payload={
                        "team_id": self.governor_info.team_id,
                        "strategy": interpreted.model_dump(),
                    },
                )
                await session.commit()

            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

            embed = build_strategy_embed(
                self.raw_text,
                self.team_name,
            )
            embed.title = f"Strategy Active -- {self.team_name}"
            embed.color = 0x2ECC71
            # Add interpreted parameters to the embed
            params_desc = (
                f"Confidence: {interpreted.confidence:.0%}\n"
                f"3pt bias: {interpreted.three_point_bias:+.1f} | "
                f"Mid: {interpreted.mid_range_bias:+.1f} | "
                f"Rim: {interpreted.at_rim_bias:+.1f}\n"
                f"Defense: {interpreted.defensive_intensity:+.2f} | "
                f"Pace: {interpreted.pace_modifier:.2f}x | "
                f"Sub: {interpreted.substitution_threshold_modifier:+.2f}"
            )
            embed.add_field(name="Interpreted Parameters", value=params_desc, inline=False)
            await interaction.response.edit_message(
                embed=embed,
                view=self,
            )
        except Exception:
            logger.exception("strategy_confirm_failed")
            await interaction.response.send_message(
                f"Could not set the strategy for **{self.team_name}** right now. "
                "This might be a temporary issue -- "
                "try clicking Confirm again, or use `/strategy` to start over.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.red,
        emoji="\u274c",
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
            embed=embed,
            view=self,
        )


class HooperTradeView(discord.ui.View):
    """Vote buttons for hooper trades — only governors on the two teams can vote."""

    def __init__(
        self,
        *,
        trade: HooperTrade,
        season_id: str,
        engine: AsyncEngine,
    ) -> None:
        super().__init__(timeout=3600)
        self.trade = trade
        self.season_id = season_id
        self.engine = engine

    def _make_embed(self) -> discord.Embed:
        from pinwheel.discord.embeds import build_hooper_trade_embed

        return build_hooper_trade_embed(
            from_team=self.trade.from_team_name,
            to_team=self.trade.to_team_name,
            offered_names=self.trade.offered_hooper_names,
            requested_names=self.trade.requested_hooper_names,
            proposer_name=self.trade.proposed_by,
            votes_cast=len(self.trade.votes),
            votes_needed=len(self.trade.required_voters),
        )

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.green,
    )
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        await self._handle_vote(interaction, "yes")

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.red,
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        await self._handle_vote(interaction, "no")

    async def _handle_vote(
        self,
        interaction: discord.Interaction,
        vote: str,
    ) -> None:
        voter_id = str(interaction.user.id)
        if voter_id not in self.trade.required_voters:
            await interaction.response.send_message(
                "Only governors on the two trading teams can vote.",
                ephemeral=True,
            )
            return

        if voter_id in self.trade.votes:
            await interaction.response.send_message(
                "You've already voted on this trade.",
                ephemeral=True,
            )
            return

        from pinwheel.core.tokens import (
            execute_hooper_trade,
            tally_hooper_trade,
            vote_hooper_trade,
        )

        vote_hooper_trade(self.trade, voter_id, vote)

        all_voted, from_ok, to_ok = tally_hooper_trade(self.trade)
        if all_voted:
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

            if from_ok and to_ok:
                from pinwheel.db.engine import get_session
                from pinwheel.db.repository import Repository

                try:
                    async with get_session(self.engine) as session:
                        repo = Repository(session)
                        await execute_hooper_trade(
                            repo,
                            self.trade,
                            self.season_id,
                        )
                        await session.commit()
                except Exception:
                    logger.exception("hooper_trade_execute_failed")
                    await interaction.response.send_message(
                        "Both teams approved the trade, but it could not be executed right now. "
                        "This is likely a temporary database issue -- let an admin know "
                        "so they can finalize the trade manually.",
                        ephemeral=True,
                    )
                    return

                embed = self._make_embed()
                embed.title = "Trade Approved -- Both teams voted in favor"
                embed.color = 0x2ECC71
                await interaction.response.edit_message(
                    embed=embed,
                    view=self,
                )
            else:
                self.trade.status = "rejected"
                embed = self._make_embed()

                from_name = self.trade.from_team_name or "Offering team"
                to_name = self.trade.to_team_name or "Receiving team"
                if not from_ok and not to_ok:
                    embed.title = "Trade Rejected -- Both teams voted against"
                elif not from_ok:
                    embed.title = f"Trade Rejected -- {from_name} voted against"
                else:
                    embed.title = f"Trade Rejected -- {to_name} voted against"

                embed.color = 0xE74C3C
                await interaction.response.edit_message(
                    embed=embed,
                    view=self,
                )
        else:
            embed = self._make_embed()
            await interaction.response.edit_message(
                embed=embed,
                view=self,
            )


class RepealConfirmView(discord.ui.View):
    """Confirm/Cancel buttons for a repeal proposal."""

    def __init__(
        self,
        *,
        original_user_id: int,
        target_effect_id: str,
        effect_description: str,
        effect_type: str,
        token_cost: int,
        governor_info: GovernorInfo,
        engine: AsyncEngine,
        token_already_spent: bool = False,
    ) -> None:
        super().__init__(timeout=300)
        self.original_user_id = original_user_id
        self.target_effect_id = target_effect_id
        self.effect_description = effect_description
        self.effect_type = effect_type
        self.token_cost = token_cost
        self.governor_info = governor_info
        self.engine = engine
        self.token_already_spent = token_already_spent

    def _disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(
        label="Confirm Repeal",
        style=discord.ButtonStyle.green,
        emoji="\u2705",
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Only the proposer can use these buttons.",
                ephemeral=True,
            )
            return

        from pinwheel.core.governance import (
            confirm_proposal,
            submit_repeal_proposal,
        )
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)
                proposal = await submit_repeal_proposal(
                    repo=repo,
                    governor_id=self.governor_info.player_id,
                    team_id=self.governor_info.team_id,
                    season_id=self.governor_info.season_id,
                    target_effect_id=self.target_effect_id,
                    effect_description=self.effect_description,
                    token_already_spent=self.token_already_spent,
                )
                await confirm_proposal(repo, proposal)
                await session.commit()

            self._disable_all()

            embed = discord.Embed(
                title="Repeal Proposal Submitted",
                description=(
                    f"Your proposal to repeal the "
                    f'**{self.effect_type.replace("_", " ")}** effect:\n\n'
                    f'"{self.effect_description}"\n\n'
                    "is now on the Floor and open for voting."
                ),
                color=0x2ECC71,
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.response.edit_message(
                embed=embed,
                view=self,
            )

            # Post public announcement to the channel
            from pinwheel.discord.embeds import build_proposal_announcement_embed

            announcement = build_proposal_announcement_embed(
                proposal_text=f"Repeal: {self.effect_description}",
                tier=proposal.tier,
                threshold=0.67,  # Tier 5 threshold
                wild=True,
            )
            if interaction.channel is not None:
                import contextlib

                with contextlib.suppress(
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    await interaction.channel.send(embed=announcement)
        except Exception:
            logger.exception("repeal_confirm_failed")
            await interaction.response.send_message(
                "Your repeal proposal could not be submitted right now. "
                "This might be a temporary database issue -- "
                "try clicking Confirm again, or use `/repeal` to start over.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.red,
        emoji="\u274c",
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Only the proposer can cancel.",
                ephemeral=True,
            )
            return

        # Refund the token if it was already spent at propose-time
        if self.token_already_spent:
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            try:
                async with get_session(self.engine) as session:
                    repo = Repository(session)
                    await repo.append_event(
                        event_type="token.regenerated",
                        aggregate_id=self.governor_info.player_id,
                        aggregate_type="token",
                        season_id=self.governor_info.season_id,
                        governor_id=self.governor_info.player_id,
                        payload={
                            "token_type": "propose",
                            "amount": self.token_cost,
                            "reason": "repeal_cancel_refund",
                        },
                    )
                    await session.commit()
            except Exception:
                logger.exception("repeal_cancel_refund_failed")

        self._disable_all()
        refund_note = "Token refunded." if self.token_already_spent else "No tokens spent."
        embed = discord.Embed(
            title="Repeal Cancelled",
            description=f"No repeal proposal submitted.\n\n{refund_note}",
            color=0x95A5A6,
        )
        embed.set_footer(text="Pinwheel Fates")
        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )


async def _notify_admin_for_review(
    interaction: discord.Interaction,
    proposal: object,
    settings: Settings,
    governor_name: str = "",
) -> None:
    """Send a DM to the admin with Veto/Clear buttons for a wild proposal.

    The proposal is already confirmed and open for voting. The admin can
    veto before tally if needed. Tries settings.pinwheel_admin_discord_id
    first, falls back to guild owner.
    """
    import contextlib

    from pinwheel.discord.embeds import build_admin_review_embed
    from pinwheel.models.governance import Proposal as ProposalModel

    if not isinstance(proposal, ProposalModel):
        return

    admin_user: discord.User | None = None

    # Try configured admin ID first
    if settings.pinwheel_admin_discord_id:
        try:
            admin_user = await interaction.client.fetch_user(
                int(settings.pinwheel_admin_discord_id),
            )
        except Exception:
            logger.warning(
                "admin_review_fetch_admin_failed id=%s",
                settings.pinwheel_admin_discord_id,
            )

    # Fall back to guild owner
    if admin_user is None and interaction.guild is not None:
        admin_user = interaction.guild.owner

    if admin_user is None:
        logger.warning("admin_review_no_admin_found proposal=%s", proposal.id)
        return

    embed = build_admin_review_embed(proposal, governor_name=governor_name)
    view = AdminReviewView(
        proposal=proposal,
        proposer_discord_id=interaction.user.id,
        engine=interaction.client.engine,  # type: ignore[attr-defined]
    )

    with contextlib.suppress(discord.Forbidden, discord.HTTPException):
        await admin_user.send(embed=embed, view=view)
        logger.info(
            "admin_review_dm_sent admin=%s proposal=%s",
            admin_user.id,
            proposal.id,
        )


class AdminReviewView(discord.ui.View):
    """Clear/Veto buttons for admin review of wild proposals.

    Sent via DM to the admin when a Tier 5+ or low-confidence proposal
    is submitted. The proposal is already confirmed and open for voting.
    Admin can veto before tally or clear to acknowledge review.
    Timeout: 24 hours.
    """

    def __init__(
        self,
        *,
        proposal: object,
        proposer_discord_id: int,
        engine: AsyncEngine,
    ) -> None:
        super().__init__(timeout=86400)  # 24 hours
        from pinwheel.models.governance import Proposal as ProposalModel

        self.proposal: ProposalModel = proposal  # type: ignore[assignment]
        self.proposer_discord_id = proposer_discord_id
        self.engine = engine

    def _disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(
        label="Clear",
        style=discord.ButtonStyle.green,
    )
    async def clear(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        import contextlib

        from pinwheel.core.governance import admin_clear_proposal
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)
                await admin_clear_proposal(repo, self.proposal)
                await session.commit()

            self._disable_all()
            embed = discord.Embed(
                title="Proposal Cleared",
                description=(
                    f'"{self.proposal.raw_text[:200]}"\n\n'
                    "The proposal has been cleared. Voting continues normally."
                ),
                color=0x2ECC71,
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.response.edit_message(
                embed=embed,
                view=self,
            )

            # DM the proposer
            with contextlib.suppress(
                discord.Forbidden,
                discord.HTTPException,
                Exception,
            ):
                proposer = await interaction.client.fetch_user(
                    self.proposer_discord_id,
                )
                await proposer.send(
                    "Admin has cleared your proposal. Voting continues normally.",
                )
        except Exception:
            logger.exception("admin_clear_failed")
            await interaction.response.send_message(
                "The proposal could not be cleared right now. "
                "This might be a temporary database issue -- "
                "try clicking Clear again. If it keeps failing, check the server logs.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Veto",
        style=discord.ButtonStyle.red,
    )
    async def veto(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # noqa: ARG002
    ) -> None:
        modal = AdminVetoReasonModal(parent_view=self)
        await interaction.response.send_modal(modal)


class AdminVetoReasonModal(discord.ui.Modal, title="Veto Proposal"):
    """Text input for admin to provide a veto reason."""

    reason = discord.ui.TextInput(
        label="Veto reason (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Why is this proposal being vetoed?",
        required=False,
        max_length=500,
    )

    def __init__(self, *, parent_view: AdminReviewView) -> None:
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        import contextlib

        from pinwheel.core.governance import admin_veto_proposal
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        reason = self.reason.value or ""

        try:
            async with get_session(self.parent_view.engine) as session:
                repo = Repository(session)
                await admin_veto_proposal(
                    repo,
                    self.parent_view.proposal,
                    reason=reason,
                )
                await session.commit()

            self.parent_view._disable_all()
            embed = discord.Embed(
                title="Proposal Vetoed",
                description=(
                    f'"{self.parent_view.proposal.raw_text[:200]}"\n\n'
                    f"Reason: {reason or 'No reason provided.'}"
                ),
                color=0xE74C3C,
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.response.edit_message(
                embed=embed,
                view=self.parent_view,
            )

            # DM the proposer
            with contextlib.suppress(
                discord.Forbidden,
                discord.HTTPException,
                Exception,
            ):
                proposer = await interaction.client.fetch_user(
                    self.parent_view.proposer_discord_id,
                )
                reason_msg = f" Reason: {reason}" if reason else ""
                await proposer.send(
                    "Your proposal has been vetoed by an admin."
                    f"{reason_msg} "
                    "Your PROPOSE token has been refunded.",
                )
        except Exception:
            logger.exception("admin_veto_failed")
            await interaction.response.send_message(
                "The proposal could not be vetoed right now. "
                "This might be a temporary database issue -- "
                "try again. If it keeps failing, check the server logs.",
                ephemeral=True,
            )
