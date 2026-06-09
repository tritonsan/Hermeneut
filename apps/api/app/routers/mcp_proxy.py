import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.settings import get_settings

router = APIRouter(tags=["Elastic MCP proxy"])


def _validate_proxy_token(request: Request, expected_token: str | None) -> None:
    if not expected_token:
        if get_settings().environment.lower() == "production":
            raise HTTPException(status_code=503, detail="MCP proxy token is not configured.")
        return

    provided_token = request.query_params.get("token") or request.headers.get("x-hermeneut-mcp-token")
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid MCP proxy token.")


@router.post("/mcp")
async def proxy_elastic_mcp(request: Request) -> Response:
    settings = get_settings()
    if not settings.elastic_mcp_endpoint or not settings.elastic_mcp_api_key:
        raise HTTPException(status_code=503, detail="Elastic MCP is not configured.")

    _validate_proxy_token(request, settings.mcp_proxy_token)

    body = await request.body()
    headers = {
        "Authorization": f"ApiKey {settings.elastic_mcp_api_key}",
        "Accept": request.headers.get("accept", "application/json, text/event-stream"),
        "Content-Type": request.headers.get("content-type", "application/json"),
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.post(settings.elastic_mcp_endpoint, headers=headers, content=body)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Elastic MCP upstream request failed.") from exc

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


@router.get("/mcp/health")
def mcp_proxy_health(request: Request) -> dict[str, str]:
    settings = get_settings()
    _validate_proxy_token(request, settings.mcp_proxy_token)
    if not settings.elastic_mcp_endpoint:
        return {"status": "not-configured"}
    if not settings.elastic_mcp_api_key:
        return {"status": "configured"}
    return {"status": "configured", "upstream": "elastic-agent-builder"}
