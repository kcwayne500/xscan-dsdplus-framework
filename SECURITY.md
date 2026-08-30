# Security policy

## Reporting a vulnerability

Please report a security issue privately to the repository owner instead of opening a public issue. Do not include real passwords, API tokens, radio recordings, device keys, or signing material in a report.

## Deployment guidance

XScan is local-only by default and binds its application server to `127.0.0.1`. Keep that default unless you understand the exposure.

- Never forward the XScan backend ports (`8890` or `8891`) directly to the internet.
- For remote access, use the supplied Caddy HTTPS reverse-proxy setup, a domain you control, and a strong administrator password.
- Keep `%LOCALAPPDATA%\XScan\auth.json`, `xscan.db`, Android signing files, recordings, and scan lists out of Git.
- Treat recordings and call metadata as potentially sensitive even when the underlying radio traffic was unencrypted.
- Install dependency updates only from the publishers named in `THIRD_PARTY_NOTICES.md`.

XScan cannot decrypt encrypted radio traffic and does not attempt to bypass access controls.
