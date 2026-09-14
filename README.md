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

## Code layout

```
apstra_client/   ApstraClient REST client (mixins by domain: blueprints,
                 networks, systems, topology, endpoints, cabling, locate,
                 revisions, telemetry, vlan, ports, catalog)
core.py          FastMCP instance, Bearer auth, write-guard, _client()
tools/           61 @mcp.tool() functions, one module per domain
dispatch/        Optional flat dispatcher toolset (see below)
prompts.py       6 guided @mcp.prompt() templates
server.py        Entry point (imports core + tools + prompts, run())
```

---

## Tests

Unit tests (no real Apstra controller, no network) live under `tests/`:

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
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
| `APSTRA_WRITE_ENABLED` | Allow mutating tools (create/update/delete, commit, rollback, revert) | `false` |
| `APSTRA_FLAT_TOOLSET`  | Collapse the 61 atomic tools into 12 scope/action dispatchers | `false` |

---

## Security: read-only mode by default

By default (`APSTRA_WRITE_ENABLED=false` or unset) this server is **read-only**:
any tool that creates, updates, deletes, commits, rolls back or reverts something
(e.g. `create_blueprint`, `commit_blueprint`, `create_virtual_network`,
`update_virtual_network`, `delete_virtual_network`, `create_security_zone`,
`create_generic_system`, `apply_ct_to_interfaces`, `enable_vn_dci`,
`enable_sz_dci`, `add_vlan_to_port`, `rollback_blueprint`, `revert_staging`)
immediately fails with a `PermissionError` before touching Apstra. Set
`APSTRA_WRITE_ENABLED=true` (env or `.env`) and restart the container to allow
these tools. All other (read-only) tools are unaffected.

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

## Flat dispatcher toolset (tool count optimization)

61 atomic tools is a lot for an LLM's tool-selection context. Setting
`APSTRA_FLAT_TOOLSET=true` (env or `.env`) collapses them into **12 scope/action
dispatchers** (`dispatch/flat_tools.py`): each dispatcher takes a `scope` (and,
for writes, an `action`) picking which underlying legacy function to call —
zero change to `apstra_client/` business logic, and mutating scopes still go
through `@_require_write` exactly like before. Disabled by default
(`false`): the 61 legacy tools stay advertised unchanged. Toggling the flag
only needs `docker compose up -d` (no rebuild) plus reconnecting the MCP
client (it caches the tool list from the previous session).

| Dispatcher | Replaces | Example scopes |
|---|---|---|
| `list_catalog`        | catalog.py (17 tools)        | `asn_pools`, `templates`, `configlet`, `task` |
| `get_blueprint`       | blueprint reads (6)          | `list`, `anomalies`, `build_errors`, `logical_diff`, `nodes`, `check_commit` |
| `get_topology`        | topology/systems/ports reads (7) | `switch_properties`, `switch_uplinks`, `generic_systems`, `ports` |
| `get_cabling`         | cabling.py (2)               | `fabric_matrix`, `cabling_matrix` |
| `get_network`         | network reads (5)            | `virtual_networks`, `virtual_network`, `security_zones` |
| `get_system`          | version_systems.py (4)       | `version`, `list`, `info`, `agents` |
| `get_telemetry`       | telemetry.py (2)             | `bgp_status`, `fabric_health` |
| `locate`              | endpoint discovery (3)       | `probe`, `endpoint`, `vm` |
| `configure_blueprint` | blueprint writes (2) ✍️       | `create`, `commit` |
| `manage_revisions`    | revisions.py (3)             | `list`, `rollback` ✍️, `revert` ✍️ |
| `configure_network`   | network writes (7) ✍️         | `virtual_network`+`create`/`update`/`delete`, `security_zone`+`create`, … |
| `configure_fabric`    | generic_system + vlan (3) ✍️  | `generic_system`+`create`, `vlan`+`prepare`/`apply` |

---

## Available tools (61 tools, or 12 dispatchers with `APSTRA_FLAT_TOOLSET=true`)

> ✍️ marks a write tool: blocked with `PermissionError` unless `APSTRA_WRITE_ENABLED=true`
> (see [Security: read-only mode by default](#security-read-only-mode-by-default)).
> The tool list below is the legacy/atomic toolset (default). See
> [Flat dispatcher toolset](#flat-dispatcher-toolset-tool-count-optimization)
> for the alternative 12-tool mode.

### Version & Systems
- `get_version`                    — Apstra version
- `list_systems`                   — Managed devices
- `get_system`                     — Details of a device
- `list_agents`                    — Apstra agents

### Blueprints
- `list_blueprints`                — All blueprints
- `create_blueprint`               — ✍️ Create a blueprint from a template
- `get_blueprint_anomalies`        — Runtime anomalies (telemetry)
- `get_blueprint_build_errors`     — Staging build errors (Uncommitted tab)
- `get_blueprint_logical_diff`     — Uncommitted logical diff (staging)
- `get_blueprint_nodes`            — Graph nodes
- `check_blueprint_commit`         — Validate staging without deploying
- `commit_blueprint`               — ✍️ Deploy staged changes (confirmation required)

### Virtual Networks
- `list_virtual_networks`          — VLANs/VXLANs of a blueprint
- `get_virtual_network`            — Details of a virtual network
- `create_virtual_network`         — ✍️ Create a virtual network
- `update_virtual_network`         — ✍️ Update a virtual network
- `delete_virtual_network`         — ✍️ Delete a virtual network
- `list_redundancy_groups`         — ESI redundancy groups
- `list_connectivity_templates`    — Connectivity templates (CT)
- `apply_ct_to_interfaces`         — ✍️ Apply a CT to interfaces
- `enable_vn_dci`                  — ✍️ Enable DCI (RT2 and/or RT5) on a VN

### Security / Routing Zones
- `list_security_zones`            — VRFs of a blueprint
- `create_security_zone`           — ✍️ Create a routing zone
- `enable_sz_dci`                  — ✍️ Enable DCI (RT5 and/or iRT) on an SZ

### Generic Systems
- `create_generic_system`          — ✍️ Create a server/appliance (auto transformation_id)
- `list_generic_systems_on_switch` — Servers connected to a switch
- `get_generic_system_on_port`     — Server on a specific port (or "free port")

### Topology / Switch properties
- `get_switch_properties`          — ASN, role, hostname of a switch
- `get_switch_uplinks`             — Connections of a switch to the Spines
- `get_link_ips`                   — Point-to-point IPs between two switches
- `get_switch_loopbacks`           — Loopbacks configured on a switch

### Endpoints, VMs & Cabling (telemetry)
- `find_endpoint`                  — Locate a learned endpoint (VM/host) by IP/MAC
- `locate`                         — Locate a MAC/IP (physical vs remote VXLAN)
- `get_vm_info`                    — VM info (vCenter/NSX integration)
- `get_fabric_matrix`              — Hierarchical cabling matrix endpoint→leaf→spine
- `cabling_matrix`                 — Cabling matrix via /cabling-map (A→B links)
- `list_ports`                     — Ports of a device (status, LACP, CT)

### Health & BGP
- `get_bgp_status`                 — Real-time state of all BGP peerings
- `get_fabric_health`              — Fabric health (links, interfaces, alerts)

### Revisions & Staging
- `list_blueprint_revisions`       — Restore points of a blueprint
- `rollback_blueprint`             — ✍️ Roll back to a previous revision
- `revert_staging`                 — ✍️ Discard uncommitted staging changes

### VLAN provisioning workflow
- `prepare_vlan`                   — Pre-flight questionnaire before adding a VLAN
- `add_vlan_to_port`               — ✍️ Create a VLAN on a leaf and assign it to a port

### Resources
- `list_asn_pools`                 — ASN pools
- `list_ip_pools`                  — IP pools
- `list_vni_pools`                 — VNI pools

### Design
- `list_logical_devices`           — Logical profiles
- `list_interface_maps`            — Interface maps
- `list_rack_types`                — Rack types
- `list_templates`                 — Datacenter templates

### Configlets
- `list_configlets`                — Global configlets
- `get_configlet`                  — Details of a configlet
- `list_blueprint_configlets`      — Configlets imported into a blueprint
- `get_blueprint_configlet`        — Details of a blueprint configlet

### Property Sets
- `list_property_sets`             — Global property sets
- `get_property_set`               — Details of a property set
- `list_blueprint_property_sets`   — Property sets of a blueprint
- `get_blueprint_property_set`     — Details of a blueprint property set

### Tasks
- `list_tasks`                     — List tasks
- `get_task`                       — Task detail

---

## Prompts (guided operations)

Reusable MCP prompt templates exposed alongside the tools:

- `blueprint_health`               — Guided health review of a blueprint
- `create_virtual_network_guide`   — Step-by-step VN creation
- `verify_fabric`                  — Fabric verification checklist
- `deploy_generic_system`          — Guided generic-system deployment
- `configure_dci`                  — Guided DCI configuration
- `audit_resources`                — Resource pools audit

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
