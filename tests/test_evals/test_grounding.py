"""Tests for grounding check."""

from pinwheel.evals.grounding import GroundingContext, build_grounding_context, check_grounding


def test_grounded_report():
    context = GroundingContext(
        team_names=["Rose City Thorns", "Burnside Breakers"],
        agent_names=["Thorn Hooper"],
        rule_params=["elam_margin"],
    )
    result = check_grounding(
        "The Rose City Thorns showed strong play this round.",
        context,
        report_id="m-1",
        report_type="simulation",
    )
    assert result.grounded is True
    assert result.entities_found >= 1


def test_ungrounded_report():
    context = GroundingContext(
        team_names=["Rose City Thorns"],
        agent_names=["Hooper X"],
        rule_params=["elam_margin"],
    )
    result = check_grounding(
        "Generic basketball content with no real names.",
        context,
        report_id="m-2",
        report_type="simulation",
    )
    assert result.grounded is False
    assert result.entities_found == 0


def test_empty_context():
    context = GroundingContext()
    result = check_grounding(
        "Any content here.",
        context,
        report_id="m-3",
        report_type="simulation",
    )
    assert result.grounded is True  # No entities expected = trivially grounded
    assert result.entities_expected == 0


def test_case_insensitive_matching():
    context = GroundingContext(team_names=["Hawthorne Hammers"])
    result = check_grounding(
        "The hawthorne hammers had a strong showing.",
        context,
        report_id="m-4",
        report_type="simulation",
    )
    assert result.entities_found >= 1


def test_build_grounding_context():
    teams = [{"name": "Team A"}, {"name": "Team B"}]
    agents = [{"name": "Hooper 1"}, {"name": "Hooper 2"}]
    ruleset = {"elam_margin": 13, "shot_clock_seconds": 12}
    context = build_grounding_context(teams, agents, ruleset)
    assert len(context.team_names) == 2
    assert len(context.agent_names) == 2
    assert len(context.rule_params) == 2


def test_multiple_entities():
    context = GroundingContext(
        team_names=["Rose City Thorns", "Burnside Breakers"],
        agent_names=["Lightning", "Thunder"],
    )
    result = check_grounding(
        "Rose City Thorns vs Burnside Breakers: Lightning scored 20 while Thunder defended.",
        context,
        report_id="m-5",
        report_type="simulation",
    )
    assert result.entities_found == 4
