# Inject Secrets into a Sandbox with a proxy Sidecar

Code running in a [Sandbox](https://modal.com/docs/guide/sandbox) often needs to call an
external API that requires an API key. Rather than passing that key into the Sandbox,
you can keep it in a separate, trusted container and let that container add it to
outbound requests:

```
Sandbox code  ->  proxy Sidecar  ->  api.anthropic.com
(no API key)      (adds the key)
```

The proxy runs as a [Sidecar](https://modal.com/docs/guide/sandbox-sidecars): a sibling
container that shares a private network with the Sandbox. That network is reachable only
from inside the Sandbox, so the proxy is available to your Sandbox code and to nothing
else, while the key itself stays in a Modal
[Secret](https://modal.com/docs/guide/secrets) mounted on the Sidecar alone.

We use [Caddy](https://caddyserver.com/) as the proxy and the Anthropic API as the
upstream, but any proxy that can set request headers (nginx, Envoy, or something you
write yourself) and any authenticated API will do.

Sandbox Sidecars are in alpha and access is restricted to allowlisted workspaces.

The full example lives in
[`sidecar_secrets_injection.py`](./sidecar_secrets_injection.py) and
[`Caddyfile`](./Caddyfile); all of the code below comes from those two files.

To run this example, create the Secret it reads the key from:

```
modal secret create anthropic-secret ANTHROPIC_API_KEY=sk-ant-...
```

then run the script:

```
pip install modal
python sidecar_secrets_injection.py
```

```python
from pathlib import Path

import modal

app = modal.App.lookup("example-sidecar-secrets-injection", create_if_missing=True)

MINUTES = 60  # seconds
```

## Configure the proxy

Each Sidecar is reachable from the Sandbox at the name we give it, resolved through
`/etc/hosts` on the shared bridge network.

```python
SIDECAR_NAME = "egress-proxy"
SIDECAR_PORT = 8080
PROXY_URL = f"http://{SIDECAR_NAME}:{SIDECAR_PORT}"
```

The proxy's behavior lives in a [`Caddyfile`](./Caddyfile) sitting next to this script.
It forwards every request to the Anthropic API, filling in the real key from the
Sidecar's own environment. It also drops any `Authorization` header that came from the
Sandbox, so the proxy decides which key reaches the upstream.

```
{
	admin off
}

:8080 {
	reverse_proxy https://api.anthropic.com {
		header_up Host api.anthropic.com
		header_up x-api-key {env.ANTHROPIC_API_KEY}
		header_up -Authorization
	}
}
```

## Build the Images

We copy that config to the path the `caddy` Image already reads on startup, so the
Sidecar needs no command of its own.

Sidecars can't build their Image lazily on startup, so we
[build it up front](https://modal.com/docs/guide/sandboxes#separating-image-builds-from-sandbox-creation)
with `Image.build` and pass the resolved Image along.

```python
with modal.enable_output():
    sidecar_image = (
        modal.Image.from_registry("caddy:2.11")
        .add_local_file(
            Path(__file__).parent / "Caddyfile", "/etc/caddy/Caddyfile", copy=True
        )
        .build(app)
    )

sandbox_image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "anthropic==0.121.0"
)
```

## Start the Sandbox

The Sandbox is told where the proxy is but is given no Secrets. An empty
`outbound_cidr_allowlist` cuts off its access to the public internet while leaving the
private network to its Sidecars intact, so the proxy becomes the Sandbox's only way out.

We pass no command, so the Sandbox stays alive waiting for us to send it work.

```python
with modal.enable_output():
    sandbox = modal.Sandbox.create(
        app=app,
        image=sandbox_image,
        env={"ANTHROPIC_BASE_URL": PROXY_URL},
        outbound_cidr_allowlist=[],
        timeout=5 * MINUTES,
    )
print(f"Sandbox ID: {sandbox.object_id}")
```

## Start the proxy Sidecar

Now we start Caddy alongside it, with the Anthropic Secret mounted here and only here.

```python
sidecar = sandbox._experimental_sidecars.create(
    name=SIDECAR_NAME,
    image=sidecar_image,
    secrets=[
        modal.Secret.from_name("anthropic-secret", required_keys=["ANTHROPIC_API_KEY"])
    ],
)
print(f"Sidecar ID: {sidecar.object_id}")
```

Creating a Sidecar returns as soon as its container starts, which is before Caddy is
listening, so we wait for the port to start accepting connections.

```python
sandbox.exec(
    "bash",
    "-c",
    f"until (echo > /dev/tcp/{SIDECAR_NAME}/{SIDECAR_PORT}) 2>/dev/null; do sleep 0.1; done",
    timeout=1 * MINUTES,
).wait()
```

## Call the API from the Sandbox

The code below runs inside the Sandbox. It first shows what it has to work with — no key,
and no direct route to Anthropic — then calls the API with an invalid key, which the
proxy swaps out for the real one on the way through.

Note that the call itself is unmodified from what it would be without a proxy: the
Anthropic SDK, like most SDKs and agent harnesses, picks up its base URL from the
environment.

```python
UNTRUSTED_CODE = """
import os
import socket

import anthropic

print(f"ANTHROPIC_API_KEY in Sandbox env: {'ANTHROPIC_API_KEY' in os.environ}")

try:
    socket.create_connection(("api.anthropic.com", 443), timeout=10).close()
    print("Direct connection to api.anthropic.com: succeeded")
except OSError as exc:
    print(f"Direct connection to api.anthropic.com: blocked ({type(exc).__name__})")

print(f"Calling Anthropic through {os.environ['ANTHROPIC_BASE_URL']} with an invalid key")
client = anthropic.Anthropic(api_key="not-a-real-key")
message = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=256,
    messages=[{"role": "user", "content": "Say hello in one sentence."}],
)
print(f"Response: {message.content[0].text}")
"""

process = sandbox.exec("python", "-c", UNTRUSTED_CODE)
for line in process.stdout:
    print(line, end="")
if process.wait() != 0:
    raise RuntimeError(process.stderr.read())
```

The output should look something like

```
ANTHROPIC_API_KEY in Sandbox env: False
Direct connection to api.anthropic.com: blocked (OSError)
Calling Anthropic through http://egress-proxy:8080 with an invalid key
Response: Hello! It's nice to meet you.
```

Terminating the Sandbox tears down its Sidecars along with it.

```python
sandbox.terminate()
```

It's worth knowing where this boundary ends: the Sandbox never sees the key, but
it can still use it, and can make as many Anthropic calls as it likes. If that matters
for your workload, the proxy is also the natural place to add rate limits or to restrict
which paths it forwards.
