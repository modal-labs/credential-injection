This repo demonstrates credential injection for Modal Sandboxes using a reverse-proxy sidecar. The proxy runs alongside the sandbox in a private container network, injecting real API credentials into outbound requests without ever exposing them to sandbox code.

## How it works

```
Sandbox code  →  proxy sidecar  →  api.anthropic.com
  (no API key)    (injects real key)
```

A reverse proxy runs as a sidecar container next to the sandbox. Because sidecars share a private network with their sandbox, the proxy is only reachable from within — network isolation is the auth boundary. The real credential lives in a Modal Secret mounted on the sidecar only; the sandbox never sees it.

Any reverse proxy that supports injecting request headers works here: [Caddy](https://caddyserver.com), [nginx](https://nginx.org), [Envoy](https://www.envoyproxy.io), a custom HTTP server, etc. This example uses Caddy for its simple configuration syntax.

## Setup

### 1. Configure the proxy

Configure your proxy to forward requests to the upstream API and inject the credential as a header. With Caddy:

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

An equivalent nginx config would use `proxy_set_header` in a `location` block with the credential read from an environment variable.

### 2. Create the Modal secret

Create a Modal secret named `anthropic-secret` containing your Anthropic API key:

```bash
modal secret create anthropic-secret ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
```

Or via the [Modal dashboard](https://modal.com/secrets): create a new secret, name it `anthropic-secret`, and add `ANTHROPIC_API_KEY` as a key.

### 3. Build the sidecar image

The sidecar image must be pre-built because `_experimental_sidecars.create` requires `image._object_id` to be set before the sandbox starts.

```python
sidecar_image = (
    modal.Image.from_registry("caddy:2")
    .add_local_file("Caddyfile", "/etc/caddy/Caddyfile", copy=True)
    .build(app)
)
```

### 4. Create the sandbox and attach the sidecar

```python
sandbox = modal.Sandbox.create(
    "python3", "-c", SANDBOX_CODE,
    image=modal.Image.debian_slim().pip_install("anthropic"),
    secrets=[modal.Secret.from_dict({"EGRESS_PROXY_URL": "http://egress-proxy:8080"})],
    app=app,
)

# The Anthropic key is mounted on the sidecar only — the sandbox never receives it.
sandbox._experimental_sidecars.create(
    name="egress-proxy",
    image=sidecar_image,
    secrets=[modal.Secret.from_name("anthropic-secret")],
)
```

### 5. Use the proxy from sandbox code

```python
import anthropic, os, time

time.sleep(5)  # give the sidecar a moment to start

client = anthropic.Anthropic(
    api_key="unused",
    base_url=os.environ["EGRESS_PROXY_URL"],
)
message = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=256,
    messages=[{"role": "user", "content": "Say hello in one sentence."}],
)
print(message.content[0].text)
```

## Demo

Install the Modal CLI and run:

```bash
pip install modal
modal run demo.py
```

This will:
1. Build a Caddy sidecar image (using the official `caddy:2` Docker image)
2. Start a sandbox configured to route API calls through the sidecar
3. Have the sandbox call Claude and print the response
