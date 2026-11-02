"""
2captcha integration for solving reCAPTCHA v3 challenges on website sources.

Not used for Facebook/Instagram (Graph API or Playwright handles those).
"""

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TWOCAPTCHA_BASE = "https://2captcha.com"
POLL_INTERVAL = 5  # seconds between status polls
MAX_POLL_ATTEMPTS = 24  # 2 minutes max


class CaptchaSolver:
    """Solves reCAPTCHA v3 via 2captcha API."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or os.environ.get("TWOCAPTCHA_API_KEY", "")

    def solve_recaptcha_v3(
        self,
        site_key: str,
        page_url: str,
        action: str = "verify",
        min_score: float = 0.3,
    ) -> Optional[str]:
        """
        Submit a reCAPTCHA v3 task and poll for the token.
        Returns the g-recaptcha-response token, or None on failure.
        """
        if not self.api_key:
            logger.warning("TWOCAPTCHA_API_KEY not set — skipping captcha solve")
            return None

        task_id = self._submit_task(site_key, page_url, action, min_score)
        if not task_id:
            return None

        return self._poll_result(task_id)

    def _submit_task(
        self,
        site_key: str,
        page_url: str,
        action: str,
        min_score: float,
    ) -> Optional[str]:
        try:
            resp = requests.post(
                f"{TWOCAPTCHA_BASE}/in.php",
                data={
                    "key": self.api_key,
                    "method": "userrecaptcha",
                    "version": "v3",
                    "googlekey": site_key,
                    "pageurl": page_url,
                    "action": action,
                    "min_score": min_score,
                    "json": 1,
                },
                timeout=15,
            )
            data = resp.json()
            if data.get("status") == 1:
                return str(data["request"])
            logger.error("2captcha submit failed: %s", data.get("request"))
            return None
        except Exception as exc:
            logger.error("2captcha submit error: %s", exc)
            return None

    def _poll_result(self, task_id: str) -> Optional[str]:
        for _ in range(MAX_POLL_ATTEMPTS):
            time.sleep(POLL_INTERVAL)
            try:
                resp = requests.get(
                    f"{TWOCAPTCHA_BASE}/res.php",
                    params={"key": self.api_key, "action": "get", "id": task_id, "json": 1},
                    timeout=10,
                )
                data = resp.json()
                if data.get("status") == 1:
                    return str(data["request"])
                if data.get("request") != "CAPCHA_NOT_READY":
                    logger.error("2captcha poll error: %s", data.get("request"))
                    return None
            except Exception as exc:
                logger.error("2captcha poll exception: %s", exc)
                return None
        logger.error("2captcha timed out after %d attempts", MAX_POLL_ATTEMPTS)
        return None
