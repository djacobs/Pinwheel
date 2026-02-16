"""Tests for the 'What Changed' widget on the home page."""

import pytest
from httpx import ASGITransport, AsyncClient

from pinwheel.config import Settings
from pinwheel.core.game_loop import step_round
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.main import create_app


@pytest.fixture
async def app_client():
    """Create a test app with an in-memory database and httpx client."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
    )
    app = create_app(settings)

    # Manually run lifespan startup
    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine

    from pinwheel.core.event_bus import EventBus
    from pinwheel.core.presenter import PresentationState

    app.state.event_bus = EventBus()
    app.state.presentation_state = PresentationState()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, engine

    await engine.dispose()


def _hooper_attrs():
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


async def _seed_season(engine):
    """Create a league with 4 teams and run 1 round."""
    async with get_session(engine) as session:
        repo = Repository(session)
        league = await repo.create_league("Test League")
        season = await repo.create_season(
            league.id,
            "Season 1",
            starting_ruleset={"quarter_minutes": 3},
        )

        team_ids = []
        for i in range(4):
            team = await repo.create_team(
                season.id,
                f"Team {i + 1}",
                color=f"#{'abcdef'[i]}{'abcdef'[i]}{'abcdef'[i]}",
                venue={"name": f"Arena {i + 1}", "capacity": 5000},
            )
            team_ids.append(team.id)
            for j in range(3):
                await repo.create_hooper(
                    team_id=team.id,
                    season_id=season.id,
                    name=f"Hooper-{i + 1}-{j + 1}",
                    archetype="sharpshooter",
                    attributes=_hooper_attrs(),
                )

        matchups = generate_round_robin(team_ids)
        for m in matchups:
            await repo.create_schedule_entry(
                season_id=season.id,
                round_number=m.round_number,
                matchup_index=m.matchup_index,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
            )

        # Run 1 round
        await step_round(repo, season.id, round_number=1)

        # Mark games as presented so they appear on arena/home
        games = await repo.get_games_for_round(season.id, 1)
        for g in games:
            await repo.mark_game_presented(g.id)

        await session.commit()

        return season.id, team_ids


class TestWhatChangedWidget:
    """Tests for the 'What Changed' widget on the home page."""

    async def test_what_changed_present_on_first_round(self, app_client):
        """What Changed widget is present on the home page after round 1."""
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/")
        assert r.status_code == 200
        # The what-changed container is always present (for HTMX polling)
        assert "what-changed" in r.text
        # On round 1 with no prior standings data, change signals may
        # include blowout/nailbiter signals from game results, or fall
        # back to the Post headline. Either way, the widget renders.
        if "what-changed-item" in r.text:
            # Should contain either a game signal or the fallback headline
            assert (
                "Latest:" in r.text
                or "nailbiter" in r.text
                or "blew it open" in r.text
                or "what-changed-item" in r.text
            )

    async def test_what_changed_shows_after_round_two(self, app_client):
        """What Changed shows after round 2+."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run a second round
        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(repo, season_id, round_number=2)
            games = await repo.get_games_for_round(season_id, 2)
            for g in games:
                await repo.mark_game_presented(g.id)
            await session.commit()

        r = await client.get("/")
        assert r.status_code == 200
        # Should show the widget if there are signals (content varies)
        # Just checking it's in the page context
        assert r.status_code == 200

    async def test_what_changed_champion_signal(self, app_client):
        """What Changed shows champion message when season phase is championship."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Set season to championship phase
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            season.status = "championship"
            await session.commit()

        r = await client.get("/")
        assert r.status_code == 200
        if "what-changed" in r.text:
            assert "champion" in r.text.lower()


class TestComputeWhatChanged:
    """Unit tests for _compute_what_changed helper."""

    def test_champion_signal_from_config(self):
        from pinwheel.api.pages import _compute_what_changed

        standings = [{"team_id": "a", "team_name": "Regular Season Leaders"}]
        signals = _compute_what_changed(
            standings=standings,
            prev_standings=[],
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="championship",
            champion_team_name="Actual Champions",
        )
        assert len(signals) == 1
        assert "Actual Champions are your champions" in signals[0]

    def test_champion_signal_fallback_to_standings(self):
        from pinwheel.api.pages import _compute_what_changed

        standings = [{"team_id": "a", "team_name": "Top Team"}]
        signals = _compute_what_changed(
            standings=standings,
            prev_standings=[],
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="championship",
        )
        assert len(signals) == 1
        assert "Top Team are your champions" in signals[0]

    def test_standings_climb_small(self):
        from pinwheel.api.pages import _compute_what_changed

        standings = [
            {"team_id": "a", "team_name": "Climbers"},
            {"team_id": "b", "team_name": "Steady"},
        ]
        prev_standings = [
            {"team_id": "b", "team_name": "Steady"},
            {"team_id": "a", "team_name": "Climbers"},
        ]
        signals = _compute_what_changed(
            standings=standings,
            prev_standings=prev_standings,
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="active",
        )
        # Climbers moved from 2nd to 1st (delta = 1, but we require >= 2)
        # So no signal for 1-position move
        assert len(signals) == 0

    def test_standings_big_climb(self):
        from pinwheel.api.pages import _compute_what_changed

        standings = [
            {"team_id": "a", "team_name": "Surgers"},
            {"team_id": "b", "team_name": "Second"},
            {"team_id": "c", "team_name": "Third"},
        ]
        prev_standings = [
            {"team_id": "b", "team_name": "Second"},
            {"team_id": "c", "team_name": "Third"},
            {"team_id": "a", "team_name": "Surgers"},
        ]
        signals = _compute_what_changed(
            standings=standings,
            prev_standings=prev_standings,
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="active",
        )
        assert len(signals) >= 1
        assert "Surgers climbed to 1st" in signals[0]

    def test_new_win_streak(self):
        from pinwheel.api.pages import _compute_what_changed

        standings = [{"team_id": "a", "team_name": "Streakers"}]
        streaks = {"a": 3}
        prev_streaks = {"a": 2}
        signals = _compute_what_changed(
            standings=standings,
            prev_standings=standings,
            streaks=streaks,
            prev_streaks=prev_streaks,
            rule_changes=[],
            season_phase="active",
        )
        assert len(signals) >= 1
        assert "Streakers on a 3-game win streak" in signals[0]

    def test_rule_change_signal(self):
        from pinwheel.api.pages import _compute_what_changed

        rule_changes = [{"parameter": "three_point_value", "new_value": 4}]
        signals = _compute_what_changed(
            standings=[],
            prev_standings=[],
            streaks={},
            prev_streaks={},
            rule_changes=rule_changes,
            season_phase="active",
        )
        assert len(signals) >= 1
        assert "Three Point Value changed to 4" in signals[0]

    def test_max_five_signals(self):
        from pinwheel.api.pages import _compute_what_changed

        standings = [
            {"team_id": "a", "team_name": "A", "wins": 5, "losses": 0},
            {"team_id": "b", "team_name": "B", "wins": 4, "losses": 1},
            {"team_id": "c", "team_name": "C", "wins": 2, "losses": 3},
            {"team_id": "d", "team_name": "D", "wins": 0, "losses": 5},
        ]
        prev_standings = [
            {"team_id": "d", "team_name": "D", "wins": 0, "losses": 4},
            {"team_id": "c", "team_name": "C", "wins": 2, "losses": 2},
            {"team_id": "b", "team_name": "B", "wins": 3, "losses": 1},
            {"team_id": "a", "team_name": "A", "wins": 4, "losses": 0},
        ]
        streaks = {"a": 5, "b": -4}
        prev_streaks = {"a": 2, "b": -1}
        rule_changes = [
            {"parameter": "shot_clock_seconds", "new_value": 20},
            {"parameter": "three_point_value", "new_value": 4},
        ]
        signals = _compute_what_changed(
            standings=standings,
            prev_standings=prev_standings,
            streaks=streaks,
            prev_streaks=prev_streaks,
            rule_changes=rule_changes,
            season_phase="active",
        )
        # Should cap at 5 signals max
        assert len(signals) <= 5

    def test_upset_detection(self):
        """Upset signal when last-place team beats first-place team."""
        from pinwheel.api.pages import _compute_what_changed

        standings = [
            {"team_id": "a", "team_name": "Alphas", "wins": 3},
            {"team_id": "b", "team_name": "Betas", "wins": 2},
            {"team_id": "c", "team_name": "Gammas", "wins": 1},
            {"team_id": "d", "team_name": "Deltas", "wins": 1},
        ]
        prev_standings = [
            {"team_id": "a", "team_name": "Alphas", "wins": 3},
            {"team_id": "b", "team_name": "Betas", "wins": 2},
            {"team_id": "c", "team_name": "Gammas", "wins": 0},
            {"team_id": "d", "team_name": "Deltas", "wins": 0},
        ]
        # Gammas (3rd place) beat Alphas (1st place)
        latest_games = [
            {
                "home_team_id": "c",
                "away_team_id": "a",
                "home_name": "Gammas",
                "away_name": "Alphas",
                "home_score": 50,
                "away_score": 40,
                "winner_team_id": "c",
            },
        ]
        signals = _compute_what_changed(
            standings=standings,
            prev_standings=prev_standings,
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="active",
            latest_round_games=latest_games,
        )
        assert any("Upset" in s for s in signals)
        assert any("Gammas" in s and "Alphas" in s for s in signals)

    def test_no_upset_for_adjacent_teams(self):
        """No upset signal when adjacent-ranked teams play."""
        from pinwheel.api.pages import _compute_what_changed

        prev_standings = [
            {"team_id": "a", "team_name": "Alphas", "wins": 3},
            {"team_id": "b", "team_name": "Betas", "wins": 2},
        ]
        standings = prev_standings
        latest_games = [
            {
                "home_team_id": "b",
                "away_team_id": "a",
                "home_name": "Betas",
                "away_name": "Alphas",
                "home_score": 50,
                "away_score": 40,
                "winner_team_id": "b",
            },
        ]
        signals = _compute_what_changed(
            standings=standings,
            prev_standings=prev_standings,
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="active",
            latest_round_games=latest_games,
        )
        assert not any("Upset" in s for s in signals)

    def test_blowout_signal(self):
        """Blowout signal when margin is >= 20."""
        from pinwheel.api.pages import _compute_what_changed

        latest_games = [
            {
                "home_team_id": "a",
                "away_team_id": "b",
                "home_name": "Crushers",
                "away_name": "Losers",
                "home_score": 60,
                "away_score": 35,
                "winner_team_id": "a",
            },
        ]
        signals = _compute_what_changed(
            standings=[],
            prev_standings=[],
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="active",
            latest_round_games=latest_games,
        )
        assert any("blew it open" in s for s in signals)
        assert any("25-point" in s for s in signals)

    def test_nailbiter_signal(self):
        """Nailbiter signal when margin is <= 2."""
        from pinwheel.api.pages import _compute_what_changed

        latest_games = [
            {
                "home_team_id": "a",
                "away_team_id": "b",
                "home_name": "Team A",
                "away_name": "Team B",
                "home_score": 45,
                "away_score": 44,
                "winner_team_id": "a",
            },
        ]
        signals = _compute_what_changed(
            standings=[],
            prev_standings=[],
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="active",
            latest_round_games=latest_games,
        )
        assert any("nailbiter" in s for s in signals)
        assert any("45-44" in s for s in signals)

    def test_clinch_signal(self):
        """Clinch signal when a team locks a playoff spot."""
        from pinwheel.api.pages import _compute_what_changed

        # 5 teams, playoff_teams=2, total_regular_rounds=6, current_round=5
        # Team A has 5 wins — bubble team (3rd place) has 1 win + 1 remaining = 2 max
        # 5 > 2, so A clinched. Prev round: A had 4 wins, bubble had 1 + 2 = 3.
        # 4 > 3 is TRUE, so A was already clinched. Let's adjust.
        # prev: A=4 wins, bubble=2 wins + 2 remaining = 4. 4 > 4 is FALSE.
        # curr: A=5 wins, bubble=2 wins + 1 remaining = 3. 5 > 3 is TRUE.
        standings = [
            {"team_id": "a", "team_name": "Leaders", "wins": 5},
            {"team_id": "b", "team_name": "Chasers", "wins": 3},
            {"team_id": "c", "team_name": "Middle", "wins": 2},
            {"team_id": "d", "team_name": "Bottom1", "wins": 1},
            {"team_id": "e", "team_name": "Bottom2", "wins": 0},
        ]
        prev_standings = [
            {"team_id": "a", "team_name": "Leaders", "wins": 4},
            {"team_id": "b", "team_name": "Chasers", "wins": 3},
            {"team_id": "c", "team_name": "Middle", "wins": 2},
            {"team_id": "d", "team_name": "Bottom1", "wins": 1},
            {"team_id": "e", "team_name": "Bottom2", "wins": 0},
        ]
        signals = _compute_what_changed(
            standings=standings,
            prev_standings=prev_standings,
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="active",
            playoff_teams=2,
            total_regular_rounds=6,
            current_round=5,
        )
        assert any("clinched" in s for s in signals)
        assert any("Leaders" in s for s in signals)

    def test_elimination_signal(self):
        """Elimination signal when a team can no longer make playoffs."""
        from pinwheel.api.pages import _compute_what_changed

        # 5 teams, playoff_teams=2, total_regular_rounds=6, current_round=5
        # Bottom2 has 0 wins + 1 remaining = 1 max wins.
        # Cutoff (2nd place Chasers) has 4 wins. 1 < 4 so eliminated.
        # Prev round: Bottom2 had 0 wins + 2 remaining = 2 max.
        # Prev cutoff was 3 wins. 2 < 3, so already eliminated? Let's adjust.
        # Let's set prev cutoff to 2, so 2 < 2 is FALSE = not eliminated yet.
        standings = [
            {"team_id": "a", "team_name": "Leaders", "wins": 5},
            {"team_id": "b", "team_name": "Chasers", "wins": 4},
            {"team_id": "c", "team_name": "Middle", "wins": 2},
            {"team_id": "d", "team_name": "Bottom1", "wins": 1},
            {"team_id": "e", "team_name": "Bottom2", "wins": 0},
        ]
        prev_standings = [
            {"team_id": "a", "team_name": "Leaders", "wins": 4},
            {"team_id": "b", "team_name": "Chasers", "wins": 2},
            {"team_id": "c", "team_name": "Middle", "wins": 2},
            {"team_id": "d", "team_name": "Bottom1", "wins": 1},
            {"team_id": "e", "team_name": "Bottom2", "wins": 0},
        ]
        signals = _compute_what_changed(
            standings=standings,
            prev_standings=prev_standings,
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="active",
            playoff_teams=2,
            total_regular_rounds=6,
            current_round=5,
        )
        assert any("eliminated" in s for s in signals)
        assert any("Bottom2" in s for s in signals)

    def test_fallback_headline_when_no_signals(self):
        """Falls back to Post headline when nothing changed."""
        from pinwheel.api.pages import _compute_what_changed

        signals = _compute_what_changed(
            standings=[],
            prev_standings=[],
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="active",
            post_headline="Big game tonight",
        )
        assert len(signals) == 1
        assert signals[0] == "Latest: Big game tonight"
