import pytest

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


def test_search_defaults_to_auto_with_no_instance(monkeypatch):
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.delenv("GROUNDHOG_SEARCH_BACKEND", raising=False)
    cfg = load_config()
    assert cfg.search_backend == "auto"
    assert cfg.searxng_url is None


def test_search_backend_and_instance_from_env(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://sx:8080/")
    monkeypatch.setenv("GROUNDHOG_SEARCH_BACKEND", "searxng")
    cfg = load_config()
    assert cfg.search_backend == "searxng"
    assert cfg.searxng_url == "http://sx:8080/"


def test_the_installed_chrome_is_the_default_browser(monkeypatch):
    for key in (
        "GROUNDHOG_BROWSER",
        "GROUNDHOG_CHROME_PATH",
        "GROUNDHOG_CHROME_PROFILE",
        "GROUNDHOG_COMPOSE_FILE",
        "GROUNDHOG_BROWSER_IMAGE",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.browser == "chrome"
    assert cfg.chrome_path is None
    assert cfg.chrome_profile.endswith("/.groundhog/chrome")


def test_the_browser_can_be_switched_to_the_stealth_image(monkeypatch):
    monkeypatch.setenv("GROUNDHOG_BROWSER", "Stealth")
    monkeypatch.setenv("GROUNDHOG_CHROME_PATH", "/opt/chrome")
    monkeypatch.setenv("GROUNDHOG_CHROME_PROFILE", "/srv/profile")
    cfg = load_config()
    assert (cfg.browser, cfg.chrome_path, cfg.chrome_profile) == (
        "stealth",
        "/opt/chrome",
        "/srv/profile",
    )


def test_an_unknown_browser_is_refused(monkeypatch):
    monkeypatch.setenv("GROUNDHOG_BROWSER", "firefox")
    with pytest.raises(ValueError, match="GROUNDHOG_BROWSER"):
        load_config()


@pytest.mark.parametrize(
    "variable,value",
    [
        ("GROUNDHOG_COMPOSE_FILE", "/srv/docker-compose.yml"),
        ("GROUNDHOG_BROWSER_IMAGE", "my/img:1"),
    ],
)
def test_a_container_setting_without_a_browser_choice_keeps_the_container(
    monkeypatch, variable, value
):
    monkeypatch.delenv("GROUNDHOG_BROWSER", raising=False)
    monkeypatch.setenv(variable, value)

    assert load_config().browser == "stealth"


def test_an_explicit_browser_choice_wins_over_a_container_setting(monkeypatch):
    monkeypatch.setenv("GROUNDHOG_BROWSER", "chrome")
    monkeypatch.setenv("GROUNDHOG_BROWSER_IMAGE", "my/img:1")

    assert load_config().browser == "chrome"


def test_a_home_relative_chrome_path_and_profile_are_expanded(monkeypatch):
    monkeypatch.setenv("HOME", "/home/ada")
    monkeypatch.setenv("GROUNDHOG_CHROME_PATH", "~/bin/chrome")
    monkeypatch.setenv("GROUNDHOG_CHROME_PROFILE", "~/gh-chrome")

    cfg = load_config()

    assert (cfg.chrome_path, cfg.chrome_profile) == ("/home/ada/bin/chrome", "/home/ada/gh-chrome")


def test_an_invalid_searxng_url_reports_only_its_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARXNG_URL", "ftp://user:secret@internal.example/path?token=private")
    with pytest.raises(ValueError) as caught:
        load_config()
    assert str(caught.value) == "SEARXNG_URL must be http(s), got scheme 'ftp'"


@pytest.mark.parametrize("value", ["", "  "])
def test_a_blank_private_address_switch_keeps_the_guard_on(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", value)

    assert load_config().block_private_ips is True


@pytest.mark.parametrize("value", ["enabled", "ture", "2"])
def test_a_private_address_switch_that_is_not_a_boolean_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", value)

    with pytest.raises(ValueError, match="GROUNDHOG_BLOCK_PRIVATE_IPS"):
        load_config()


@pytest.mark.parametrize("value", ["0", "-1"])
def test_a_page_limit_below_one_is_refused(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("GROUNDHOG_MAX_CONCURRENT_PAGES", value)

    with pytest.raises(ValueError, match="GROUNDHOG_MAX_CONCURRENT_PAGES"):
        load_config()


@pytest.mark.parametrize("value", ["", "  "])
def test_a_blank_number_takes_its_default(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    for name in (
        "GROUNDHOG_MAX_CONCURRENT_PAGES",
        "GROUNDHOG_MIN_DELAY_MS",
        "GROUNDHOG_MAX_TOKENS",
    ):
        monkeypatch.setenv(name, value)

    cfg = load_config()

    assert (cfg.max_concurrent_pages, cfg.min_delay_ms, cfg.max_tokens) == (4, 5000, 20000)
