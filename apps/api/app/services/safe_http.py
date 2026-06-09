from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


class UnsafeExternalUrlError(ValueError):
    pass


class DownloadTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class SafeDownloadResult:
    content: bytes
    final_url: str
    content_type: str


def allowed_host_list(value: str | None) -> tuple[str, ...]:
    return tuple(host.strip().lower() for host in (value or "").split(",") if host.strip())


def validate_external_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeExternalUrlError("unsafe_external_url: only HTTPS URLs are allowed.")
    if parsed.username or parsed.password:
        raise UnsafeExternalUrlError("unsafe_external_url: credentials in URLs are not allowed.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeExternalUrlError("unsafe_external_url: URL host is required.")
    if _is_private_host(host):
        raise UnsafeExternalUrlError("unsafe_external_url: private or local hosts are not allowed.")
    if allowed_hosts and not _host_allowed(host, allowed_hosts):
        raise UnsafeExternalUrlError("unsafe_external_url: host is not in the controlled allowlist.")


async def fetch_limited_bytes(
    url: str,
    *,
    allowed_hosts: tuple[str, ...],
    max_bytes: int,
    timeout: float = 20.0,
    max_redirects: int = 4,
) -> SafeDownloadResult:
    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            validate_external_url(current, allowed_hosts)
            async with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise UnsafeExternalUrlError("unsafe_external_url: redirect without a location.")
                    current = str(response.url.join(location))
                    continue
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise DownloadTooLargeError("download_too_large: source exceeds the configured size limit.")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise DownloadTooLargeError("download_too_large: source exceeds the configured size limit.")
                    chunks.append(chunk)
                return SafeDownloadResult(
                    content=b"".join(chunks),
                    final_url=str(response.url),
                    content_type=response.headers.get("content-type", ""),
                )
    raise UnsafeExternalUrlError("unsafe_external_url: too many redirects.")


def _host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    for allowed in allowed_hosts:
        if host == allowed or host.endswith(f".{allowed}") or host.startswith(allowed):
            return True
    return False


def _is_private_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
