# Contributing

**English** · [Türkçe](CONTRIBUTING.tr.md)

Thanks for helping. Bug reports, fixes and documentation improvements are welcome.

## Reporting bugs

Open an issue: <https://github.com/bmericc/cockpit-mcp-gateway/issues>. Include:

- the version or image tag (`public-gateway/VERSION`, or the tag you pulled)
- the distribution of the managed server and the connection route shown in the tool output (`cockpit`, `cockpit-ssh` or `ssh`)
- what you did, what you expected and what happened, with the relevant `docker compose logs gateway` lines

Remove passwords, tokens, host names and IP addresses you do not want to share. **Security issues go through [SECURITY.md](SECURITY.md), not public issues.**

## Development setup

Python 3.11 or newer:

```bash
cd public-gateway
```

```bash
python -m venv .venv && . .venv/bin/activate
```

```bash
pip install -r requirements-dev.txt
```

The tests need no real servers: SSH is mocked and `tests/fake_cockpit.py` imitates cockpit-ws. To try against a real Cockpit, see [Development and tests](README.md#development-and-tests).

## Checks

CI runs these on every push and pull request; run them before opening a PR:

```bash
python -m pyflakes *.py
```

```bash
python -m pytest
```

```bash
pip-audit -r requirements.txt
```

The Docker image must still build for `linux/amd64`, `linux/arm64` and `linux/arm/v7`.

## Rules

- **Commit messages and pull request titles in English**, imperative mood (`Add ...`, `Fix ...`).
- **Interface strings:** source strings are English. Wrap new ones in `t("...")` and add the Turkish translation to the `TR` dictionary in `public-gateway/i18n.py`; a test fails if one is missing.
- **Documentation is bilingual:** when you change `README.md` or a file in `docs/`, update the Turkish counterpart (`README.tr.md`, `docs/*.tr.md`, `docs/genel-bakis.md` for `docs/overview.md`) in the same pull request.
- Changing tools must stay behind `confirm: true` and be disabled by `MCP_READ_ONLY`. Pass commands as argv, never through a shell string built from inputs.
- Add an entry under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md).
- Releases: bump `public-gateway/VERSION`, then tag `vX.Y.Z` with the same number; CI checks that they match.

## License

By contributing you agree that your contribution is licensed under the [GNU GPL v3](LICENSE).
