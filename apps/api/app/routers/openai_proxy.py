from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.dependencies import get_app_settings
from app.services.openai_proxy import forward_chat_completion
from app.settings import Settings

router = APIRouter(prefix="/v1", tags=["OpenAI-compatible Vertex proxy"])


@router.post("/chat/completions", response_model=None)
async def chat_completions(
    request: Request,
    payload: dict[str, Any],
    settings: Settings = Depends(get_app_settings),
) -> Response:
    return await forward_chat_completion(request, payload, settings)
