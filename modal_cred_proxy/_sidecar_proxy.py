"""JWT-less credential injector for running as a sandbox sidecar.

Differences vs. `_proxy.credential_injector`:

- No JWT validation. A sidecar is only reachable from its own sandbox's
  network namespace (and any peer sidecars), so authentication is the
  network, not a token.
- Strips `x-api-key` / `authorization` on the way in so the sandbox can
  pass *anything* (or nothing) where an API key would normally go — the
  upstream key is injected here.
- Runnable as ``python -m modal_cred_proxy._sidecar_proxy``.
"""

import argparse
import os

import httpx
import uvicorn
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

_STRIP_REQUEST_HEADERS = frozenset(
    ["host", "x-api-key", "authorization", "content-length"]
)
_STRIP_RESPONSE_HEADERS = frozenset(["transfer-encoding"])


def credential_injector_plain(
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True, help="Upstream hostname, e.g. api.anthropic.com")
    parser.add_argument(
            "--header",
        action="append",
        required=True,
        help="Header to inject as NAME=ENV_VAR (repeatable). The value is read from the named env var.",
    )
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    inject = {}
    for entry in args.header:
        header_name, _, env_var = entry.partition("=")
        if not header_name or not env_var:
            raise SystemExit(f"--header expects NAME=ENV_VAR, got: {entry!r}")
        value = os.environ.get(env_var)
        if value is None:
            raise SystemExit(f"env var {env_var!r} not set in sidecar")
        inject[header_name] = value

    app = credential_injector_plain(args.upstream, inject)
    # 0.0.0.0 so peer containers in the sandbox network can reach this.
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
