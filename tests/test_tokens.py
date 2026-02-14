"""Tests for hooper trade voting, execution, and timing with simulation."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.tokens import (
    execute_hooper_trade,
    tally_hooper_trade,
    vote_hooper_trade,
)
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
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


# ---------------------------------------------------------------------------
# Integration tests: hooper trade execution and timing with simulation
# ---------------------------------------------------------------------------


def _hooper_attrs() -> dict:
    return {
        "scoring": 50,
        "passing": 40,
        "defense": 35,
        "speed": 45,
        "stamina": 40,
        "iq": 50,
        "ego": 30,
        "chaotic_alignment": 40,
        "fate": 30,
    }


@pytest.fixture
async def trade_engine() -> AsyncEngine:
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _setup_two_teams(
    repo: Repository,
) -> tuple[str, str, str, list[str], list[str]]:
    """Create a league, season, 2 teams with 4 hoopers each, and a schedule.

    Returns (season_id, team_a_id, team_b_id, team_a_hooper_ids, team_b_hooper_ids).
    """
    from pinwheel.core.scheduler import generate_round_robin

    league = await repo.create_league("Trade Test League")
    season = await repo.create_season(
        league.id,
        "Trade Season",
        starting_ruleset={"quarter_minutes": 3},
    )

    team_a = await repo.create_team(
        season.id,
        "Team Alpha",
        venue={"name": "Alpha Arena", "capacity": 5000},
    )
    team_b = await repo.create_team(
        season.id,
        "Team Beta",
        venue={"name": "Beta Arena", "capacity": 5000},
    )

    a_ids = []
    for j in range(4):
        h = await repo.create_hooper(
            team_id=team_a.id,
            season_id=season.id,
            name=f"Alpha-{j + 1}",
            archetype="sharpshooter",
            attributes=_hooper_attrs(),
        )
        a_ids.append(h.id)

    b_ids = []
    for j in range(4):
        h = await repo.create_hooper(
            team_id=team_b.id,
            season_id=season.id,
            name=f"Beta-{j + 1}",
            archetype="sharpshooter",
            attributes=_hooper_attrs(),
        )
        b_ids.append(h.id)

    matchups = generate_round_robin([team_a.id, team_b.id], num_rounds=2)
    for m in matchups:
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=m.round_number,
            matchup_index=m.matchup_index,
            home_team_id=m.home_team_id,
            away_team_id=m.away_team_id,
        )

    return season.id, team_a.id, team_b.id, a_ids, b_ids


class TestExecuteHooperTrade:
    """Integration tests for hooper trade execution against a real DB."""

    async def test_swap_moves_hoopers(self, trade_engine: AsyncEngine):
        """execute_hooper_trade swaps hoopers between teams in the DB."""
        async with get_session(trade_engine) as session:
            repo = Repository(session)
            season_id, team_a, team_b, a_ids, b_ids = await _setup_two_teams(repo)

            trade = HooperTrade(
                id="trade-exec-1",
                from_team_id=team_a,
                to_team_id=team_b,
                offered_hooper_ids=[a_ids[0]],
                requested_hooper_ids=[b_ids[0]],
                offered_hooper_names=["Alpha-1"],
                requested_hooper_names=["Beta-1"],
                proposed_by="gov-1",
                required_voters=["gov-1", "gov-2"],
                from_team_voters=["gov-1"],
                to_team_voters=["gov-2"],
                from_team_name="Team Alpha",
                to_team_name="Team Beta",
            )

            await execute_hooper_trade(repo, trade, season_id)

            # Verify hooper moved: Alpha-1 should be on Team Beta
            moved_a = await repo.get_hooper(a_ids[0])
            assert moved_a is not None
            assert moved_a.team_id == team_b

            # Verify hooper moved: Beta-1 should be on Team Alpha
            moved_b = await repo.get_hooper(b_ids[0])
            assert moved_b is not None
            assert moved_b.team_id == team_a

    async def test_trade_event_recorded(self, trade_engine: AsyncEngine):
        """execute_hooper_trade appends a hooper_trade.executed event."""
        async with get_session(trade_engine) as session:
            repo = Repository(session)
            season_id, team_a, team_b, a_ids, b_ids = await _setup_two_teams(repo)

            trade = HooperTrade(
                id="trade-event-1",
                from_team_id=team_a,
                to_team_id=team_b,
                offered_hooper_ids=[a_ids[0]],
                requested_hooper_ids=[b_ids[0]],
                offered_hooper_names=["Alpha-1"],
                requested_hooper_names=["Beta-1"],
                proposed_by="gov-1",
                required_voters=["gov-1"],
                from_team_voters=["gov-1"],
                to_team_voters=[],
                from_team_name="Team Alpha",
                to_team_name="Team Beta",
            )

            await execute_hooper_trade(repo, trade, season_id)

            events = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["hooper_trade.executed"],
            )
            assert len(events) == 1
            assert events[0].payload["id"] == "trade-event-1"


class TestTradeTimingWithSimulation:
    """Tests that hooper trades take effect at the right time relative to rounds.

    Key invariant: a trade executed before a round affects that round's rosters;
    a trade executed mid-round does NOT affect the in-progress round but DOES
    affect the next round.
    """

    async def test_trade_before_round_affects_rosters(self, trade_engine: AsyncEngine):
        """A trade committed before step_round is reflected in the simulation."""
        from pinwheel.core.game_loop import step_round

        async with get_session(trade_engine) as session:
            repo = Repository(session)
            season_id, team_a, team_b, a_ids, b_ids = await _setup_two_teams(repo)

            # Execute trade: Alpha-1 goes to Beta, Beta-1 goes to Alpha
            trade = HooperTrade(
                id="trade-before-round",
                from_team_id=team_a,
                to_team_id=team_b,
                offered_hooper_ids=[a_ids[0]],
                requested_hooper_ids=[b_ids[0]],
                offered_hooper_names=["Alpha-1"],
                requested_hooper_names=["Beta-1"],
                proposed_by="gov-1",
                required_voters=["gov-1"],
                from_team_voters=["gov-1"],
                to_team_voters=[],
                from_team_name="Team Alpha",
                to_team_name="Team Beta",
            )
            await execute_hooper_trade(repo, trade, season_id)

            # Now run the round — the sim should see the traded rosters
            result = await step_round(repo, season_id, round_number=1)

            assert len(result.games) >= 1

            # Verify rosters: Alpha-1 should be playing for Team Beta
            team_a_domain = result.teams_cache[team_a]
            team_b_domain = result.teams_cache[team_b]

            a_hooper_ids = {h.id for h in team_a_domain.hoopers}
            b_hooper_ids = {h.id for h in team_b_domain.hoopers}

            # Alpha-1 (a_ids[0]) was offered → now on Team Beta
            assert a_ids[0] in b_hooper_ids
            assert a_ids[0] not in a_hooper_ids

            # Beta-1 (b_ids[0]) was requested → now on Team Alpha
            assert b_ids[0] in a_hooper_ids
            assert b_ids[0] not in b_hooper_ids

    async def test_trade_mid_round_does_not_affect_current_round(
        self, trade_engine: AsyncEngine
    ):
        """A trade committed in a separate session mid-simulation does not
        change the rosters for the current round.

        We simulate by running the sim phase (which loads teams), then
        executing a trade in a separate session, and verifying the already-loaded
        teams_cache is unaffected.
        """
        from pinwheel.core.game_loop import _phase_simulate_and_govern

        # Set up season in one session
        async with get_session(trade_engine) as session:
            repo = Repository(session)
            season_id, team_a, team_b, a_ids, b_ids = await _setup_two_teams(repo)

        # Run the simulation phase in session 1
        async with get_session(trade_engine) as session:
            repo = Repository(session)
            sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)

        assert sim is not None

        # The sim has loaded teams — Alpha-1 should still be on Team Alpha
        team_a_domain = sim.teams_cache[team_a]
        a_hooper_ids_before = {h.id for h in team_a_domain.hoopers}
        assert a_ids[0] in a_hooper_ids_before

        # Now, in a separate session (simulating a Discord trade accepted mid-round),
        # execute the trade
        async with get_session(trade_engine) as session:
            repo = Repository(session)
            trade = HooperTrade(
                id="trade-mid-round",
                from_team_id=team_a,
                to_team_id=team_b,
                offered_hooper_ids=[a_ids[0]],
                requested_hooper_ids=[b_ids[0]],
                offered_hooper_names=["Alpha-1"],
                requested_hooper_names=["Beta-1"],
                proposed_by="gov-1",
                required_voters=["gov-1"],
                from_team_voters=["gov-1"],
                to_team_voters=[],
                from_team_name="Team Alpha",
                to_team_name="Team Beta",
            )
            await execute_hooper_trade(repo, trade, season_id)

        # The sim's teams_cache is a Python dict of domain objects — already loaded.
        # The trade committed in a different session does NOT retroactively change them.
        team_a_domain_after = sim.teams_cache[team_a]
        a_hooper_ids_after = {h.id for h in team_a_domain_after.hoopers}
        assert a_ids[0] in a_hooper_ids_after  # Still on Alpha in the sim's view

    async def test_trade_takes_effect_next_round(self, trade_engine: AsyncEngine):
        """A trade executed between rounds is visible in the subsequent round."""
        from pinwheel.core.game_loop import step_round

        # Setup
        async with get_session(trade_engine) as session:
            repo = Repository(session)
            season_id, team_a, team_b, a_ids, b_ids = await _setup_two_teams(repo)

        # Play round 1 — no trade yet
        async with get_session(trade_engine) as session:
            repo = Repository(session)
            r1 = await step_round(repo, season_id, round_number=1)

        # Verify Alpha-1 is on Team Alpha in round 1
        a_ids_r1 = {h.id for h in r1.teams_cache[team_a].hoopers}
        assert a_ids[0] in a_ids_r1

        # Execute trade between rounds
        async with get_session(trade_engine) as session:
            repo = Repository(session)
            trade = HooperTrade(
                id="trade-between-rounds",
                from_team_id=team_a,
                to_team_id=team_b,
                offered_hooper_ids=[a_ids[0]],
                requested_hooper_ids=[b_ids[0]],
                offered_hooper_names=["Alpha-1"],
                requested_hooper_names=["Beta-1"],
                proposed_by="gov-1",
                required_voters=["gov-1"],
                from_team_voters=["gov-1"],
                to_team_voters=[],
                from_team_name="Team Alpha",
                to_team_name="Team Beta",
            )
            await execute_hooper_trade(repo, trade, season_id)

        # Play round 2 — trade should be visible
        async with get_session(trade_engine) as session:
            repo = Repository(session)
            r2 = await step_round(repo, season_id, round_number=2)

        # Verify Alpha-1 is now on Team Beta in round 2
        a_ids_r2 = {h.id for h in r2.teams_cache[team_a].hoopers}
        b_ids_r2 = {h.id for h in r2.teams_cache[team_b].hoopers}
        assert a_ids[0] not in a_ids_r2  # Moved away from Alpha
        assert a_ids[0] in b_ids_r2  # Now on Beta

        # And Beta-1 is on Team Alpha
        assert b_ids[0] in a_ids_r2
        assert b_ids[0] not in b_ids_r2

    async def test_get_hoopers_for_team_reflects_trade(self, trade_engine: AsyncEngine):
        """Repository.get_hoopers_for_team returns updated rosters after trade."""
        async with get_session(trade_engine) as session:
            repo = Repository(session)
            season_id, team_a, team_b, a_ids, b_ids = await _setup_two_teams(repo)

            # Before trade: Alpha-1 on Team Alpha
            hoopers_a = await repo.get_hoopers_for_team(team_a)
            assert any(h.id == a_ids[0] for h in hoopers_a)

            # Execute trade
            trade = HooperTrade(
                id="trade-get-hoopers",
                from_team_id=team_a,
                to_team_id=team_b,
                offered_hooper_ids=[a_ids[0]],
                requested_hooper_ids=[b_ids[0]],
                offered_hooper_names=["Alpha-1"],
                requested_hooper_names=["Beta-1"],
                proposed_by="gov-1",
                required_voters=["gov-1"],
                from_team_voters=["gov-1"],
                to_team_voters=[],
                from_team_name="Team Alpha",
                to_team_name="Team Beta",
            )
            await execute_hooper_trade(repo, trade, season_id)

            # After trade: Alpha-1 should be on Team Beta
            hoopers_a_after = await repo.get_hoopers_for_team(team_a)
            hoopers_b_after = await repo.get_hoopers_for_team(team_b)

            a_after_ids = {h.id for h in hoopers_a_after}
            b_after_ids = {h.id for h in hoopers_b_after}

            assert a_ids[0] not in a_after_ids
            assert a_ids[0] in b_after_ids
            assert b_ids[0] in a_after_ids
            assert b_ids[0] not in b_after_ids
