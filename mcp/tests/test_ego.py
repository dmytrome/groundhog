import asyncio
import json
import shutil
import textwrap
from pathlib import Path

import pytest
import websockets

from groundhog_mcp import ego, engine
from groundhog_mcp.cdp import CDPClient, CDPError

from .test_engine import _cfg

_TAKEOVER = json.loads(
    (Path(__file__).parent / "fixtures" / "ego_takeover_error.json").read_text()
)["message"]

_FAKE_SDK = textwrap.dedent(
    """
    import { writeFileSync } from "node:fs";
    const mode = process.env.FAKE_EGO_MODE || "";
    const dir = process.env.FAKE_EGO_DIR;
    const takeover = %s;
    let pages = 1;
    if (mode === "closed") await new Promise((r) => setTimeout(r, 30000));
    globalThis.taskSpace = async () => {
      if (mode === "name-in-use") {
        console.log("Error: EGO_TASK_SPACE_NAME_IN_USE: the name groundhog is held by the user");
        process.exit(1);
      }
      return {
        async newPage() {
          const id = ++pages;
          return {
            label: `p${id}`,
            targetId: `T${id}`,
            async cdp(method, params, opts) {
              if (mode === "hold" && method !== "Page.navigate") {
                await new Promise((r) => setTimeout(r, opts.timeout));
                throw new Error(`CDP request timed out: ${method}`);
              }
              if (method !== "Page.navigate") return {};
              if (mode === "takeover-navigate") throw new Error(takeover);
              if (mode === "bad-url") throw new Error("Cannot navigate to invalid URL");
              return { frameId: `T${id}`, loaderId: "L1" };
            },
            async events() {
              if (mode === "takeover-events") throw new Error(takeover);
              return [];
            },
            async close() {},
          };
        },
        async finish() { writeFileSync(`${dir}/finished`, ""); },
      };
    };
    """
) % json.dumps(_TAKEOVER)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the bridge")


@pytest.fixture
def fake_ego(tmp_path, monkeypatch):
    (tmp_path / "sdk.mjs").write_text(_FAKE_SDK)
    binary = tmp_path / "ego-browser"
    binary.write_text(
        "#!/bin/bash\n"
        'printf "%s " "$@" > "$FAKE_EGO_DIR/argv"\n'
        'exec node --input-type=module - < <(cat "$FAKE_EGO_DIR/sdk.mjs" -)\n'
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{shutil.which('node').rsplit('/', 1)[0]}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_EGO_DIR", str(tmp_path))
    return tmp_path


async def _attached(url: str) -> tuple[CDPClient, str]:
    cdp = CDPClient(url)
    await cdp.connect()
    target = await cdp.send("Target.createTarget", {"url": "about:blank"})
    att = await cdp.send("Target.attachToTarget", {"targetId": target["targetId"]})
    return cdp, att["sessionId"]


async def test_a_command_reaches_ego_and_its_reply_comes_back(fake_ego):
    bridge = ego.Bridge()
    cdp = CDPClient(await bridge.start())
    await cdp.connect()

    reply = await cdp.send("Target.createTarget", {"url": "about:blank"})
    await cdp.close()
    await bridge.aclose()

    assert reply == {"targetId": "T2"}


async def test_a_client_without_the_token_is_turned_away(fake_ego):
    bridge = ego.Bridge()
    url = await bridge.start()

    try:
        async with websockets.connect(url.rsplit("/", 1)[0] + "/guess") as ws:
            with pytest.raises(websockets.ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 5)
    finally:
        await bridge.aclose()


async def test_the_token_is_not_on_the_command_line(fake_ego):
    bridge = ego.Bridge()
    url = await bridge.start()
    await bridge.aclose()

    assert url.rsplit("/", 1)[1] not in (fake_ego / "argv").read_text()


async def test_closing_groundhog_lets_ego_finish_its_task_space(fake_ego):
    bridge = ego.Bridge()
    await bridge.start()

    await bridge.aclose()

    assert (fake_ego / "finished").exists()


@pytest.mark.parametrize("mode", ["takeover-navigate", "takeover-events"])
async def test_a_takeover_closes_the_connection_and_leaves_the_space_to_the_user(
    fake_ego, monkeypatch, mode
):
    monkeypatch.setenv("FAKE_EGO_MODE", mode)
    bridge = ego.Bridge()
    cdp = CDPClient(await bridge.start())

    with pytest.raises(CDPError):
        await cdp.connect()
        target = await cdp.send("Target.createTarget", {"url": "about:blank"})
        att = await cdp.send("Target.attachToTarget", {"targetId": target["targetId"]})
        sid = att["sessionId"]
        await cdp.send("Page.navigate", {"url": "https://example.com/"}, session_id=sid)
        await asyncio.wait_for(cdp.expect_event("Page.loadEventFired", session_id=sid), 5)
    await cdp.close()
    await bridge.aclose()

    assert ego.TAKEOVER in cdp.close_reason
    assert not (fake_ego / "finished").exists()


async def test_a_navigation_ego_refuses_fails_at_once(fake_ego, monkeypatch):
    monkeypatch.setenv("FAKE_EGO_MODE", "bad-url")
    bridge = ego.Bridge()
    cdp, sid = await _attached(await bridge.start())

    try:
        with pytest.raises(CDPError, match="Cannot navigate to invalid URL"):
            await asyncio.wait_for(
                cdp.send("Page.navigate", {"url": "nota"}, session_id=sid), timeout=5
            )
        assert not cdp.closed
    finally:
        await cdp.close()
        await bridge.aclose()


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("Runtime.evaluate", {"expression": "1", "timeout": 300}),
        ("Page.getFrameTree", {}),
        ("Page.createIsolatedWorld", {"frameId": "T2"}),
    ],
)
async def test_a_quick_call_ego_holds_gives_up_in_seconds(fake_ego, monkeypatch, method, params):
    monkeypatch.setenv("FAKE_EGO_MODE", "hold")
    bridge = ego.Bridge()
    cdp, sid = await _attached(await bridge.start())

    try:
        with pytest.raises(CDPError, match="timed out"):
            await asyncio.wait_for(cdp.send(method, params, session_id=sid), timeout=5)
    finally:
        await cdp.close()
        await bridge.aclose()


async def test_a_space_the_user_holds_is_left_alone(fake_ego, monkeypatch):
    monkeypatch.setenv("FAKE_EGO_MODE", "name-in-use")

    with pytest.raises(OSError, match="task space is with you"):
        await ego.Bridge().start()


async def test_a_fetch_during_a_takeover_tells_the_caller_to_hand_back_control(
    fake_ego, monkeypatch
):
    monkeypatch.setenv("FAKE_EGO_MODE", "takeover-navigate")

    async def public(url, cfg):
        return None

    monkeypatch.setattr(engine.safety, "check_url", public)
    provider = engine.EngineProvider(_cfg(browser="ego"))
    try:
        with pytest.raises(engine.BrowserUnavailableError, match="will not take it back"):
            await provider.fetch("https://example.com/")
    finally:
        await provider.aclose()


@pytest.mark.parametrize("mode,open_", [("", True), ("closed", False)])
async def test_ego_counts_as_reachable_only_while_it_answers(fake_ego, monkeypatch, mode, open_):
    monkeypatch.setenv("FAKE_EGO_MODE", mode)
    monkeypatch.setattr(ego, "_PROBE_TIMEOUT_S", 2.0)

    assert await ego.reachable() is open_


async def test_without_ego_installed_the_error_says_what_to_install(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(OSError, match="install it so `ego-browser` is on PATH"):
        await ego.Bridge().start()
