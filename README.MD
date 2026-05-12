This repo demonstrates an egress proxy built on Modal that injects credentials into outbound requests from sandboxes, without exposing those credentials to sandbox code.

## Use case

Modal Sandboxes run untrusted or user-supplied code. This proxy lets that code call upstream APIs (e.g. Anthropic) without the sandbox ever seeing the real API key. The sandbox only holds a short-lived JWT; the proxy validates it and injects the real credentials before forwarding the request.

## How it works

### Deploying a proxy

Create an egress_proxy Modal function:

```python
import os

from modal_cred_proxy import credential_injector

@app.function(secrets=[
    modal.Secret.from_name("modal_egress_proxy_secret"),
    modal.Secret.from_name("anthropic_secret")
])
@modal.asgi_app()
def proxy_function():
    anthropic_key = os.environ['ANTHROPIC_API_KEY']
    egress_secret = os.environ['MODAL_EGRESS_PROXY_SECRET']

    return credential_injector(
        egress_secret,        # JWT symmetric secret
        'api.anthropic.com',  # upstream hostname
        {'x-api-key': anthropic_key},  # headers to inject
    )
```

`credential_injector` returns an ASGI app that validates the JWT
and injects credentials. The deployed function must be wrapped with
`@modal.asgi_app()` and `@app.function`.

### Creating a sandbox

```python
from modal_cred_proxy import create_jwt

egress_jwt = create_jwt(
    "<<symmetric_secret_for_egress_proxy>>", # must match the first argument passed to `credential_injector`.
    validity_seconds=3600,
)

secret = modal.Secret.from_dict({
    "EGRESS_JWT": egress_jwt,
    "EGRESS_PROXY_URL": proxy_function.get_web_url(),
})

sb = Sandbox.create(
    secrets=[secret],
)
```

### Using the proxy from sandbox code

Inside the sandbox, point the API client at the proxy and pass the JWT as the API key:

```python
import anthropic, os

client = anthropic.Anthropic(
    api_key=os.environ["EGRESS_JWT"],
    base_url=os.environ["EGRESS_PROXY_URL"],
)

message = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=256,
    messages=[{"role": "user", "content": "Say hello in one sentence."}],
)
print(message.content[0].text)
```

The proxy accepts the JWT in either the `x-api-key` header or as an `Authorization: Bearer` token, validates it, strips it, then forwards the request upstream with the real credentials injected.

## Authentication

Sandboxes authenticate with the proxy using short-lived JWTs signed with HS256.

Sandbox code never sees the upstream API key — only its own JWT and the proxy URL.

## Demo

```bash
uv run demo.py
```

This will:
1. Deploy an egress proxy for `api.anthropic.com`
2. Start a sandbox configured to use the proxy
3. Send a prompt to Claude from inside the sandbox and print the response
