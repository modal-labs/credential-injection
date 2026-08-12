This repo demonstrates Secrets injection for Modal Sandboxes using a reverse-proxy Sidecar. The proxy runs alongside the Sandbox in a private container network, adding the real API key to outbound requests without ever exposing it to Sandbox code.

> **Note:** Sandbox Sidecars are an alpha API restricted to allowlisted workspaces. This example also lives in [modal-examples](https://github.com/modal-labs/examples) under `13_sandboxes/secrets-injection/`.

## How it works

```
Sandbox code  ->  proxy Sidecar  ->  api.anthropic.com
(no API key)      (adds the key)
```

The proxy runs as a [Sidecar](https://modal.com/docs/guide/sandbox-sidecars): a sibling container that shares a private network with the Sandbox. That network is reachable only from inside the Sandbox, so the proxy is available to your Sandbox code and to nothing else, while the key itself stays in a Modal [Secret](https://modal.com/docs/guide/secrets) mounted on the Sidecar alone.

The Sandbox is created with an empty `outbound_cidr_allowlist`, which cuts off its access to the public internet while leaving the private network to its Sidecars intact — the proxy becomes the Sandbox's only way out.

This example uses [Caddy](https://caddyserver.com/) as the proxy and the Anthropic API as the upstream, but any proxy that can set request headers (nginx, Envoy, or something you write yourself) and any authenticated API will do.

## Setup

### 1. Configure the proxy

The proxy's behavior lives in the `Caddyfile`. It forwards every request to the Anthropic API, filling in the real key from the Sidecar's own environment. It also drops any `Authorization` header that came from the Sandbox, so the proxy decides which key reaches the upstream.

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

### 2. Create the Modal Secret

Create a Modal Secret named `anthropic-secret` containing your Anthropic API key:

```bash
modal secret create anthropic-secret ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Run the example

```bash
pip install modal
python sidecar_secrets_injection.py
```

This will:

1. Build a Caddy Sidecar Image up front with `Image.build` (Sidecars can't build their Image lazily on startup)
2. Start a Sandbox with no Secrets and no public internet access, pointed at the proxy via `ANTHROPIC_BASE_URL`
3. Start the Caddy Sidecar with the Anthropic Secret mounted on it — and only it
4. Run code inside the Sandbox that shows it has no key and no direct route to Anthropic, then calls the API with an invalid key, which the proxy swaps out for the real one on the way through

The output should look something like:

```
ANTHROPIC_API_KEY in Sandbox env: False
Direct connection to api.anthropic.com: blocked (OSError)
Calling Anthropic through http://egress-proxy:8080 with an invalid key
Response: Hello! It's nice to meet you.
```

## Where the boundary ends

The Sandbox never sees the key, but it can still use it, and can make as many Anthropic calls as it likes. If that matters for your workload, the proxy is also the natural place to add rate limits or to restrict which paths it forwards.
