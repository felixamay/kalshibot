"""Read-only status for the OpenAI-hosted browser. The session id stays on the server."""

from fastapi import APIRouter

router = APIRouter()

_OFF = {
    "connected": False,
    "session_id_present": False,
    "kalshi_loaded": False,
    "last_successful_observation": None,
    "error": "GPT browser is not connected",
}


@router.get("/gpt-browser/status")
async def gpt_browser_status() -> dict:
    from app.main import orchestrator

    visual = getattr(orchestrator, "visual", None)
    hosted = getattr(visual, "hosted", None) if visual else None
    if hosted is None:
        return dict(_OFF)
    return hosted.public_status()
