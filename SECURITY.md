# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
(the **Security** tab → **Report a vulnerability**). Do not open a public issue for
security problems. We aim to acknowledge reports within a few days.

## Threat model

Groundhog fetches attacker-influenced URLs on behalf of an agent, so two risks are
in scope by design:

- **SSRF.** URLs may be attacker-controlled. The SSRF guard (enabled by
  `GROUNDHOG_BLOCK_PRIVATE_IPS=true`) resolves the host and blocks loopback, private,
  link-local (incl. cloud-metadata `169.254.169.254`), reserved, multicast, unspecified,
  CGNAT, and IPv4-mapped IPv6, and re-checks the final URL after redirects. Disabling it
  removes this protection — only do so on a trusted network.
- **Unauthenticated CDP.** The stealth browser exposes an **unauthenticated** CDP
  endpoint (port 9222). Anyone who can reach it controls the browser. Bind it to
  localhost or a trusted private network; never expose it publicly.

## Out of scope

- Bypassing a specific site's anti-bot system (stealth is best-effort, not guaranteed).
- Content served by third-party sites fetched through the tool.

## Supported versions

Only the latest released version receives security fixes while the project is pre-1.0.
