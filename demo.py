import os

import modal

from modal_cred_proxy import create_jwt, credential_injector

app = modal.App("egress-proxy-demo")

EGRESS_JWT_SECRET = "demo-egress-proxy-secret-change-for-production-wow-look-its-different-now"

_image = (
    modal.Image.debian_slim()
    .pip_install("pyjwt", "httpx", "starlette")
    .add_local_python_source("modal_cred_proxy")
)

SANDBOX_CODE = """
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
"""


@app.function(
    image=_image,
    secrets=[
        modal.Secret.from_name("modal_egress_proxy_secret"),
        modal.Secret.from_name("anthropic-secret"),
    ],
)
@modal.asgi_app()
def proxy_function():
    anthropic_key = os.environ["ANTHROPIC_API_KEY"]
    egress_secret = os.environ["MODAL_EGRESS_PROXY_SECRET"]
    return credential_injector(
        egress_secret,
        "api.anthropic.com",
        {"x-api-key": anthropic_key},
    )


@app.local_entrypoint()
def main():
    modal.Secret.objects.create(
        "modal_egress_proxy_secret",
        {"MODAL_EGRESS_PROXY_SECRET": EGRESS_JWT_SECRET},
        allow_existing=True,
    )
    egress_modal_secret = modal.Secret.from_name("modal_egress_proxy_secret")
    egress_modal_secret.hydrate()
    egress_modal_secret.update({"MODAL_EGRESS_PROXY_SECRET": EGRESS_JWT_SECRET})

    egress_jwt = create_jwt(EGRESS_JWT_SECRET, validity_seconds=3600)

    secret = modal.Secret.from_dict({
        "EGRESS_JWT": egress_jwt,
        "EGRESS_PROXY_URL": proxy_function.get_web_url(),
    })

    sb = modal.Sandbox.create(
        "python3", "-c", SANDBOX_CODE,
        image=modal.Image.debian_slim().pip_install("anthropic"),
        secrets=[secret],
        app=app,
    )
    sb.wait()
    print(sb.stdout.read())
