"""Tests for hooper trade per-team majority voting."""

from pinwheel.core.tokens import tally_hooper_trade, vote_hooper_trade
from pinwheel.models.tokens import HooperTrade


def _make_trade(
    from_team_voters: list[str],
    to_team_voters: list[str],
) -> HooperTrade:
    """Create a HooperTrade with the given per-team voter lists."""
    return HooperTrade(
        id="trade-1",
        from_team_id="team-a",
        to_team_id="team-b",
        offered_hooper_ids=["h1"],
        requested_hooper_ids=["h2"],
        offered_hooper_names=["Hooper A"],
        requested_hooper_names=["Hooper B"],
        proposed_by=from_team_voters[0] if from_team_voters else "proposer",
        required_voters=from_team_voters + to_team_voters,
        from_team_voters=from_team_voters,
        to_team_voters=to_team_voters,
        from_team_name="Team Alpha",
        to_team_name="Team Beta",
    )


class TestTallyHooperTrade:
    def test_both_teams_approve(self):
        """Trade passes when both teams have majority yes."""
        trade = _make_trade(
            from_team_voters=["f1", "f2", "f3"],
            to_team_voters=["t1", "t2"],
        )
        # From team: 2 yes, 1 no
        vote_hooper_trade(trade, "f1", "yes")
        vote_hooper_trade(trade, "f2", "yes")
        vote_hooper_trade(trade, "f3", "no")
        # To team: 2 yes
        vote_hooper_trade(trade, "t1", "yes")
        vote_hooper_trade(trade, "t2", "yes")

        all_voted, from_ok, to_ok = tally_hooper_trade(trade)
        assert all_voted is True
        assert from_ok is True
        assert to_ok is True

    def test_one_team_rejects(self):
        """Trade fails when one team unanimously says no, even if other says yes."""
        trade = _make_trade(
            from_team_voters=["f1", "f2"],
            to_team_voters=["t1", "t2"],
        )
        # From team: both yes
        vote_hooper_trade(trade, "f1", "yes")
        vote_hooper_trade(trade, "f2", "yes")
        # To team: both no
        vote_hooper_trade(trade, "t1", "no")
        vote_hooper_trade(trade, "t2", "no")

        all_voted, from_ok, to_ok = tally_hooper_trade(trade)
        assert all_voted is True
        assert from_ok is True
        assert to_ok is False

    def test_both_teams_reject(self):
        """Trade fails when both teams say no."""
        trade = _make_trade(
            from_team_voters=["f1"],
            to_team_voters=["t1"],
        )
        vote_hooper_trade(trade, "f1", "no")
        vote_hooper_trade(trade, "t1", "no")

        all_voted, from_ok, to_ok = tally_hooper_trade(trade)
        assert all_voted is True
        assert from_ok is False
        assert to_ok is False

    def test_not_all_voted(self):
        """Returns False for all when not everyone has voted."""
        trade = _make_trade(
            from_team_voters=["f1", "f2"],
            to_team_voters=["t1"],
        )
        vote_hooper_trade(trade, "f1", "yes")
        # f2 and t1 haven't voted yet

        all_voted, from_ok, to_ok = tally_hooper_trade(trade)
        assert all_voted is False
        assert from_ok is False
        assert to_ok is False

    def test_from_team_rejects_to_team_approves(self):
        """Trade fails when from_team rejects even though to_team approves."""
        trade = _make_trade(
            from_team_voters=["f1", "f2", "f3"],
            to_team_voters=["t1"],
        )
        # From team: 1 yes, 2 no
        vote_hooper_trade(trade, "f1", "yes")
        vote_hooper_trade(trade, "f2", "no")
        vote_hooper_trade(trade, "f3", "no")
        # To team: 1 yes
        vote_hooper_trade(trade, "t1", "yes")

        all_voted, from_ok, to_ok = tally_hooper_trade(trade)
        assert all_voted is True
        assert from_ok is False
        assert to_ok is True

    def test_tie_within_team_rejects(self):
        """A tie (equal yes and no) within a team should reject (strict majority)."""
        trade = _make_trade(
            from_team_voters=["f1", "f2"],
            to_team_voters=["t1"],
        )
        vote_hooper_trade(trade, "f1", "yes")
        vote_hooper_trade(trade, "f2", "no")
        vote_hooper_trade(trade, "t1", "yes")

        all_voted, from_ok, to_ok = tally_hooper_trade(trade)
        assert all_voted is True
        assert from_ok is False  # 1 yes vs 1 no = tie = reject
        assert to_ok is True
