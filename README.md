# Cockpit MCP Gateway

[![tests](https://github.com/bmericc/cockpit-mcp-gateway/actions/workflows/tests.yml/badge.svg)](https://github.com/bmericc/cockpit-mcp-gateway/actions/workflows/tests.yml)

**English** · [Türkçe](README.tr.md)

A gateway that exposes Linux servers to [Model Context Protocol (MCP)](https://modelcontextprotocol.io) clients (Claude etc.) through the [Cockpit](https://cockpit-project.org) running on them. It works with any distribution that runs Cockpit, such as Debian, Ubuntu, Fedora and RHEL. An AI assistant can read the state of the servers, manage services/packages/firewall and run commands.

![How Cockpit MCP Gateway works](docs/images/how-it-works.png)

For a detailed description of the architecture and security: [Technical Overview](docs/overview.md)

- Commands run through **Cockpit** on the server. If the Cockpit port is not reachable from outside, Cockpit is reached through an **SSH tunnel**; if that fails too, commands run directly over **SSH**.
- Logging in to the gateway (web panel and MCP clients) is done with a **Cockpit account**. There is no separate user database.
- The web panel, error messages and MCP tool descriptions are in **English** by default and in **Turkish** when the browser asks for it (`Accept-Language`).

## Installation

```bash
cp sample.env .env      # edit the values
docker compose pull
docker compose up -d --no-build
```

Images for `linux/amd64` and `linux/arm64` are published to `ghcr.io/bmericc/cockpit-mcp-gateway` and Docker Hub (`bmericc/cockpit-mcp-gateway`).

The gateway listens on port `7435`. Put a reverse proxy that terminates HTTPS (nginx, Caddy etc.) in front of it; `X-Forwarded-*` headers are honoured only from the addresses in `TRUSTED_PROXIES`. `docker-compose.yml` publishes the port on `127.0.0.1` only; set `BIND_ADDRESS` to change that.

Cockpit must be installed and running on the target servers:

```bash
sudo apt install -y cockpit            # Fedora/RHEL: sudo dnf install -y cockpit
sudo systemctl enable --now cockpit.socket
```

Administrative operations need `sudo`, which a minimal Debian installation does not have. Install it and give the Cockpit user the right to use it: `apt install sudo && usermod -aG sudo USER`.

If the gateway can reach the server over SSH, the Cockpit port (9090) does not need to be opened to the outside; Cockpit is reached through an SSH tunnel (see *Connection order*). If direct access is wanted, the port should only be opened to the gateway's IP address:

```bash
sudo ufw allow from GATEWAY_IP to any port 9090 proto tcp
# firewalld: sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="GATEWAY_IP" port port="9090" protocol="tcp" accept' && sudo firewall-cmd --reload
```

### Environment variables (`.env`)

| Variable | Description |
| --- | --- |
| `SECRET_KEY` | Signs session cookies and encrypts Cockpit passwords. If empty or a sample value, a random one is generated once and kept in `data/.secret_key`. **If it changes, stored Cockpit passwords can no longer be decrypted.** |
| `PORT` | Port to listen on (default `7435`) |
| `PUBLIC_URL` | The address the gateway is reached at from outside, e.g. `https://mcp.example.com`. OAuth redirects use it. |
| `COCKPIT_AUTH_URL` | The Cockpit that verifies logins. Default `https://host.docker.internal:9090` (the host machine the gateway runs on). |
| `COCKPIT_AUTH_VERIFY_TLS` | Whether to verify this Cockpit's certificate (default `false`) |
| `ALLOWED_USERS` | Cockpit users allowed into the gateway (comma separated). **Required: the gateway does not start if it is empty.** |
| `MCP_API_KEY` | **Access token** required in addition to the Cockpit login (second factor). If empty or a sample value, a random one is generated once and kept in `data/.mcp_api_key`. Changing it ends all panel and OAuth sessions. |
| `OUTBOUND_PROXY` | Optional. Default proxy for connections to the servers (`socks5://`, `socks5h://`, `http://`). See *Connection proxy*. |
| `SSH_LOGINS` | Users and key folders to try for SSH connections (tunnel and fallback). Default: `root:/root/.ssh` |
| `TRUSTED_PROXIES` | Reverse proxy addresses allowed to set `X-Forwarded-*` (comma separated, CIDR allowed). Default `127.0.0.1`. With a proxy on the host and the compose network, use the bridge range, e.g. `172.16.0.0/12`. Never use `*`: clients could then forge the address in the audit log. |
| `MCP_READ_ONLY` | `true` disables every tool that changes something (default `false`). The `confirm` field is filled in by the model, so it is not human approval; this switch is. |
| `AUDIT_LOG_MAX_MB` | Maximum size of the audit log file (default `10`). See *Audit log*. |

`docker-compose.yml` gives access to the host machine under the name `host.docker.internal`. It also mounts `/root/.ssh` read-only. To use another user's keys, add the folder in a `docker-compose.override.yml` and to `SSH_LOGINS`. The file also drops Linux capabilities and makes the container filesystem read-only.

## Logging in and connecting an MCP client

MCP endpoint: `https://<PUBLIC_URL>/mcp` (Streamable HTTP). The HTTP+SSE transport, deprecated in the MCP specification, stays available at `https://<PUBLIC_URL>/sse` for older clients.

The login has two parts: the **Cockpit username/password** and the **access token** (`MCP_API_KEY`). The token alone does not grant access. This way a weak Cockpit password is not enough on its own.

| Method | Usage |
| --- | --- |
| **OAuth 2.1** (recommended) | When the client connects, a login page opens in the browser: username, Cockpit password and gateway password (`MCP_API_KEY`). After login the client receives a token (1 hour; renewals stop 7 days after the login, then the client has to log in with Cockpit again). |
| **HTTP Basic** | `Authorization: Basic base64(user:password)` + the access token (`X-MCP-Token: <token>` header or `?token=<token>`) |

If the gateway password is added to the MCP address (`https://<PUBLIC_URL>/mcp?token=<MCP_API_KEY>`), the login page does not ask for it again; only the username and Cockpit password are requested. If it is missing or wrong, a second password field appears on the page. The same applies to the web panel (`/login?token=<token>`).

**claude.ai:** Enter `https://<PUBLIC_URL>/mcp?token=<token>` under *Settings → Connectors → Add custom connector*. Log in with your Cockpit account on the page that opens while connecting.

**Claude Code:**

```bash
claude mcp add --transport sse cockpit-gateway "https://<PUBLIC_URL>/sse?token=<token>"
```

OAuth flow endpoints:
- `/.well-known/oauth-protected-resource/sse`
- `/.well-known/oauth-authorization-server`
- `/register` (dynamic client registration)
- `/authorize` (PKCE)
- `/token`
- `/revoke`

Tokens are stored hashed in `data/oauth.json`.

## Adding servers

Log in to the web panel (`https://<PUBLIC_URL>/`) with your Cockpit account. Then fill in the *Add / Update Server* form:

| Field | Description |
| --- | --- |
| Server name, Host | Required |
| Cockpit user / password | The Cockpit account the commands run as. For administrative operations this user needs sudo rights. |
| Cockpit address | `https://HOST:9090` if empty |
| Verify TLS certificate | Leave off for self-signed certificates |
| SSH user / port / key path | For the Cockpit tunnel and the SSH fallback |
| Connection proxy | Optional, see *Connection proxy* |

Servers are kept in `data/servers.json`, which is not tracked by git. Cockpit passwords are encrypted in the file; the `list_servers` tool does not show passwords.

**Connection check:** Pressing Save really connects to the server and tries the routes in *Connection order*. If Cockpit works directly or through the SSH tunnel you get a ✓; if only plain SSH works the server is saved but shown with a ⚠ warning (and the reason Cockpit is not working). If no connection can be made at all, nothing is saved and an error is shown. If the server is down at the moment, the *Save without testing the connection* option can be used. The *Test* button in the server list retries the connection on demand and shows the latest status in the table.

**The gateway's public IP address:** The top of the panel shows the IP address the gateway reaches the internet from. This is the address that must be allowed in the firewall of remote servers for Cockpit (9090/tcp) and SSH (22/tcp) (if SSH is open, 9090 is not required since Cockpit can be used through the tunnel). The address is cached for 10 minutes; the *Refresh* button queries it again. Servers on the same local network see the local IP address of the machine running the gateway instead.

### Connection proxy

Cockpit and SSH connections to the servers can go through a proxy. The servers then see the proxy's IP address instead of the gateway's; allowing a single fixed address in the firewall is enough.

- **Default proxy:** set with `OUTBOUND_PROXY` in `.env` and applied to all servers.
- **Per server:** the *Connection proxy* field in the panel. An address such as `socks5://host:1080`, `direct` (no proxy) or `default` (back to the default) can be entered. If left empty, the current setting is kept.
- **Supported types:** `http://` (HTTP CONNECT), `socks5://` (target name resolved on the gateway), `socks5h://` (target name resolved on the proxy). A username/password can be given in the address; it is stored encrypted in the server records and the password is hidden in the panel and in `list_servers` output.
- **Egress IP:** if a default proxy is set, the panel also shows the egress IP address seen through the proxy.

### Shared SSH keys

In the *Shared SSH Keys* section of the panel a private key can be pasted (together with its passphrase if it has one) or a new Ed25519 key can be generated. These keys are tried for SSH connections (Cockpit tunnel and SSH fallback) on all servers, for every user, after the user's own `.ssh` keys. It is enough to add the public key line from the table to `~/.ssh/authorized_keys` on the servers. Private keys are stored in `data/ssh_keys.json` encrypted with `SECRET_KEY` and are not shown in the panel.

### Connection order

1. **Cockpit (direct):** if a Cockpit user is defined for the server, the Cockpit address is tried first. Administrative operations use Cockpit's superuser (sudo) mechanism.
2. **Cockpit (SSH tunnel):** if Cockpit cannot be reached (for example 9090 is closed in the firewall), the gateway connects to the server over SSH and opens a tunnel inside that connection to the server's own Cockpit (`localhost:9090`). Commands still run through Cockpit; the Cockpit port does not need to be opened to the outside. SSH port forwarding (`AllowTcpForwarding`, on by default) must not be disabled on the server.
3. **SSH:** if the tunnel cannot be established either, commands run directly over SSH. For non-root users, administrative commands run with `sudo -n` (passwordless sudo required).

Over SSH the user defined for the server is tried first, then the `SSH_LOGINS` order; for each user the shared keys are tried after their own keys.

- A failure to reach Cockpit directly is remembered for 10 minutes; during that time the tunnel is used without waiting for a timeout on every call.
- If Cockpit rejects the password, the tunnel is not tried and plain SSH is used directly.

Every tool output says which route was used: `"connection": {"via": "cockpit" | "cockpit-ssh" | "ssh"}`. If it fell back to SSH, the `cockpit_error` field says why Cockpit could not be used.

## MCP tools

**Read tools** (no confirmation needed):

| Tool | Description |
| --- | --- |
| `list_servers` | Registered servers |
| `fleet_health` | Load, failed services and root disk summary of all servers (in parallel) |
| `server_info` | Hostname, OS version, kernel, uptime |
| `resource_usage` | Memory, swap, load, CPU, disk usage |
| `service_status` | Service state and latest logs |
| `failed_services` | Failed systemd units |
| `read_logs` | `journalctl` (unit, time, priority, search filter) |
| `top_processes` | Processes using the most CPU/memory |
| `network_info` | IPs, routes, listening ports |
| `firewall_status` | Firewall (`firewalld` or `ufw`) state and rules |
| `selinux_status` | SELinux mode and recent denials |
| `apparmor_status` | AppArmor profile status and recent denials (Debian/Ubuntu) |
| `available_updates` | Pending (security) updates |
| `package_info` | Whether a package is installed, and its version |

**Changing tools** (without `confirm: true` they only show what would be done):

| Tool | Description |
| --- | --- |
| `service_action` | start / stop / restart / reload / enable / disable |
| `install_updates` | `dnf upgrade` / `apt-get upgrade` (optionally security only) |
| `package_install` / `package_remove` | Install / remove packages (`dnf` / `apt-get`) |
| `firewall_rule` | Add/remove a permanent port/service rule (`firewalld` or `ufw`) |
| `reboot_server` | Reboot |
| `run_remote_command` | Arbitrary command (administrator privileges with `as_root`) |

The package tools (`available_updates`, `install_updates`, `package_info`, `package_install`, `package_remove`) use `apt` on Debian/Ubuntu and `dnf` on Fedora/RHEL; on other distributions they return an error without running anything. The distribution is read from `/etc/os-release` when a server is added and on every connection to it, and `data/servers.json` is updated when it changes. The distribution and its version are shown in the panel and in `list_servers`. These read commands appear in the audit log of each call. When upgrading from an older version nothing needs to be done: at startup the gateway fills in this information for existing servers in the background, and any server it cannot reach is completed later. The firewall tools use whichever of `firewalld` and `ufw` is installed; if both are, `firewalld` is used when it is active. `firewall_rule` never enables `ufw`, because that could cut off access to the server; if it is inactive the tool says so. `selinux_status` and `apparmor_status` answer without running anything on servers that do not have that module. The installed tools are checked together with the distribution, so a newly installed one is picked up on the next connection.

Inputs (service, package, port etc.) are validated. Commands are passed as argv, so they are not open to command injection. Outputs are returned as JSON, long outputs are truncated, and commands time out.

## Audit log

Every operation done through the gateway is recorded and shown on the *Audit log* page (`/logs`) of the panel:

- **MCP tool calls:** who (user, IP), which tool, which server, the parameters, the commands run on the server (connection route, user, exit code), the result and the duration. Calls that were not executed because `confirm: true` was not given appear as *awaiting confirmation*.
- **MCP connections:** successful connections and rejected credentials.
- **Panel actions:** login (successful/failed), logout, adding/updating/testing/deleting servers, adding/generating/deleting shared SSH keys.

The page has a text search and user, server, source (MCP / panel) and status filters; the newest entry is on top.

Entries are kept in `data/audit.log`, one JSON object per line (file mode `600`). When the file reaches `AUDIT_LOG_MAX_MB` it is rotated to `data/audit.log.1` and a new one is started; only one backup is kept. Passwords, access tokens and private keys are not recorded. **The commands that were run and their output (truncated to 4000 characters per field) are recorded**; secrets written into a command or its output end up in the log too. Messages are recorded in the language of the request that caused them.

## Interface language

The language is chosen per request from the browser's `Accept-Language` header: Turkish if the browser prefers it, English otherwise. This covers the web panel, login pages, error messages, and MCP tool descriptions and messages (MCP clients normally send no `Accept-Language`, so they get English).

Source strings are English; the Turkish translations live in [`public-gateway/i18n.py`](public-gateway/i18n.py). To add a string, wrap it in `t("...")` and add its translation to the `TR` dictionary; a test fails if a translation is missing.

## Development and tests

```bash
cd public-gateway
pip install -r requirements-dev.txt
python -m pytest
```

- The tests do not connect to real servers. SSH is mocked; for Cockpit, `tests/fake_cockpit.py` contains a fake server written after the behaviour of the real cockpit-ws.
- Testing against a real Cockpit:

  ```bash
  COCKPIT_TEST_URL=https://host:9090 COCKPIT_TEST_USER=... COCKPIT_TEST_PASSWORD=... python -m pytest tests/test_cockpit_real.py
  ```
- GitHub Actions runs the tests with Python 3.11 on every push and PR.

> The `mcp` package is pinned to `<2`; the code uses the mcp 1.x API.

## Security notes

- The gateway can perform operations with administrator privileges on the registered servers. Keep `ALLOWED_USERS` short and consider `MCP_READ_ONLY=true` where changes are not needed.
- Even if the Cockpit password is weak, nobody can log in without `MCP_API_KEY`. If the token is wrong, the password is never sent to Cockpit. After 10 failed attempts an IP address is blocked for 5 minutes.
- A token in the URL is hidden in the gateway's access log, but your reverse proxy logs it too. Prefer entering it on the login page, or mask `token=` in the proxy's log format.
- Panel sessions last 8 hours, are revoked on the server at logout and end when the gateway restarts. An open SSE connection is re-checked every 30 seconds; Streamable HTTP checks every request.
- TLS verification is off by default for Cockpit connections (for self-signed certificates); it is never verified for the Cockpit connection inside the SSH tunnel. For SSH, the server key is stored on first connection and any change is refused until *Forget host key* is used in the panel, so the very first connection has to be trusted.
- Keep `SECRET_KEY` and the `data/` folder secret, and do not change `SECRET_KEY`.
- `servers.json` is never treated as empty when it cannot be read: the gateway reports an error instead of overwriting it.

## Components

| Folder | Description |
| --- | --- |
| `public-gateway/` | MCP server, OAuth authorization, web panel, Cockpit and SSH clients |

## License

[GNU GPL v3](LICENSE)
