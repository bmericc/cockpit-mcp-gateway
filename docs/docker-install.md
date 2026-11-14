# Installing with Docker

**English** · [Türkçe](docker-install.tr.md)

[← Back to README](../README.md)

Two ways to run the gateway:

- [A. Prebuilt image](#a-prebuilt-image) (recommended): pull the published image, no source code needed.
- [B. From source](#b-from-source): build the image yourself from a clone of the repository.

## Requirements

- A Linux machine with Docker Engine and the Docker Compose plugin (`docker compose version` should work).
- Images exist for `linux/amd64`, `linux/arm64` and `linux/arm/v7` (Raspberry Pi etc.).
- A domain name and a reverse proxy that terminates HTTPS (nginx, Caddy, Traefik etc.). MCP clients such as claude.ai only connect to HTTPS addresses.
- On every server you want to manage: Cockpit, and a Cockpit user with `sudo` rights for administrative operations. See [Preparing the servers](#preparing-the-servers).
- Cockpit on the machine that runs the gateway too (or on any machine it can reach): logins to the gateway are checked against it (`COCKPIT_AUTH_URL`).

## A. Prebuilt image

Images are published to two registries; both hold the same image:

| Registry | Image |
| --- | --- |
| GitHub Container Registry | `ghcr.io/bmericc/cockpit-mcp-gateway` |
| Docker Hub | `bmericc/cockpit-mcp-gateway` |

Tags: `latest` (newest release), `1.0.0`, `1.0`, `1` (by version) and `sha-<commit>` (every build of `main`).

1. Create a folder:

   ```bash
   mkdir -p /opt/cockpit-mcp-gateway && cd /opt/cockpit-mcp-gateway
   ```

2. Download the compose file and the sample settings:

   ```bash
   curl -fsSLO https://raw.githubusercontent.com/bmericc/cockpit-mcp-gateway/main/docker-compose.yml
   ```

   ```bash
   curl -fsSL -o .env https://raw.githubusercontent.com/bmericc/cockpit-mcp-gateway/main/sample.env
   ```

3. Edit `.env` (see [Settings](#settings)). At least set `PUBLIC_URL` and `ALLOWED_USERS`:

   ```bash
   nano .env
   ```

4. Pull the image and start it:

   ```bash
   docker compose pull
   ```

   ```bash
   docker compose up -d --no-build
   ```

5. Check that it is running:

   ```bash
   docker compose ps
   ```

   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7435/login
   ```

   `200` means the gateway is up. Then set up the [reverse proxy](#reverse-proxy).

To pin a version instead of `latest`, add `IMAGE_TAG=1.0.0` to `.env`. To use Docker Hub instead of GHCR, change the `image:` line in `docker-compose.yml` to `bmericc/cockpit-mcp-gateway:${IMAGE_TAG:-latest}`.

## B. From source

1. Clone the repository:

   ```bash
   git clone https://github.com/bmericc/cockpit-mcp-gateway.git /opt/cockpit-mcp-gateway && cd /opt/cockpit-mcp-gateway
   ```

2. Create and edit the settings:

   ```bash
   cp sample.env .env
   ```

   ```bash
   nano .env
   ```

3. Build and start:

   ```bash
   docker compose up -d --build
   ```

The image is built from `public-gateway/Dockerfile`. To run without Docker (development), see [Development and tests](../README.md#development-and-tests).

## Settings

All settings live in `.env`. The gateway needs **no third-party API keys**: logins are checked against Cockpit, and servers are reached through Cockpit or SSH.

| Variable | Required | What it does |
| --- | --- | --- |
| `ALLOWED_USERS` | **yes** | Cockpit users allowed into the gateway, comma separated. The gateway does not start if it is empty. |
| `PUBLIC_URL` | **yes** | The HTTPS address the gateway is reached at, e.g. `https://mcp.example.com`. OAuth redirects and the addresses shown in the panel use it. |
| `COCKPIT_AUTH_URL` | no | The Cockpit that checks logins. Default `https://host.docker.internal:9090`: the Cockpit of the machine running the gateway. |
| `COCKPIT_AUTH_VERIFY_TLS` | no | `true` to verify that Cockpit's certificate. Default `false` (Cockpit uses a self-signed certificate). |
| `SECRET_KEY` | no | Signs sessions and encrypts the stored Cockpit passwords and SSH keys. If empty, one is generated once and kept in `data/.secret_key`. **Never change it afterwards**: stored passwords could no longer be decrypted. |
| `MCP_API_KEY` | no | The gateway password (second factor, asked together with the Cockpit password). If empty, one is generated once and kept in `data/.mcp_api_key`. Changing it logs everyone out. |
| `PORT` | no | Port inside the container. Default `7435`. |
| `TRUSTED_PROXIES` | no | Addresses allowed to set `X-Forwarded-*` (comma separated, CIDR allowed). Default `127.0.0.1`. With a reverse proxy on the host, use the Docker bridge range, e.g. `172.16.0.0/12`. |
| `MCP_READ_ONLY` | no | `true`: MCP clients only get read tools. Default `false`. |
| `SSH_LOGINS` | no | Users and key folders tried for SSH, e.g. `root:/root/.ssh`. |
| `OUTBOUND_PROXY` | no | Proxy for connections to the servers: `socks5://`, `socks5h://` or `http://`. |
| `AUDIT_LOG_MAX_MB` | no | Size at which `data/audit.log` is rotated. Default `10`. |
| `BIND_ADDRESS` | no | Read by `docker-compose.yml`, not by the gateway: the host address the port is published on. Default `127.0.0.1`. |
| `IMAGE_TAG` | no | Read by `docker-compose.yml`: the image tag. Default `latest`. |

To read the generated gateway password:

```bash
cat data/.mcp_api_key
```

## Reverse proxy

`docker-compose.yml` publishes the port only on `127.0.0.1:7435`, so a reverse proxy on the same machine is expected. The MCP endpoints keep long-lived streams open; disable buffering and use long timeouts.

nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.example.com;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:7435;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
```

Caddy:

```caddyfile
mcp.example.com {
    reverse_proxy 127.0.0.1:7435 {
        flush_interval -1
    }
}
```

Requests from nginx/Caddy on the host reach the container from the Docker bridge network, so set `TRUSTED_PROXIES=172.16.0.0/12` (or the exact bridge subnet). Otherwise the audit log shows the bridge address instead of the client's.

The access token can be part of the MCP address (`?token=`). Your proxy logs it; mask `token=` in the proxy's log format or enter the token on the login page instead.

## Preparing the servers

On each server to be managed:

```bash
sudo apt install -y cockpit
```

```bash
sudo systemctl enable --now cockpit.socket
```

On Fedora/RHEL use `sudo dnf install -y cockpit`. A minimal Debian has no `sudo`: `apt install sudo && usermod -aG sudo USER`.

If the gateway can reach the server over SSH, port 9090 does not need to be open: Cockpit is used through an SSH tunnel. Otherwise open 9090 only to the gateway's address, which the panel shows at the top. Servers are then added in the panel: see the [Feature guide](features.md#servers).

## Connecting an MCP client

- **claude.ai:** *Settings → Connectors → Add custom connector*, address `https://mcp.example.com/mcp?token=<MCP_API_KEY>`. Log in with your Cockpit account on the page that opens.
- **Claude Code:**

  ```bash
  claude mcp add --transport sse cockpit-gateway "https://mcp.example.com/sse?token=<MCP_API_KEY>"
  ```

Details: [Logging in and connecting an MCP client](../README.md#logging-in-and-connecting-an-mcp-client).

## Updating

Prebuilt image:

```bash
docker compose pull
```

```bash
docker compose up -d --no-build
```

From source:

```bash
git pull
```

```bash
docker compose up -d --build
```

Settings and data stay in `.env` and `data/`. Panel sessions end on restart; OAuth clients keep their tokens.

## Backup

Everything the gateway keeps is in `data/`:

| File | Content |
| --- | --- |
| `.secret_key` | Encryption key (only if `SECRET_KEY` is empty in `.env`) |
| `.mcp_api_key` | Gateway password (only if `MCP_API_KEY` is empty) |
| `servers.json` | Servers, encrypted Cockpit passwords, pinned SSH host keys |
| `ssh_keys.json` | Shared SSH keys, encrypted |
| `oauth.json` | OAuth clients and hashed tokens |
| `audit.log`, `audit.log.1` | Audit log |

Back up `data/` and `.env` together. Without the matching `SECRET_KEY` the passwords and keys in the backup cannot be decrypted. Keep the backup private.

```bash
tar czf cockpit-mcp-gateway-$(date +%F).tar.gz .env data
```

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Container restarts, log says `ALLOWED_USERS is empty` | Set `ALLOWED_USERS` in `.env`, then `docker compose up -d`. |
| Login fails although the password is right | The user is not in `ALLOWED_USERS`, the gateway password is wrong, or `COCKPIT_AUTH_URL` is not reachable from the container. Check with `docker compose logs gateway`. After 10 failed attempts an IP address is blocked for 5 minutes. |
| `host.docker.internal` does not resolve | Keep the `extra_hosts` entry in `docker-compose.yml`, or set `COCKPIT_AUTH_URL` to the host's address. |
| claude.ai cannot connect | `PUBLIC_URL` must be the exact HTTPS address, and the proxy must pass `/mcp`, `/sse`, `/messages/`, `/.well-known/`, `/authorize`, `/token` and `/register`. |
| Audit log shows `172.x.x.x` for every client | Add the Docker bridge range to `TRUSTED_PROXIES`. |
| Servers stop connecting after a move or restore | `SECRET_KEY` (or `data/.secret_key`) changed, so stored Cockpit passwords cannot be decrypted. Restore the old one. |
| SSH fallback cannot read keys | Keys must be under a folder mounted into the container (default `/root/.ssh`) and listed in `SSH_LOGINS`. |

Logs:

```bash
docker compose logs -f gateway
```
