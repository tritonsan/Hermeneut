from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.settings import Settings

UNSUPPORTED_SCHEMA_KEYS = {
    "$schema",
    "additionalItems",
    "dependentRequired",
    "dependentSchemas",
    "patternProperties",
    "propertyNames",
    "unevaluatedProperties",
}


def sanitize_openai_payload(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    cleaned = _strip_unsupported_schema_keys(deepcopy(payload))
    cleaned["model"] = cleaned.get("model") or settings.vertex_openai_model

    if cleaned.get("tool_choice") == "required":
        cleaned["tool_choice"] = "auto"

    return cleaned


def _strip_unsupported_schema_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_unsupported_schema_keys(child)
            for key, child in value.items()
            if key not in UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(value, list):
        return [_strip_unsupported_schema_keys(item) for item in value]
    return value


async def forward_chat_completion(
    request: Request,
    payload: dict[str, Any],
    settings: Settings,
) -> JSONResponse | StreamingResponse:
    _authorize_proxy_request(request, settings)
    project_id = settings.google_cloud_project
    if not project_id:
        raise HTTPException(status_code=500, detail="GOOGLE_CLOUD_PROJECT is not configured.")

    cleaned = sanitize_openai_payload(payload, settings)
    token = _get_google_access_token()
    url = (
        "https://aiplatform.googleapis.com/v1/"
        f"projects/{project_id}/locations/{settings.vertex_openai_location}/endpoints/openapi/chat/completions"
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    if cleaned.get("stream"):
        return StreamingResponse(
            _stream_vertex_response(url, headers, cleaned),
            media_type="text/event-stream",
        )

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, headers=headers, json=cleaned)
    return JSONResponse(status_code=response.status_code, content=response.json())


def _authorize_proxy_request(request: Request, settings: Settings) -> None:
    expected = settings.openai_proxy_api_key
    if not expected:
        if settings.environment.lower() == "production":
            raise HTTPException(status_code=503, detail="OpenAI-compatible proxy API key is not configured.")
        return

    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or token != expected:
        raise HTTPException(status_code=401, detail="Invalid proxy API key.")


def _get_google_access_token() -> str:
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


async def _stream_vertex_response(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> AsyncIterator[bytes]:
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                body = await response.aread()
                yield body
                return

            async for chunk in response.aiter_bytes():
                yield chunk
