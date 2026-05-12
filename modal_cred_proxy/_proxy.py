import httpx
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from ._jwt import validate_jwt

# Hop-by-hop and auth headers stripped before forwarding to upstream
_STRIP_REQUEST_HEADERS = frozenset(
    ["host", "x-api-key", "authorization", "content-length"]
)
# Hop-by-hop headers stripped from upstream response
_STRIP_RESPONSE_HEADERS = frozenset(["transfer-encoding"])


def credential_injector(
    egress_secret: str,
    hostname: str,
    header_transformations: dict[str, str],
):
    upstream_host = hostname
    inject_headers = header_transformations

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        if scope["type"] != "http":
            return

        request = Request(scope, receive)
        token = _extract_token(request)

        if not token or not validate_jwt(token, egress_secret):
            await Response("Unauthorized", status_code=401)(scope, receive, send)
            return

        path = request.url.path
        if request.url.query:
            path += f"?{request.url.query}"
        upstream_url = f"https://{upstream_host}{path}"

        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _STRIP_REQUEST_HEADERS
        }
        headers.update(inject_headers)

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    method=request.method,
                    url=upstream_url,
                    headers=headers,
                    content=request.stream(),
                ) as upstream_resp:
                    response_headers = {
                        k: v
                        for k, v in upstream_resp.headers.items()
                        if k.lower() not in _STRIP_RESPONSE_HEADERS
                    }
                    response = StreamingResponse(
                        upstream_resp.aiter_raw(),
                        status_code=upstream_resp.status_code,
                        headers=response_headers,
                    )
                    await response(scope, receive, send)
        except httpx.RequestError as exc:
            await Response(f"Upstream error: {exc}", status_code=502)(
                scope, receive, send
            )

    return app


def _extract_token(request: Request) -> str | None:
    if x_api_key := request.headers.get("x-api-key"):
        return x_api_key
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return None
