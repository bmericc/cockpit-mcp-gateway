# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Docker installation guide and panel feature guide with screenshots, in English and Turkish (`docs/docker-install.md`, `docs/features.md`).
- `SECURITY.md`, `CONTRIBUTING.md` and this changelog.
- Docker images get a `{{major}}` tag (`1`) next to `1.0.0` and `1.0`.
- A GitHub release page is created for every version tag, with the notes taken from this changelog (`.github/workflows/release.yml`).

### Changed

- `latest` now moves only when a version tag is pushed; builds of `main` get only a `sha-<commit>` tag.
- Docker Hub publishing is skipped when the `DOCKERHUB_TOKEN` secret is missing; the Docker Hub user comes from the `DOCKERHUB_USERNAME` variable (repository owner by default).
- Image builds use the GitHub Actions cache.
- Cover and diagram images no longer show Claude.

## [1.0.0] - 2026-11-01

First versioned release.

### Added

- MCP gateway that manages Linux servers through Cockpit, with an SSH tunnel to Cockpit and plain SSH as fallbacks.
- Login with a Cockpit account plus a gateway password (`MCP_API_KEY`); OAuth 2.1 with PKCE and dynamic client registration, and HTTP Basic.
- Streamable HTTP transport at `/mcp`; HTTP+SSE stays at `/sse`.
- Read tools (`fleet_health`, `server_info`, `resource_usage`, `service_status`, `read_logs`, `firewall_status`, `available_updates` and more) and changing tools behind `confirm: true` (`service_action`, `install_updates`, `package_install`, `package_remove`, `firewall_rule`, `reboot_server`, `run_remote_command`); `MCP_READ_ONLY` disables the changing ones.
- Debian/Ubuntu (`apt`, `ufw`, AppArmor) and Fedora/RHEL (`dnf`, `firewalld`, SELinux) support.
- Web panel: servers with connection check, shared SSH keys, gateway public IP, connection proxy per server or `OUTBOUND_PROXY`.
- Audit log of MCP calls, connections and panel actions (`data/audit.log`, `/logs`).
- English interface with Turkish chosen from `Accept-Language`.
- Docker images for `linux/amd64`, `linux/arm64` and `linux/arm/v7` on GHCR and Docker Hub.

[Unreleased]: https://github.com/bmericc/cockpit-mcp-gateway/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/bmericc/cockpit-mcp-gateway/releases/tag/v1.0.0
