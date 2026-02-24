"""Tests for all Pydantic domain models."""

import pytest
from pydantic import ValidationError

from pinwheel.models.game import (
    CommentaryLine,
    GameResult,
    HooperBoxScore,
    PossessionLog,
)
from pinwheel.models.governance import Amendment, GovernanceEvent, Proposal, Vote
from pinwheel.models.report import Report, ReportUpdate
from pinwheel.models.rules import DEFAULT_RULESET, RuleChange, RuleSet
from pinwheel.models.team import Hooper, Move, PlayerAttributes, Team, Venue, suppress_budget_check
from pinwheel.models.tokens import TokenBalance, Trade

# --- RuleSet ---


class TestRuleSet:
    def test_defaults(self):
        rules = RuleSet()
        assert rules.quarter_minutes == 10
        assert rules.shot_clock_seconds == 15
        assert rules.three_point_value == 3
        assert rules.elam_margin == 15

    def test_custom_values(self):
        rules = RuleSet(three_point_value=4, elam_margin=10)
        assert rules.three_point_value == 4
        assert rules.elam_margin == 10

    def test_validation_rejects_out_of_range(self):
        with pytest.raises(ValidationError):
            RuleSet(quarter_minutes=100)

    def test_default_ruleset_singleton(self):
        assert DEFAULT_RULESET.quarter_minutes == 10

    def test_rule_change(self):
        rc = RuleChange(
            parameter="three_point_value",
            old_value=3,
            new_value=4,
            source_proposal_id="p-1",
            round_enacted=8,
        )
        assert rc.new_value == 4


# --- Team / Hooper ---


class TestPlayerAttributes:
    def test_total(self):
        attrs = PlayerAttributes(
            scoring=80,
            passing=40,
            defense=25,
            speed=50,
            stamina=30,
            iq=55,
            ego=32,
            chaotic_alignment=20,
            fate=28,
        )
        assert attrs.total() == 360

    def test_rejects_zero(self):
        with pytest.raises(ValidationError):
            PlayerAttributes(
                scoring=0,
                passing=40,
                defense=25,
                speed=50,
                stamina=30,
                iq=55,
                ego=32,
                chaotic_alignment=20,
                fate=28,
            )

    def test_rejects_over_100(self):
        with pytest.raises(ValidationError):
            PlayerAttributes(
                scoring=101,
                passing=40,
                defense=25,
                speed=50,
                stamina=30,
                iq=55,
                ego=32,
                chaotic_alignment=20,
                fate=28,
            )

    def test_budget_enforced_exact(self):
        """Total of exactly 360 is accepted."""
        attrs = PlayerAttributes(
            scoring=80,
            passing=40,
            defense=25,
            speed=50,
            stamina=30,
            iq=55,
            ego=32,
            chaotic_alignment=20,
            fate=28,
        )
        assert attrs.total() == 360

    def test_budget_enforced_within_variance(self):
        """Totals within +/- 10 of 360 are accepted."""
        # Total = 350 (lower bound)
        attrs_low = PlayerAttributes(
            scoring=80,
            passing=40,
            defense=25,
            speed=40,
            stamina=30,
            iq=55,
            ego=32,
            chaotic_alignment=20,
            fate=28,
        )
        assert attrs_low.total() == 350

        # Total = 370 (upper bound)
        attrs_high = PlayerAttributes(
            scoring=80,
            passing=40,
            defense=25,
            speed=60,
            stamina=30,
            iq=55,
            ego=32,
            chaotic_alignment=20,
            fate=28,
        )
        assert attrs_high.total() == 370

    def test_budget_rejects_too_low(self):
        """Total below 350 is rejected."""
        with pytest.raises(ValidationError, match="Attribute total 349"):
            PlayerAttributes(
                scoring=80,
                passing=40,
                defense=25,
                speed=39,
                stamina=30,
                iq=55,
                ego=32,
                chaotic_alignment=20,
                fate=28,
            )

    def test_budget_rejects_too_high(self):
        """Total above 370 is rejected."""
        with pytest.raises(ValidationError, match="Attribute total 371"):
            PlayerAttributes(
                scoring=80,
                passing=40,
                defense=25,
                speed=61,
                stamina=30,
                iq=55,
                ego=32,
                chaotic_alignment=20,
                fate=28,
            )

    def test_model_construct_bypasses_budget(self):
        """model_construct() allows arbitrary totals (for runtime use)."""
        attrs = PlayerAttributes.model_construct(
            scoring=100,
            passing=100,
            defense=100,
            speed=100,
            stamina=100,
            iq=100,
            ego=100,
            chaotic_alignment=100,
            fate=100,
        )
        assert attrs.total() == 900

    def test_suppress_budget_check_context_manager(self):
        """suppress_budget_check() allows arbitrary totals within context."""
        with suppress_budget_check():
            attrs = PlayerAttributes(
                scoring=100,
                passing=100,
                defense=100,
                speed=100,
                stamina=100,
                iq=100,
                ego=100,
                chaotic_alignment=100,
                fate=100,
            )
        assert attrs.total() == 900

        # Outside context, validation is active again
        with pytest.raises(ValidationError):
            PlayerAttributes(
                scoring=100,
                passing=100,
                defense=100,
                speed=100,
                stamina=100,
                iq=100,
                ego=100,
                chaotic_alignment=100,
                fate=100,
            )

    def test_budget_configurable_via_class_vars(self):
        """BUDGET and VARIANCE class vars control the allowed range."""
        assert PlayerAttributes.BUDGET == 360
        assert PlayerAttributes.VARIANCE == 10


class TestTeamModels:
    def test_venue(self):
        v = Venue(name="The Thorn Garden", capacity=18000, altitude_ft=50)
        assert v.surface == "hardwood"

    def test_move(self):
        m = Move(name="Heat Check", trigger="made_three", effect="+15% three_point")
        assert m.source == "archetype"

    def test_hooper(self):
        attrs = PlayerAttributes(
            scoring=80,
            passing=40,
            defense=25,
            speed=50,
            stamina=30,
            iq=55,
            ego=32,
            chaotic_alignment=20,
            fate=28,
        )
        h = Hooper(
            id="a-1",
            name="Nakamura",
            team_id="t-1",
            archetype="sharpshooter",
            attributes=attrs,
        )
        assert h.is_starter is True

    def test_team(self):
        v = Venue(name="Court", capacity=5000)
        t = Team(id="t-1", name="Thorns", venue=v)
        assert t.hoopers == []


# --- Game ---


class TestGameModels:
    def test_possession_log(self):
        p = PossessionLog(
            quarter=1,
            possession_number=1,
            offense_team_id="t-1",
            ball_handler_id="a-1",
            action="three_point",
            result="made",
            points_scored=3,
        )
        assert p.points_scored == 3

    def test_box_score_percentages(self):
        bs = HooperBoxScore(
            hooper_id="a-1",
            hooper_name="Test",
            team_id="t-1",
            field_goals_made=5,
            field_goals_attempted=10,
            three_pointers_made=3,
            three_pointers_attempted=6,
        )
        assert bs.fg_pct == 0.5
        assert bs.three_pct == 0.5

    def test_box_score_zero_attempts(self):
        bs = HooperBoxScore(hooper_id="a-1", hooper_name="Test", team_id="t-1")
        assert bs.fg_pct == 0.0
        assert bs.three_pct == 0.0

    def test_game_result(self):
        gr = GameResult(
            game_id="g-1-1",
            home_team_id="t-1",
            away_team_id="t-2",
            home_score=55,
            away_score=52,
            winner_team_id="t-1",
            seed=42,
            total_possessions=65,
        )
        assert gr.elam_activated is False

    def test_commentary_line(self):
        cl = CommentaryLine(
            game_id="g-1-1",
            possession_index=5,
            quarter=2,
            commentary="What a shot!",
            energy="peak",
            tags=["clutch"],
        )
        assert cl.energy == "peak"


# --- Governance ---


class TestGovernanceModels:
    def test_governance_event(self):
        e = GovernanceEvent(
            id="e-1",
            event_type="proposal.submitted",
            aggregate_id="p-1",
            aggregate_type="proposal",
            governor_id="gov-1",
        )
        assert e.event_type == "proposal.submitted"

    def test_proposal(self):
        p = Proposal(
            id="p-1",
            governor_id="gov-1",
            team_id="t-1",
            raw_text="Increase 3pt value to 4",
        )
        assert p.status == "draft"
        assert p.tier == 1

    def test_vote(self):
        v = Vote(proposal_id="p-1", governor_id="gov-1", vote="yes")
        assert v.weight == 1.0

    def test_amendment(self):
        a = Amendment(proposal_id="p-1", governor_id="gov-2", amendment_text="Make it 5 instead")
        assert a.new_interpretation is None


# --- Tokens ---


class TestTokenModels:
    def test_token_balance_defaults(self):
        tb = TokenBalance(governor_id="gov-1")
        assert tb.propose == 2
        assert tb.amend == 2
        assert tb.boost == 2

    def test_trade(self):
        t = Trade(
            id="tr-1",
            from_governor="gov-1",
            to_governor="gov-2",
            offered_type="propose",
            offered_amount=1,
            requested_type="boost",
            requested_amount=2,
        )
        assert t.status == "offered"

    def test_trade_rejects_zero_amount(self):
        with pytest.raises(ValidationError):
            Trade(
                id="tr-1",
                from_governor="gov-1",
                to_governor="gov-2",
                offered_type="propose",
                offered_amount=0,
                requested_type="boost",
                requested_amount=1,
            )


# --- Report ---


class TestReportModels:
    def test_report(self):
        m = Report(id="m-1", report_type="simulation", round_number=14)
        assert m.content == ""

    def test_private_report(self):
        m = Report(id="m-2", report_type="private", governor_id="gov-1", round_number=14)
        assert m.governor_id == "gov-1"

    def test_report_update(self):
        mu = ReportUpdate(report_id="m-1", report_type="governance", round_number=14)
        assert mu.excerpt == ""


# --- Cross-module import test ---


def test_no_circular_imports():
    """All model modules can be imported together without circular dependency."""
    from pinwheel.models import game, governance, report, rules, team, tokens  # noqa: F401
