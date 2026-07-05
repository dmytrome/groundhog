from groundhog_mcp.config import load_config


def test_defaults(monkeypatch):
    for key in (
        "CDP_URL",
        "GROUNDHOG_MIN_DELAY_MS",
        "GROUNDHOG_BLOCK_PRIVATE_IPS",
        "GROUNDHOG_MAX_TOKENS",
        "GROUNDHOG_AUTO_START_BROWSER",
        "GROUNDHOG_BROWSER_IMAGE",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.cdp_url == "http://127.0.0.1:9222"
    assert cfg.min_delay_ms == 5000
    assert cfg.block_private_ips is True
    assert cfg.max_tokens == 20000
    assert cfg.auto_start_browser is True  # turnkey: on by default
    assert cfg.browser_image == "ghcr.io/dmytrome/groundhog:latest"


def test_auto_start_can_be_disabled(monkeypatch):
    monkeypatch.setenv("GROUNDHOG_AUTO_START_BROWSER", "false")
    monkeypatch.setenv("GROUNDHOG_BROWSER_IMAGE", "custom/image:1")
    cfg = load_config()
    assert cfg.auto_start_browser is False
    assert cfg.browser_image == "custom/image:1"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("CDP_URL", "http://127.0.0.1:9333")
    monkeypatch.setenv("GROUNDHOG_MIN_DELAY_MS", "0")
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "false")
    cfg = load_config()
    assert cfg.cdp_url == "http://127.0.0.1:9333"
    assert cfg.min_delay_ms == 0
    assert cfg.block_private_ips is False
