"""
Client for the Juniper Apstra REST API.
"""

import ipaddress
import requests
import urllib3
from typing import Any, Optional

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class BaseMixin:
    """HTTP client for the Apstra API."""

    _SPEED_MAP = {
        "1g": "1G",   "1000m": "1G",
        "10g": "10G", "10000m": "10G",
        "25g": "25G",
        "40g": "40G",
        "100g": "100G",
        "400g": "400G",
    }

    @staticmethod
    def _slim(items: list[dict], *keys: str) -> list[dict]:
        """Filter each dict of a list to keep only the specified keys."""
        return [{k: item[k] for k in keys if k in item} for item in items]

    def __init__(self, host: str, username: str, password: str, verify_ssl: bool = False):
        self.base_url   = f"https://{host}/api"
        self.username   = username
        self.password   = password
        self.verify_ssl = verify_ssl
        self.session    = requests.Session()
        self.token: Optional[str] = None

    # ── Auth ─────────────────────────────────────────────────────────────

    def login(self) -> None:
        resp = self.session.post(
            f"{self.base_url}/user/login",
            json={"username": self.username, "password": self.password},
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        self.token = resp.json()["token"]
        self.session.headers.update(
            {"AuthToken": self.token, "Content-Type": "application/json"}
        )

    def logout(self) -> None:
        if self.token:
            self.session.post(f"{self.base_url}/user/logout", verify=self.verify_ssl)
            self.token = None

    def _ensure_logged_in(self) -> None:
        if not self.token:
            self.login()

    def _raise_for_status(self, r: requests.Response) -> None:
        """Enriched raise_for_status: includes the response body in the error message."""
        if not r.ok:
            try:
                detail = r.json()
            except ValueError:
                detail = r.text
            raise requests.HTTPError(
                f"{r.status_code} {r.reason} — {detail}",
                response=r,
            )

    def _get(self, path: str, params: dict | None = None) -> Any:
        self._ensure_logged_in()
        r = self.session.get(f"{self.base_url}{path}", params=params, verify=self.verify_ssl)
        self._raise_for_status(r)
        return r.json()

    def _post(self, path: str, body: dict) -> Any:
        self._ensure_logged_in()
        r = self.session.post(f"{self.base_url}{path}", json=body, verify=self.verify_ssl)
        self._raise_for_status(r)
        return r.json()

    def _patch(self, path: str, body: dict) -> Any:
        self._ensure_logged_in()
        r = self.session.patch(f"{self.base_url}{path}", json=body, verify=self.verify_ssl)
        self._raise_for_status(r)
        return r.json()

    def _put(self, path: str, body: dict) -> Any:
        self._ensure_logged_in()
        r = self.session.put(f"{self.base_url}{path}", json=body, verify=self.verify_ssl)
        self._raise_for_status(r)
        return r.json()

    def _delete(self, path: str) -> None:
        self._ensure_logged_in()
        r = self.session.delete(f"{self.base_url}{path}", verify=self.verify_ssl)
        r.raise_for_status()

    @staticmethod
    def _extract_items(data: Any) -> list[dict]:
        """Normalize listed responses coming from different API variants."""
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return items
            rows = data.get("data")
            if isinstance(rows, list):
                return rows
            result = data.get("result")
            if isinstance(result, list):
                return result
            return []
        if isinstance(data, list):
            return data
        return []

