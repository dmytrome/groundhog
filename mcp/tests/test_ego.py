import asyncio
import json
import sys
import textwrap

import pytest
import websockets

from groundhog_mcp import ego

_FAKE_EGO = textwrap.dedent(
    """
    import asyncio, json, re, sys
    import websockets

    async def main():
        conf = json.loads(re.match(r"const GROUNDHOG = (.*?);\\n", sys.argv[3]).group(1))
        url = f"ws://127.0.0.1:{conf['port']}/ego/{conf['token']}"
        async with websockets.connect(url) as ws:
            async for raw in ws:
                m = json.loads(raw)
                if "msg" in m:
                    reply = {"id": m["msg"]["id"], "result": {"ran": m["msg"]["method"]}}
                    await ws.send(json.dumps({"cid": m["cid"], "msg": reply}))
        open(sys.argv[0] + ".finished", "w").close()

    asyncio.run(main())
    """
)


@pytest.fixture
def fake_ego(tmp_path, monkeypatch):
    binary = tmp_path / "ego-browser"
    binary.write_text(f"#!{sys.executable}\n{_FAKE_EGO}")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    return binary


async def test_a_command_reaches_ego_and_its_reply_comes_back(fake_ego):
    bridge = ego.Bridge()
    url = await bridge.start()

    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"id": 7, "method": "Target.createTarget"}))
        reply = json.loads(await ws.recv())
    await bridge.aclose()

    assert reply == {"id": 7, "result": {"ran": "Target.createTarget"}}


async def test_a_client_without_the_token_is_turned_away(fake_ego):
    bridge = ego.Bridge()
    url = await bridge.start()

    try:
        async with websockets.connect(url.rsplit("/", 1)[0] + "/guess") as ws:
            with pytest.raises(websockets.ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 5)
    finally:
        await bridge.aclose()


async def test_closing_groundhog_lets_ego_finish_its_task_space(fake_ego):
    bridge = ego.Bridge()
    await bridge.start()

    await bridge.aclose()

    assert (fake_ego.parent / "ego-browser.finished").exists()


async def test_without_ego_installed_the_error_says_what_to_install(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(OSError, match="install it so `ego-browser` is on PATH"):
        await ego.Bridge().start()
