"""Credential injection via a Sandbox sidecar container (vs. demo.py's deployed proxy function).

The proxy runs as a sibling container in the sandbox's private network — it is
only reachable from the sandbox itself, so:

  - No JWT auth is needed (the network is the auth boundary).
  - No `@modal.asgi_app()` / public URL / Modal Function deployment.
  - The sandbox addresses the proxy by container name ("egress-proxy"),
    resolved via the runtime's per-task /etc/hosts management.

The Anthropic secret is mounted into the sidecar only, never the sandbox.
"""

import modal

from modal_cred_proxy import credential_injector_plain  # noqa: F401  (referenced via __all__)

app = modal.App("egress-proxy-sidecar-demo")

SIDECAR_NAME = "egress-proxy"
SIDECAR_PORT = 8080

# The sandbox reaches the proxy by hostname (sidecar container name) on the
# sandbox-local network. No public URL, no JWT.
SANDBOX_CODE = f"""
import anthropic, os, time

print("sleeping for 5 secs...")

time.sleep(5)

client = anthropic.Anthropic(
    api_key="unused-the-proxy-injects-the-real-one",
    base_url=os.environ["EGRESS_PROXY_URL"],
)

message = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=256,
    messages=[{{"role": "user", "content": "Say hello in one sentence."}}],
)
print(message.content[0].text)
"""


@app.local_entrypoint()
def main():
    # Image for the sidecar: ships the proxy code + its deps. Must be a
    # pre-built image because `_experimental_containers.create` requires
    # `image._object_id` to be set.
    sidecar_image = (
        modal.Image.debian_slim()
        .pip_install("pyjwt", "httpx", "starlette", "uvicorn")
        .add_local_python_source("modal_cred_proxy", copy=True)
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
                {
                    "EGRESS_PROXY_URL": f"http://{SIDECAR_NAME}:{SIDECAR_PORT}",
                }
            )
        ],
        app=app,
    )

    # Start the proxy as a sidecar. The Anthropic key is mounted here only —
    # the sandbox's environment never receives it.
    sandbox._experimental_containers.create(
        "python",
        "-m",
        "modal_cred_proxy._sidecar_proxy",
        "--upstream",
        "api.anthropic.com",
        "--header",
        "x-api-key=ANTHROPIC_API_KEY",
        "--port",
        str(SIDECAR_PORT),
        name=SIDECAR_NAME,
        image=sidecar_image,
        secrets=[modal.Secret.from_name("anthropic-secret")],
        workdir="/root",
    )

    sandbox.wait()
    print(sandbox.stdout.read())
