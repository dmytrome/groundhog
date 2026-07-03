# Anti-bot conformance harness

`antibot.py` drives the running container over **raw CDP** — never enabling the
Runtime/Console domains, exactly like the Groundhog engine — visits each detector,
records a verdict plus a full-page screenshot, probes the fingerprint surface, and
writes a self-contained **`report.html`** (screenshots embedded) you can open in a
browser.

```bash
# from the repo root
docker compose up --build -d          # start the stealth browser

pip install -r tests/requirements.txt # just `websockets`
python tests/antibot.py               # writes tests/report.html
open tests/report.html                # see verdicts + screenshots
```

- `CDP_URL` defaults to `http://127.0.0.1:9222`. Use `127.0.0.1`, not `localhost`.
- Match the container's `TZ` to the exit-IP geo (e.g. `TZ=Europe/London docker compose
  up -d`), or the location-coherence detectors will flag a UTC clock behind a non-UTC IP.
- Exit code is non-zero if any pass/fail detector fails, so it works in CI.
- Two outputs: `report.html` (screenshots embedded, gitignored — open locally) and
  `../RESULTS.md` (a small committed table anyone can read on GitHub as the public proof).
  Regenerate both by re-running the harness.

Pass/fail detectors: deviceandbrowserinfo (`isBot`), iphey (Trustworthy), browserscan
(Normal), sannysoft (0 fails). The **Fingerprint surface** section records diagnostic
values (WebGL renderer, canvas, timezone, `navigator.webdriver`, platform) rather than a
pass/fail — use it to confirm coherence. Detectors are network-dependent and their markup
changes; the screenshot is the source of truth.
