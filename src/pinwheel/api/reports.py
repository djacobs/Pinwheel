"""Report API endpoints — retrieval with access control."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinwheel.api.deps import RepoDep

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/round/{season_id}/{round_number}")
async def get_round_reports(
    season_id: str,
    round_number: int,
    repo: RepoDep,
    report_type: str | None = None,
) -> dict:
    """Get all public reports for a round. Private reports are excluded."""
    rows = await repo.get_reports_for_round(season_id, round_number, report_type)
    # Filter out private reports from public endpoint
    public = [r for r in rows if r.report_type != "private"]
    return {
        "data": [
            {
                "id": r.id,
                "report_type": r.report_type,
                "round_number": r.round_number,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in public
        ]
    }


@router.get("/private/{season_id}/{governor_id}")
async def get_private_reports(
    season_id: str,
    governor_id: str,
    repo: RepoDep,
    round_number: int | None = None,
) -> dict:
    """Get private reports for a specific governor.

    Access control: in production, this would verify the requester IS the governor.
    For hackathon, we trust the governor_id parameter.
    """
    rows = await repo.get_private_reports(season_id, governor_id, round_number)
    return {
        "data": [
            {
                "id": r.id,
                "report_type": r.report_type,
                "round_number": r.round_number,
                "governor_id": r.governor_id,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.get("/latest/{season_id}")
async def get_latest_reports(season_id: str, repo: RepoDep) -> dict:
    """Get the most recent simulation and governance reports."""
    sim = await repo.get_latest_report(season_id, "simulation")
    gov = await repo.get_latest_report(season_id, "governance")

    result: dict = {}
    if sim:
        result["simulation"] = {
            "id": sim.id,
            "round_number": sim.round_number,
            "content": sim.content,
            "created_at": sim.created_at.isoformat() if sim.created_at else None,
        }
    if gov:
        result["governance"] = {
            "id": gov.id,
            "round_number": gov.round_number,
            "content": gov.content,
            "created_at": gov.created_at.isoformat() if gov.created_at else None,
        }

    if not result:
        raise HTTPException(status_code=404, detail="No reports found for this season")

    return {"data": result}
