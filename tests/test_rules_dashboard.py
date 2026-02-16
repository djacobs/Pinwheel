"""Tests for the rules governance dashboard — change history, attribution, impact.

Tests cover:
- Governor name attribution in rule change history
- Vote margin display
- Original proposal text display
- Drift bar visualization (distance from default)
- High-drift visual class for significantly changed rules
- Change count badges
- Enhanced repository get_rule_change_timeline method
"""

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


def _hooper_attrs() -> dict[str, int]:
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


async def _seed_season(engine: object) -> tuple[str, list[str]]:
    """Create a league with 4 teams and run 1 round."""
    async with get_session(engine) as session:
        repo = Repository(session)
        league = await repo.create_league("Test League")
        season = await repo.create_season(
            league.id,
            "Season 1",
            starting_ruleset={"quarter_minutes": 3},
        )

        team_ids: list[str] = []
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

        await step_round(repo, season.id, round_number=1)

        games = await repo.get_games_for_round(season.id, 1)
        for g in games:
            await repo.mark_game_presented(g.id)

        await session.commit()
        return season.id, team_ids


async def _create_rule_change_with_vote(
    engine: object,
    season_id: str,
    team_id: str,
    *,
    governor_discord_id: str = "gov-dash-1",
    governor_username: str = "RuleMaker",
    proposal_id: str = "prop-dash-1",
    parameter: str = "three_point_value",
    old_value: int = 3,
    new_value: int = 5,
    round_enacted: int = 2,
    raw_text: str = "Make three-pointers worth 5",
    yes_count: int = 3,
    no_count: int = 1,
) -> str:
    """Helper: create governor, proposal, vote tally, and rule.enacted.

    Returns the governor player ID.
    """
    async with get_session(engine) as session:
        repo = Repository(session)

        player = await repo.get_or_create_player(
            discord_id=governor_discord_id,
            username=governor_username,
        )
        await repo.enroll_player(player.id, team_id, season_id)

        # Proposal submitted
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=proposal_id,
            aggregate_type="proposal",
            season_id=season_id,
            governor_id=player.id,
            team_id=team_id,
            round_number=round_enacted,
            payload={
                "id": proposal_id,
                "raw_text": raw_text,
                "governor_id": player.id,
                "team_id": team_id,
                "tier": 1,
                "status": "submitted",
            },
        )

        # Proposal passed with vote tally
        weighted_yes = float(yes_count)
        weighted_no = float(no_count)
        await repo.append_event(
            event_type="proposal.passed",
            aggregate_id=proposal_id,
            aggregate_type="proposal",
            season_id=season_id,
            round_number=round_enacted,
            payload={
                "proposal_id": proposal_id,
                "weighted_yes": weighted_yes,
                "weighted_no": weighted_no,
                "total_weight": weighted_yes + weighted_no,
                "passed": True,
                "threshold": 0.5,
                "yes_count": yes_count,
                "no_count": no_count,
                "total_eligible": yes_count + no_count,
            },
        )

        # Rule enacted
        await repo.append_event(
            event_type="rule.enacted",
            aggregate_id=proposal_id,
            aggregate_type="rule_change",
            season_id=season_id,
            round_number=round_enacted,
            payload={
                "parameter": parameter,
                "old_value": old_value,
                "new_value": new_value,
                "source_proposal_id": proposal_id,
                "round_enacted": round_enacted,
            },
        )

        # Update season ruleset (copy dict so SQLAlchemy detects change)
        season = await repo.get_season(season_id)
        ruleset_data = dict(season.current_ruleset or {})
        ruleset_data[parameter] = new_value
        season.current_ruleset = ruleset_data

        await session.commit()
        return player.id


class TestRulesGovernanceDashboard:
    """Tests for enhanced governance dashboard on the rules page."""

    async def test_governor_name_in_rule_history(self, app_client: tuple) -> None:
        """Rule change history should show the governor's username."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        await _create_rule_change_with_vote(
            engine,
            season_id,
            team_ids[0],
            governor_username="TheRuleMaker",
        )

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "TheRuleMaker" in r.text

    async def test_vote_margin_in_rule_history(self, app_client: tuple) -> None:
        """Rule change history should show the vote margin."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        await _create_rule_change_with_vote(
            engine,
            season_id,
            team_ids[0],
            yes_count=4,
            no_count=1,
        )

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "rule-change-vote-margin" in r.text
        assert "Passed 4" in r.text

    async def test_proposal_text_in_rule_history(self, app_client: tuple) -> None:
        """Rule change history should show the original proposal text."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        await _create_rule_change_with_vote(
            engine,
            season_id,
            team_ids[0],
            raw_text="Increase three-point value to 5 for more excitement",
        )

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "rule-change-proposal-text" in r.text
        assert "Increase three-point value to 5" in r.text

    async def test_drift_bar_for_changed_rule(self, app_client: tuple) -> None:
        """Changed rules should show a drift bar with percentage."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Three-point value: default 3, range 1-10, changing to 8
        # Drift = |8 - 3| / (10 - 1) * 100 = 55%
        await _create_rule_change_with_vote(
            engine,
            season_id,
            team_ids[0],
            parameter="three_point_value",
            old_value=3,
            new_value=8,
        )

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "rule-drift-bar" in r.text
        assert "% from default" in r.text

    async def test_high_drift_class_applied(self, app_client: tuple) -> None:
        """Rules with >= 50% drift should get the high-drift class."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Three-point value: default 3, range 1-10, changing to 8
        # Drift = |8 - 3| / (10 - 1) * 100 = 55% => high-drift
        await _create_rule_change_with_vote(
            engine,
            season_id,
            team_ids[0],
            parameter="three_point_value",
            old_value=3,
            new_value=8,
        )

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "rule-card-high-drift" in r.text

    async def test_no_high_drift_for_small_change(self, app_client: tuple) -> None:
        """Rules with small changes should NOT get the high-drift class."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Three-point value: default 3, range 1-10, changing to 4
        # Drift = |4 - 3| / (10 - 1) * 100 = 11% => NOT high-drift
        await _create_rule_change_with_vote(
            engine,
            season_id,
            team_ids[0],
            parameter="three_point_value",
            old_value=3,
            new_value=4,
        )

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "rule-card-high-drift" not in r.text

    async def test_change_count_badge(self, app_client: tuple) -> None:
        """Rules with changes should show a change count badge."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Two changes to three_point_value
        await _create_rule_change_with_vote(
            engine,
            season_id,
            team_ids[0],
            governor_discord_id="gov-count-1",
            governor_username="Counter1",
            proposal_id="prop-count-1",
            parameter="three_point_value",
            old_value=3,
            new_value=4,
            round_enacted=1,
            raw_text="Three pointers worth 4",
        )
        await _create_rule_change_with_vote(
            engine,
            season_id,
            team_ids[0],
            governor_discord_id="gov-count-2",
            governor_username="Counter2",
            proposal_id="prop-count-2",
            parameter="three_point_value",
            old_value=4,
            new_value=5,
            round_enacted=2,
            raw_text="Three pointers worth 5",
        )

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "2 changes this season" in r.text

    async def test_no_change_count_for_unchanged_rules(self, app_client: tuple) -> None:
        """Unchanged rules should NOT show a change count badge."""
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "changes this season" not in r.text

    async def test_change_timeline_section_title(self, app_client: tuple) -> None:
        """The history section should be titled Change Timeline."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        await _create_rule_change_with_vote(
            engine,
            season_id,
            team_ids[0],
        )

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "Change Timeline" in r.text

    async def test_change_history_header_in_card(self, app_client: tuple) -> None:
        """Rule card with history should show Change History header."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        await _create_rule_change_with_vote(
            engine,
            season_id,
            team_ids[0],
        )

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "rule-history-header" in r.text
        assert "Change History" in r.text


class TestRuleChangeTimelineRepository:
    """Tests for the enhanced get_rule_change_timeline repository method."""

    async def test_timeline_includes_governor_name(self, app_client: tuple) -> None:
        """Timeline entries should include the governor username."""
        _, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            player = await repo.get_or_create_player(
                discord_id="timeline-gov-1",
                username="TimelineGov",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-tl-1",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                round_number=1,
                payload={
                    "id": "prop-tl-1",
                    "raw_text": "Test proposal",
                    "governor_id": player.id,
                    "team_id": team_ids[0],
                    "tier": 1,
                    "status": "submitted",
                },
            )

            await repo.append_event(
                event_type="rule.enacted",
                aggregate_id="prop-tl-1",
                aggregate_type="rule_change",
                season_id=season_id,
                round_number=1,
                payload={
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "source_proposal_id": "prop-tl-1",
                    "round_enacted": 1,
                },
            )

            await session.commit()

        async with get_session(engine) as session:
            repo = Repository(session)
            timeline = await repo.get_rule_change_timeline(season_id)

            assert len(timeline) == 1
            entry = timeline[0]
            assert entry["governor_name"] == "TimelineGov"
            assert entry["governor_id"] == player.id

    async def test_timeline_includes_vote_margin(self, app_client: tuple) -> None:
        """Timeline entries should include vote margin."""
        _, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            player = await repo.get_or_create_player(
                discord_id="timeline-gov-2",
                username="VoterGov",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-tl-2",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                round_number=1,
                payload={
                    "id": "prop-tl-2",
                    "raw_text": "Vote test",
                    "governor_id": player.id,
                    "team_id": team_ids[0],
                    "tier": 1,
                    "status": "submitted",
                },
            )

            await repo.append_event(
                event_type="proposal.passed",
                aggregate_id="prop-tl-2",
                aggregate_type="proposal",
                season_id=season_id,
                round_number=1,
                payload={
                    "proposal_id": "prop-tl-2",
                    "weighted_yes": 3.0,
                    "weighted_no": 1.0,
                    "total_weight": 4.0,
                    "passed": True,
                    "threshold": 0.5,
                    "yes_count": 3,
                    "no_count": 1,
                    "total_eligible": 4,
                },
            )

            await repo.append_event(
                event_type="rule.enacted",
                aggregate_id="prop-tl-2",
                aggregate_type="rule_change",
                season_id=season_id,
                round_number=1,
                payload={
                    "parameter": "shot_clock_seconds",
                    "old_value": 24,
                    "new_value": 20,
                    "source_proposal_id": "prop-tl-2",
                    "round_enacted": 1,
                },
            )

            await session.commit()

        async with get_session(engine) as session:
            repo = Repository(session)
            timeline = await repo.get_rule_change_timeline(season_id)

            assert len(timeline) == 1
            entry = timeline[0]
            assert entry["vote_yes"] == 3.0
            assert entry["vote_no"] == 1.0
            assert entry["vote_margin"] is not None
            assert "3" in entry["vote_margin"]
            assert "1" in entry["vote_margin"]

    async def test_timeline_includes_raw_text(self, app_client: tuple) -> None:
        """Timeline entries should include original proposal text."""
        _, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            player = await repo.get_or_create_player(
                discord_id="timeline-gov-3",
                username="TextGov",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-tl-3",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                round_number=1,
                payload={
                    "id": "prop-tl-3",
                    "raw_text": "Ban dunking after halftime",
                    "governor_id": player.id,
                    "team_id": team_ids[0],
                    "tier": 1,
                    "status": "submitted",
                },
            )

            await repo.append_event(
                event_type="rule.enacted",
                aggregate_id="prop-tl-3",
                aggregate_type="rule_change",
                season_id=season_id,
                round_number=1,
                payload={
                    "parameter": "elam_margin",
                    "old_value": 15,
                    "new_value": 20,
                    "source_proposal_id": "prop-tl-3",
                    "round_enacted": 1,
                },
            )

            await session.commit()

        async with get_session(engine) as session:
            repo = Repository(session)
            timeline = await repo.get_rule_change_timeline(season_id)

            assert len(timeline) == 1
            entry = timeline[0]
            assert entry["raw_text"] == "Ban dunking after halftime"

    async def test_timeline_without_vote_data(self, app_client: tuple) -> None:
        """Timeline should handle missing vote outcome events."""
        _, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            player = await repo.get_or_create_player(
                discord_id="timeline-gov-4",
                username="NoVoteGov",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-tl-4",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                round_number=1,
                payload={
                    "id": "prop-tl-4",
                    "raw_text": "No vote data proposal",
                    "governor_id": player.id,
                    "team_id": team_ids[0],
                    "tier": 1,
                    "status": "submitted",
                },
            )

            await repo.append_event(
                event_type="rule.enacted",
                aggregate_id="prop-tl-4",
                aggregate_type="rule_change",
                season_id=season_id,
                round_number=1,
                payload={
                    "parameter": "quarter_minutes",
                    "old_value": 10,
                    "new_value": 8,
                    "source_proposal_id": "prop-tl-4",
                    "round_enacted": 1,
                },
            )

            await session.commit()

        async with get_session(engine) as session:
            repo = Repository(session)
            timeline = await repo.get_rule_change_timeline(season_id)

            assert len(timeline) == 1
            entry = timeline[0]
            assert entry["vote_yes"] == 0.0
            assert entry["vote_no"] == 0.0
            assert entry["vote_margin"] is None
            assert entry["governor_name"] == "NoVoteGov"
