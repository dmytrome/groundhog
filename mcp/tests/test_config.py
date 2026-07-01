from groundhog_mcp.config import load_config


def test_defaults(monkeypatch):
    for key in ("CDP_URL", "GROUNDHOG_MIN_DELAY_MS", "GROUNDHOG_BLOCK_PRIVATE_IPS",
                "GROUNDHOG_MAX_TOKENS", "GROUNDHOG_USER_AGENT"):
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.cdp_url == "http://127.0.0.1:9222"
    assert cfg.min_delay_ms == 5000
    assert cfg.block_private_ips is True
    assert cfg.max_tokens == 20000
    assert "Chrome/149" in cfg.user_agent


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("CDP_URL", "http://127.0.0.1:9333")
    monkeypatch.setenv("GROUNDHOG_MIN_DELAY_MS", "0")
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "false")
    cfg = load_config()
    assert cfg.cdp_url == "http://127.0.0.1:9333"
    assert cfg.min_delay_ms == 0
    assert cfg.block_private_ips is False
