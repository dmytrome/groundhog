import app
import pytest
from fastapi.testclient import TestClient

from groundhog_mcp import cdp, engine, safety


@pytest.fixture(autouse=True)
def _fresh_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    app._hits.clear()
    app._global_hits.clear()
    monkeypatch.setattr(app, "_RATE_LIMIT_PER_HOUR", 2)
    monkeypatch.setattr(app, "_GLOBAL_RATE_LIMIT_PER_MINUTE", 3)


def _fetching(monkeypatch: pytest.MonkeyPatch, outcome: BaseException | None = None) -> None:
    async def read_url(url: str) -> dict[str, str]:
        if outcome is not None:
            raise outcome
        return {"url": url}

    monkeypatch.setattr(app, "read_url", read_url)


def _post(client: TestClient) -> int:
    return client.post("/api/fetch", json={"url": "https://example.com/"}).status_code


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _fetching(monkeypatch)
    return TestClient(app.app, client=("203.0.113.1", 1234))


def test_a_visitor_over_their_own_limit_does_not_use_up_everyone_elses(
    client: TestClient,
) -> None:
    statuses = [_post(client) for _ in range(10)]
    other = TestClient(app.app, client=("203.0.113.2", 1234))

    assert statuses == [200, 200] + [429] * 8
    assert other.post("/api/fetch", json={"url": "https://example.com/"}).status_code == 200


def test_a_blocked_url_is_refused_as_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "true")
    monkeypatch.setenv("GROUNDHOG_AUTO_START_BROWSER", "false")
    monkeypatch.setattr(engine, "_provider", None)
    client = TestClient(app.app)

    response = client.post("/api/fetch", json={"url": "http://169.254.169.254/"})

    assert response.status_code == 400
    assert response.json()["detail"] == "blocked by SSRF policy"


def test_a_browser_outage_is_not_described_to_the_visitor(monkeypatch: pytest.MonkeyPatch) -> None:
    _fetching(monkeypatch, engine.BrowserUnavailableError("docker run chrome at 10.0.0.5"))
    client = TestClient(app.app)

    response = client.post("/api/fetch", json={"url": "https://example.com/"})

    assert response.status_code == 502
    assert "10.0.0.5" not in response.text


def test_a_visitor_who_left_an_hour_ago_is_forgotten(monkeypatch: pytest.MonkeyPatch) -> None:
    _fetching(monkeypatch)
    now = [1000.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: now[0])
    TestClient(app.app, client=("203.0.113.9", 1)).post(
        "/api/fetch", json={"url": "https://example.com/"}
    )
    now[0] += app._RATE_WINDOW_S + 1

    TestClient(app.app, client=("203.0.113.10", 1)).post(
        "/api/fetch", json={"url": "https://example.com/"}
    )

    assert set(app._hits) == {"203.0.113.10"}


def test_a_failed_fetch_is_not_blamed_on_the_visitor(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = safety.CallerFacingError("CDPError: net::ERR_NAME_NOT_RESOLVED at 10.0.0.5")
    failure.__cause__ = cdp.CDPError("net::ERR_NAME_NOT_RESOLVED at 10.0.0.5")
    _fetching(monkeypatch, failure)
    client = TestClient(app.app)

    response = client.post("/api/fetch", json={"url": "https://example.com/"})

    assert response.status_code == 502
    assert "10.0.0.5" not in response.text


def test_a_visitor_turned_away_when_busy_does_not_break_the_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app, "_GLOBAL_RATE_LIMIT_PER_MINUTE", 1)

    assert app._admit("203.0.113.1") is None
    assert app._admit("203.0.113.2") is not None
    assert app._admit("203.0.113.3") is not None
    assert set(app._hits) == {"203.0.113.1"}


@pytest.mark.parametrize("url", ["http://[::1", "ftp://example.com/", "example.com"])
def test_a_url_that_is_not_one_is_refused_before_fetching(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    _fetching(monkeypatch, AssertionError("fetched"))
    client = TestClient(app.app)

    response = client.post("/api/fetch", json={"url": url})

    assert response.status_code == 400
    assert not app._hits
