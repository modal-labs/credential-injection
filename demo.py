"""Credential injection for Modal Sandboxes via a reverse-proxy sidecar.

A proxy runs as a sibling container in the sandbox's private network, so it is
only reachable from the sandbox itself — network isolation is the auth boundary.

The Anthropic secret is mounted into the sidecar only, never the sandbox.
This example uses Caddy, but any reverse proxy (nginx, Envoy, etc.) works.
"""

import modal

app = modal.App("egress-proxy-demo")

SIDECAR_NAME = "egress-proxy"
SIDECAR_PORT = 8080

SANDBOX_CODE = f"""
import anthropic, os, time

# Give the sidecar a moment to finish starting before the first request.
time.sleep(5)

proxy_url = os.environ["EGRESS_PROXY_URL"]
has_key = "ANTHROPIC_API_KEY" in os.environ
print(f"ANTHROPIC_API_KEY in sandbox env: {{has_key}}")
print(f"Routing requests through: {{proxy_url}}")
print("Calling Anthropic API with api_key='unused' — the sidecar will inject the real key...")

client = anthropic.Anthropic(
    api_key="unused",
    base_url=proxy_url,
)

message = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=256,
    messages=[{{"role": "user", "content": "Say hello in one sentence."}}],
)
print(f"Response: {{message.content[0].text}}")
"""


@app.local_entrypoint()
def main():
    # Must be a pre-built image because `_experimental_sidecars.create`
    # requires `image._object_id` to be set before the sandbox is created.
    # caddy:2 default entrypoint: caddy run --config /etc/caddy/Caddyfile
    sidecar_image = (
        modal.Image.from_registry("caddy:2")
        .add_local_file("Caddyfile", "/etc/caddy/Caddyfile", copy=True)
        .build(app)
    )

    sandbox_image = modal.Image.debian_slim().pip_install("anthropic")

    sandbox = modal.Sandbox.create(
        "python3",
        "-c",
        SANDBOX_CODE,
        image=sandbox_image,
        secrets=[
            modal.Secret.from_dict(
                {"EGRESS_PROXY_URL": f"http://{SIDECAR_NAME}:{SIDECAR_PORT}"}
            )
        ],
        app=app,
    )

    # Start Caddy as a sidecar. The Anthropic key is mounted here only —
    # the sandbox's environment never receives it.
    sandbox._experimental_sidecars.create(
        name=SIDECAR_NAME,
        image=sidecar_image,
        secrets=[modal.Secret.from_name("anthropic-secret")],
    )

    sandbox.wait()
    print(sandbox.stdout.read())
