"""Unit tests for CaptchaSolver (2captcha) — no real HTTP calls."""

import time
from unittest.mock import MagicMock, patch

import pytest

from api.scrapers.captcha import CaptchaSolver


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _: None)


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------

def test_no_api_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
    solver = CaptchaSolver(api_key="")
    result = solver.solve_recaptcha_v3("site_key", "https://example.com")
    assert result is None


# ---------------------------------------------------------------------------
# Submit task
# ---------------------------------------------------------------------------

def test_submit_task_returns_task_id() -> None:
    solver = CaptchaSolver(api_key="test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": 1, "request": "12345"}
    with patch("requests.post", return_value=mock_resp):
        task_id = solver._submit_task("site_key", "https://example.com", "verify", 0.3)
    assert task_id == "12345"


def test_submit_task_failure_returns_none() -> None:
    solver = CaptchaSolver(api_key="test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": 0, "request": "ERROR_WRONG_USER_KEY"}
    with patch("requests.post", return_value=mock_resp):
        task_id = solver._submit_task("site_key", "https://example.com", "verify", 0.3)
    assert task_id is None


def test_submit_task_exception_returns_none() -> None:
    solver = CaptchaSolver(api_key="test_key")
    with patch("requests.post", side_effect=Exception("network error")):
        task_id = solver._submit_task("site_key", "https://example.com", "verify", 0.3)
    assert task_id is None


# ---------------------------------------------------------------------------
# Poll result
# ---------------------------------------------------------------------------

def test_poll_result_returns_token_on_ready() -> None:
    solver = CaptchaSolver(api_key="test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": 1, "request": "03AGdBq2...token"}
    with patch("requests.get", return_value=mock_resp):
        token = solver._poll_result("12345")
    assert token == "03AGdBq2...token"


def test_poll_result_not_ready_then_ready() -> None:
    solver = CaptchaSolver(api_key="test_key")
    not_ready = MagicMock()
    not_ready.json.return_value = {"status": 0, "request": "CAPCHA_NOT_READY"}
    ready = MagicMock()
    ready.json.return_value = {"status": 1, "request": "final_token"}
    with patch("requests.get", side_effect=[not_ready, ready]):
        token = solver._poll_result("12345")
    assert token == "final_token"


def test_poll_result_error_response_returns_none() -> None:
    solver = CaptchaSolver(api_key="test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": 0, "request": "ERROR_CAPTCHA_UNSOLVABLE"}
    with patch("requests.get", return_value=mock_resp):
        token = solver._poll_result("12345")
    assert token is None


def test_poll_result_exception_returns_none() -> None:
    solver = CaptchaSolver(api_key="test_key")
    with patch("requests.get", side_effect=Exception("timeout")):
        token = solver._poll_result("12345")
    assert token is None


# ---------------------------------------------------------------------------
# Full solve_recaptcha_v3 flow
# ---------------------------------------------------------------------------

def test_solve_full_flow_success() -> None:
    solver = CaptchaSolver(api_key="test_key")
    solver._submit_task = MagicMock(return_value="99999")
    solver._poll_result = MagicMock(return_value="recaptcha_token_xyz")
    token = solver.solve_recaptcha_v3("sk", "https://example.com")
    assert token == "recaptcha_token_xyz"
    solver._submit_task.assert_called_once()
    solver._poll_result.assert_called_once_with("99999")


def test_solve_submit_fails_skips_poll() -> None:
    solver = CaptchaSolver(api_key="test_key")
    solver._submit_task = MagicMock(return_value=None)
    solver._poll_result = MagicMock()
    result = solver.solve_recaptcha_v3("sk", "https://example.com")
    assert result is None
    solver._poll_result.assert_not_called()
