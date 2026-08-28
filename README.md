# Apstra MCP Server

MCP (Model Context Protocol) server for Juniper Apstra.
Lets Claude and other LLMs query and drive Apstra using natural language.

---

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your settings
```

---

## Configuration

| Variable           | Description                                    | Default |
|--------------------|------------------------------------------------|---------|
| `APSTRA_HOST`      | IP or FQDN of the Apstra controller            | —       |
| `APSTRA_USERNAME`  | Apstra user                                    | —       |
| `APSTRA_PASSWORD`  | Password                                       | —       |
| `APSTRA_VERIFY_SSL`| Verify the SSL certificate (true/false)        | `false` |
| `MCP_TRANSPORT`    | `sse` (network) or `stdio` (local)             | `sse`   |
| `MCP_HOST`         | Listen address (SSE mode)                      | `0.0.0.0` |
| `MCP_PORT`         | Listen port (SSE mode)                         | `8000`  |
| `APSTRA_AUTH_ENABLED` | Require a Bearer token on every MCP request | `false` |
| `APSTRA_TOKENS_FILE`  | Named tokens file                            | `/app/secrets/.tokens` |
| `APSTRA_TRUST_FORWARDED_FOR` | Trust `X-Forwarded-For` (proxy)       | `false` |

---

## Security: Bearer authentication of the MCP client

This server can require a **named Bearer token** on every request sent to the
`/mcp` endpoint. The feature is **disabled by default** (backward compatible)
and applies only to the HTTP transport (`streamable-http`).

- Enabled: every request to `/mcp` must carry `Authorization: Bearer <token>`.
  A missing or invalid token receives **HTTP 401**. The token **name** identifies
  the client.
- **LOCKED** mode: if auth is enabled but no token exists yet, the server starts
  but rejects every request with **HTTP 503** (fail-closed) until the first token
  is created, followed by a restart.

### Token management

Tokens are stored in `secrets/.tokens` (perms `0600`, git-ignored).
Manage them **inside the container** with the provided CLI:

```bash
# Create a named token (shows the secret only once — keep it safe)
docker compose exec hpe-apstra-mcp python apstra_token_manager.py generate --name vscode-dev

# List tokens (secret masked)
docker compose exec hpe-apstra-mcp python apstra_token_manager.py list

# Reveal a token
docker compose exec hpe-apstra-mcp python apstra_token_manager.py show --name vscode-dev

# Revoke a token
docker compose exec hpe-apstra-mcp python apstra_token_manager.py revoke --name vscode-dev
```

Generated tokens are prefixed with `apstra_`. Use a distinct token per
client/agent. After creating/revoking a token, **restart the container**
(`docker compose restart hpe-apstra-mcp`): the token store is loaded at
startup.

### Enabling

```yaml
# docker-compose.yml (or .env)
APSTRA_AUTH_ENABLED: "true"
```

```bash
docker compose up -d --build
# 1) generate the first token (the server is LOCKED until one exists)
docker compose exec hpe-apstra-mcp python apstra_token_manager.py generate --name vscode-dev
# 2) restart to load the token
docker compose restart hpe-apstra-mcp
```

### Connecting the MCP client (with auth)

```json
{
  "servers": {
    "hpe-apstra-mcp": {
      "type": "http",
      "url": "http://localhost:8005/mcp",
      "headers": { "Authorization": "Bearer apstra_xxxxxxxxxxxxxxxxxxxx" }
    }
  }
}
```

---

## Deployment modes

### SSE mode — remote server / Docker (recommended)

The server exposes an HTTP endpoint. Claude Desktop connects to it over the network.

```bash
# Without Docker
MCP_TRANSPORT=sse python server.py

# With Docker Compose
docker compose up -d
docker compose logs -f
```

Claude Desktop config (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "apstra": {
      "url": "http://SERVER_IP:8000/sse"
    }
  }
}
```

### stdio mode — same machine as Claude Desktop

```bash
MCP_TRANSPORT=stdio python server.py
```

Claude Desktop config:
```json
{
  "mcpServers": {
    "apstra": {
      "command": "python",
      "args": ["/path/to/hpe-apstra-mcp/server.py"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "APSTRA_HOST": "192.168.1.100",
        "APSTRA_USERNAME": "admin",
        "APSTRA_PASSWORD": "secret"
      }
    }
  }
}
```

### stdio mode via Docker (same machine, Python isolation)

```json
{
  "mcpServers": {
    "apstra": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "MCP_TRANSPORT=stdio",
        "--env-file", "/absolute/path/to/.env",
        "--network", "host",
        "hpe-apstra-mcp"
      ]
    }
  }
}
```

---

## Network architecture (SSE mode)

```
  Claude Desktop host           Docker server / VM
  ┌──────────────────┐          ┌──────────────────────┐
  │  Claude Desktop  │─────────▶│  hpe-apstra-mcp :8000│
  │                  │ HTTP/SSE │  (Python + MCP)      │
  └──────────────────┘          └──────────┬───────────┘
                                            │ HTTPS REST
                                 ┌──────────▼───────────┐
                                 │  Juniper Apstra      │
                                 └──────────────────────┘
```

> **Security**: in production, place a reverse proxy (Nginx, Caddy) in front of
> port 8000 with Basic authentication or mTLS, or use a VPN.

---

## Available tools (30 tools)

### Blueprints
- `list_blueprints`                — All blueprints
- `get_blueprint_anomalies`        — Anomalies of a blueprint
- `get_blueprint_nodes`            — Graph nodes
- `check_blueprint_commit`         — Validate staging without deploying
- `commit_blueprint`               — Deploy staged changes

### Virtual Networks
- `list_virtual_networks`          — VLANs/VXLANs of a blueprint
- `create_virtual_network`         — Create a virtual network
- `delete_virtual_network`         — Delete a virtual network
- `enable_vn_dci`                  — Enable DCI (RT2 and/or RT5) on a VN

### Security / Routing Zones
- `list_security_zones`            — VRFs of a blueprint
- `create_security_zone`           — Create a routing zone
- `enable_sz_dci`                  — Enable DCI (RT5 and/or iRT) on an SZ

### Systems & Agents
- `list_systems`                   — Managed devices
- `get_system`                     — Details of a device
- `list_agents`                    — Apstra agents

### Generic Systems
- `create_generic_system`          — Create a server/appliance (auto transformation_id)
- `list_generic_systems_on_switch` — Servers connected to a switch
- `get_generic_system_on_port`     — Server on a specific port (or "free port")

### Topology / Properties
- `get_switch_properties`          — ASN, role, hostname of a switch
- `get_switch_uplinks`             — Connections of a switch to the Spines
- `get_link_ips`                   — Point-to-point IPs between two switches
- `get_switch_loopbacks`           — Loopbacks configured on a switch

### Resources
- `list_asn_pools`                 — ASN pools
- `list_ip_pools`                  — IP pools
- `list_vni_pools`                 — VNI pools

### Design
- `list_logical_devices`           — Logical profiles
- `list_interface_maps`            — Interface maps
- `list_rack_types`                — Rack types
- `list_templates`                 — Datacenter templates

### Tasks & Info
- `list_tasks` / `get_task`        — Task tracking
- `get_version`                    — Apstra version

---

## Example questions for Claude

```
"What is the ASN configured on leaf-01?"
"How is leaf-02 connected to the spines?"
"What IPs are used between leaf-01 and spine-02?"
"What loopbacks are configured on spine-01?"
"Which servers are connected to leaf-03?"
"What is plugged into xe-0/0/12 on leaf-01?"
"Enable RT2 and RT5 for the VN prod-web in the DCI"
"Enable DCI on the routing zone vrf-prod"
"Create a server web-01 at 25G on leaf-01 xe-0/0/0 and leaf-02 xe-0/0/0 with LACP"
"Run a commit check on the blueprint datacenter-paris"
"Deploy the blueprint changes with the message 'add VLAN 100'"
```
