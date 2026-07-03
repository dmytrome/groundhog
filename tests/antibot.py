#!/usr/bin/env python3
"""Anti-bot conformance harness.

Drives the running container over RAW CDP — never enabling the Runtime/Console
domains, exactly like the Groundhog engine — visits each detector, records a
verdict plus a full-page screenshot, probes the fingerprint surface (Tier 2),
and writes a self-contained HTML report (screenshots embedded) to
`tests/report.html`.

    docker compose up --build -d
    pip install -r tests/requirements.txt      # just `websockets`
    python tests/antibot.py                     # writes tests/report.html
    CDP_URL=http://127.0.0.1:9222 python tests/antibot.py

Exit code is non-zero if any pass/fail detector fails, so it works in CI.
"""

import asyncio
import json
import os
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import websockets

CDP = os.environ.get("CDP_URL", "http://127.0.0.1:9222")
REPORT = Path(__file__).parent / "report.html"
RESULTS_MD = Path(__file__).parent.parent / "RESULTS.md"
_MAX_SHOT_H = 6000
_MAX_DETAIL = 90
_INTRO = "Driven over raw CDP (no Runtime.enable) against"


def _verdict(ok):
    """Tri-state classifier each renderer decorates: None -> info, else pass/fail."""
    return "info" if ok is None else ("pass" if ok else "fail")


class Session:
    """Minimal raw-CDP driver: Page domain only, never Runtime.enable."""

    def __init__(self, ws):
        self._ws = ws
        self._id = 0

    async def send(self, method, params=None, sid=None):
        self._id += 1
        mid = self._id
        msg = {"id": mid, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        await self._ws.send(json.dumps(msg))
        while True:
            data = json.loads(await self._ws.recv())
            if data.get("id") == mid:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})

    async def open(self, url, settle):
        target = await self.send("Target.createTarget", {"url": "about:blank"})
        tid = target["targetId"]
        sid = (await self.send("Target.attachToTarget", {"targetId": tid, "flatten": True}))[
            "sessionId"
        ]
        await self.send("Page.enable", sid=sid)
        await self.send("Page.navigate", {"url": url}, sid=sid)
        await asyncio.sleep(settle)
        return tid, sid

    async def eval(self, sid, expr):
        res = await self.send(
            "Runtime.evaluate", {"expression": expr, "returnByValue": True}, sid=sid
        )
        return res.get("result", {}).get("value")

    async def screenshot(self, sid):
        metrics = await self.send("Page.getLayoutMetrics", sid=sid)
        size = metrics.get("cssContentSize") or metrics.get("contentSize")
        w = min(int(size["width"]) + 1, 2000)
        h = min(int(size["height"]) + 1, _MAX_SHOT_H)
        shot = await self.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": True,
                "clip": {"x": 0, "y": 0, "width": w, "height": h, "scale": 1},
            },
            sid=sid,
        )
        return shot["data"]


def _isbot_pass(text):
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        return None, "could not parse"
    data = json.loads(m.group())
    flags = [k for k, v in data.get("details", {}).items() if v is True]
    ok = data.get("isBot") is False
    return ok, f"isBot={data.get('isBot')}, flags={flags or 'none'}"


def _match_pass(pattern, good):
    def check(text):
        m = re.search(pattern, text or "", re.I)
        v = m.group(0).strip() if m else "(not found)"
        return (bool(m) and bool(re.search(good, v, re.I))), v

    return check


def _info(text):
    """Record a value with no pass/fail (diagnostic auditors like CreepJS)."""
    return None, (str(text).strip() or "?")[:_MAX_DETAIL]


# name, url, settle seconds, JS probe expression, verdict interpreter
DETECTORS = [
    (
        "deviceandbrowserinfo",
        "https://deviceandbrowserinfo.com/are_you_a_bot",
        8,
        "(()=>{const el=[...document.querySelectorAll('pre,code')]"
        ".find(e=>e.innerText.includes('isBot'));return el?el.innerText:'';})()",
        _isbot_pass,
    ),
    (
        # iphey flickers "Suspicious" mid-load before settling; give it room.
        "iphey",
        "https://iphey.com/",
        26,
        "(document.body.innerText.match(/(Trustworthy|Suspicious|Unreliable)/i)||['?'])[0]",
        _match_pass(r"(Trustworthy|Suspicious|Unreliable)", r"Trustworthy"),
    ),
    (
        "browserscan",
        "https://www.browserscan.net/bot-detection",
        12,
        "(document.body.innerText.match(/(Normal|Detected)/i)||['?'])[0]",
        _match_pass(r"(Normal|Detected)", r"Normal"),
    ),
    (
        "sannysoft",
        "https://bot.sannysoft.com/",
        6,
        "(()=>{let f=0;document.querySelectorAll('td.failed,td.warn').forEach(()=>f++);"
        "return 'fails='+f;})()",
        _match_pass(r"fails=\d+", r"fails=0\b"),
    ),
    (
        "areyouheadless",
        "https://arh.antoinevastel.com/bots/areyouheadless",
        5,
        "(document.body.innerText.match(/You are (not )?(a )?chrome headless/i)||['?'])[0]",
        _match_pass(r"You are (not )?(a )?chrome headless", r"not chrome headless"),
    ),
    (
        "creepjs",
        "https://abrahamjuliot.github.io/creepjs/",
        11,
        "(()=>{const t=document.body.innerText;"
        "const h=(t.match(/(\\d+)% (like )?headless/i)||[''])[0];"
        "const s=(t.match(/(\\d+)% stealth/i)||[''])[0];"
        "return [h,s].filter(Boolean).join(' | ')||'?';})()",
        _info,
    ),
    (
        "incolumitas",
        "https://bot.incolumitas.com/",
        10,
        "(()=>{const el=[...document.querySelectorAll('pre,code')]"
        ".find(e=>/is_datacenter|behav|score/i.test(e.innerText));"
        "return el?el.innerText:'?';})()",
        _info,
    ),
]

# Fingerprint-surface probe (Tier 2) — read coherent values in-page, no scraping.
FP_PROBE = """(()=>{
  const out = {};
  out.userAgent = navigator.userAgent;
  out.platform = navigator.platform;
  out.language = navigator.language + ' / ' + (navigator.languages||[]).join(',');
  out.webdriver = String(navigator.webdriver);
  out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.hardwareConcurrency = navigator.hardwareConcurrency;
  out.deviceMemory = navigator.deviceMemory;
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const d = gl && gl.getExtension('WEBGL_debug_renderer_info');
    out.webglVendor = d ? gl.getParameter(d.UNMASKED_VENDOR_WEBGL) : 'no-ext';
    out.webglRenderer = d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'no-ctx';
  } catch (e) { out.webglRenderer = 'err:' + e.message; }
  try {
    const c = document.createElement('canvas'); c.width = 200; c.height = 40;
    const ctx = c.getContext('2d'); ctx.textBaseline = 'top';
    ctx.font = '14px Arial'; ctx.fillText('Groundhog canvas probe', 2, 2);
    out.canvasHash = c.toDataURL().slice(-16);
  } catch (e) { out.canvasHash = 'err'; }
  return out;
})()"""


async def main():
    with urllib.request.urlopen(CDP.rstrip("/") + "/json/version", timeout=5) as r:
        version = json.load(r)
    ws_url = version["webSocketDebuggerUrl"]

    detector_rows = []
    failures = 0
    async with websockets.connect(ws_url, max_size=None) as ws:
        s = Session(ws)
        for name, url, settle, probe, interpret in DETECTORS:
            try:
                tid, sid = await s.open(url, settle)
                raw = await s.eval(sid, probe)
                ok, detail = interpret(raw)
                shot = await s.screenshot(sid)
                await s.send("Target.closeTarget", {"targetId": tid})
            except Exception as exc:  # a detector erroring is a data point, not a crash
                ok, detail, shot = False, f"error: {exc}", ""
            if ok is False:
                failures += 1
            detector_rows.append((name, url, ok, detail, shot))
            print(f"  {_verdict(ok).upper():4}  {name}: {detail}")

        tid, sid = await s.open("https://example.com/", 2)
        fingerprint = await s.eval(sid, FP_PROBE) or {}
        await s.send("Target.closeTarget", {"targetId": tid})

    REPORT.write_text(_html(version, detector_rows, fingerprint))
    RESULTS_MD.write_text(_markdown(version, detector_rows, fingerprint))
    graded = sum(1 for _, _, ok, _, _ in detector_rows if ok is not None)
    print(f"\nreport: {REPORT}\nresults: {RESULTS_MD}")
    print(f"{graded - failures}/{graded} pass/fail detectors passed")
    return 1 if failures else 0


def _markdown(version, rows, fp):
    """A small, image-free table committed as the public proof (RESULTS.md)."""

    def cell(x):
        return str(x).replace("|", "\\|").replace("\n", " ")

    out = [
        "# Anti-bot conformance results",
        "",
        f"{_INTRO} `{version.get('Browser')}`, regenerated by "
        f"`tests/antibot.py`. Last run: {datetime.now(UTC):%Y-%m-%d}.",
        "",
        "| Detector | Result | Detail |",
        "| --- | --- | --- |",
    ]
    for name, url, ok, detail, _shot in rows:
        status = {"info": "info", "pass": "**pass**", "fail": "**FAIL**"}[_verdict(ok)]
        out.append(f"| [{name}]({url}) | {status} | {cell(detail)} |")
    out += ["", "## Fingerprint surface", "", "| Key | Value |", "| --- | --- |"]
    out += [f"| {k} | {cell(v)} |" for k, v in fp.items()]
    out.append("")
    return "\n".join(out)


def _html(version, rows, fp):
    def esc(x):
        return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    cards = []
    for name, url, ok, detail, shot in rows:
        cls = _verdict(ok)
        label = {"info": "&mdash;", "pass": "PASS", "fail": "FAIL"}[cls]
        img = (
            f'<img src="data:image/png;base64,{shot}" alt="{esc(name)}">'
            if shot
            else "<em>no screenshot</em>"
        )
        cards.append(
            f'<section class="{cls}"><h3><span class="tag">{label}</span> '
            f'<a href="{esc(url)}">{esc(name)}</a></h3>'
            f"<p>{esc(detail)}</p><div class=shot>{img}</div></section>"
        )
    fp_rows = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in fp.items())
    return f"""<!doctype html><meta charset=utf-8>
<title>Groundhog anti-bot report</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:2rem;max-width:900px}}
 h1{{margin-bottom:.2rem}} .sub{{color:#666}}
 section{{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}}
 section.pass{{border-left:5px solid #1a9d4b}} section.fail{{border-left:5px solid #d33}}
 section.info{{border-left:5px solid #888}}
 .tag{{font-weight:700;font-size:.8rem;padding:.1rem .5rem;border-radius:4px;background:#eee}}
 .pass .tag{{background:#d7f3e0;color:#1a6b36}} .fail .tag{{background:#fadbdb;color:#a11}}
 .shot img{{max-width:100%;border:1px solid #eee;border-radius:4px}}
 table{{border-collapse:collapse;width:100%}}
 th,td{{text-align:left;padding:.3rem .6rem;border-bottom:1px solid #eee;vertical-align:top}}
 th{{color:#555;white-space:nowrap}}
 code{{background:#f4f4f4;padding:.1rem .3rem;border-radius:3px}}
</style>
<h1>Groundhog anti-bot report</h1>
<p class=sub>{_INTRO} <code>{esc(version.get("Browser"))}</code>.
Screenshots are the source of truth.</p>
<h2>Detectors</h2>
{"".join(cards)}
<h2>Fingerprint surface</h2>
<table>{fp_rows}</table>
"""


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
