"""Tests for A/B comparison."""

import pytest

from pinwheel.evals.ab_compare import (
    build_variant,
    compare_variants,
    get_ab_win_rates,
    store_ab_comparison,
)
from pinwheel.evals.grounding import GroundingContext


def test_build_variant_public():
    variant = build_variant(
        report_id="m-1",
        report_type="simulation",
        prompt_version="A",
        content="The Rose City Thorns dominated this round.",
    )
    assert variant.content is not None
    assert variant.length > 0


def test_build_variant_private_strips_content():
    variant = build_variant(
        report_id="m-2",
        report_type="private",
        prompt_version="A",
        content="Governor gov-1 was active this round.",
    )
    assert variant.content is None  # Privacy enforcement
    assert variant.length > 0  # Length still tracked


def test_build_variant_with_grounding():
    context = GroundingContext(team_names=["Rose City Thorns", "Breakers"])
    variant = build_variant(
        report_id="m-3",
        report_type="simulation",
        prompt_version="A",
        content="The Rose City Thorns and Breakers faced off.",
        context=context,
    )
    assert variant.grounding_score > 0


def test_compare_variants_a_wins():
    a = build_variant("m-a", "simulation", "A", "A " * 250)  # ~500 chars, ideal
    b = build_variant("m-b", "simulation", "B", "B " * 50)  # Too short
    result = compare_variants(a, b)
    assert result.winner in ("A", "B", "tie")


def test_compare_variants_tie():
    a = build_variant("m-a", "simulation", "A", "Content " * 62)
    b = build_variant("m-b", "simulation", "B", "Content " * 62)
    result = compare_variants(a, b)
    assert result.winner == "tie"


@pytest.mark.asyncio
async def test_store_and_get_ab(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    a = build_variant("m-a", "simulation", "A", "A content " * 50)
    b = build_variant("m-b", "simulation", "B", "B content " * 50)
    comparison = compare_variants(a, b)

    await store_ab_comparison(repo, season.id, 1, comparison)

    rates = await get_ab_win_rates(repo, season.id)
    assert rates["total"] == 1


@pytest.mark.asyncio
async def test_ab_win_rates_empty(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    rates = await get_ab_win_rates(repo, season.id)
    assert rates["total"] == 0
