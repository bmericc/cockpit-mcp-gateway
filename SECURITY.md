# Security policy

**English** · [Türkçe](SECURITY.tr.md)

## Supported versions

Security fixes go into the latest release only. Update to the newest image (`latest` or the highest version tag) before reporting.

| Version | Supported |
| --- | --- |
| 1.x (latest) | ✓ |
| older | ✗ |

## Reporting a vulnerability

**Do not open a public issue.** Report privately through GitHub:

<https://github.com/bmericc/cockpit-mcp-gateway/security/advisories/new>

Please include the version or image tag, the setup (reverse proxy, `MCP_READ_ONLY`, transport), steps to reproduce and the impact. You will get an answer within a few days. Once a fix is released, the advisory is published with credit unless you ask otherwise.

## Scope

In scope: the gateway in `public-gateway/` and the published Docker images, for example:

- getting past the login (Cockpit password + gateway password, OAuth, HTTP Basic)
- running commands or changing servers without `confirm: true`, or despite `MCP_READ_ONLY=true`
- command injection through tool inputs
- reading stored passwords, SSH keys or tokens from `data/`
- secrets leaking into logs, the panel or tool output
- CSRF, XSS or session issues in the web panel

Out of scope: vulnerabilities in Cockpit, OpenSSH or the managed servers themselves; setups that ignore the documented requirements (no HTTPS, `TRUSTED_PROXIES=*`, the port published to the internet without a reverse proxy); actions an allowed user is meant to be able to do.

## Running it safely

- Put it behind HTTPS and keep the port on `127.0.0.1` (the `docker-compose.yml` default).
- Keep `ALLOWED_USERS` short. Use `MCP_READ_ONLY=true` where changes are not needed.
- Keep `.env` and `data/` private and back them up together. Never change `SECRET_KEY` after the first start.
- Set `TRUSTED_PROXIES` to your proxy's address only.
- Mask `token=` in your reverse proxy's access log, or enter the token on the login page instead of the URL.
- Update to new releases; GitHub Dependabot alerts are enabled for this repository.

More: [Security notes](README.md#security-notes).
