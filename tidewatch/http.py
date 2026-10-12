"""JSON fetcher with retries, raw-response archiving and offline replay.

Live mode saves every response to raw_dir/<name>.json. Replay mode reads those
files back instead of calling the network, so a past day can be re-rendered
exactly (handy after a style change, or to debug a bad chart).
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests

USER_AGENT = "tidewatch-bot/0.3 (+https://x.com/tidewatchxyz)"


class FetchError(RuntimeError):
    pass


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


class Fetcher:
    def __init__(self, raw_dir: Path | None = None, *, replay_dir: Path | None = None,
                 retries: int = 3, timeout: float = 30):
        self.raw_dir = raw_dir
        self.replay_dir = replay_dir
        self.retries = retries
        self.timeout = timeout
        self._local = threading.local()  # one Session per thread

    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers["User-Agent"] = USER_AGENT
            self._local.session = s
        return s

    def get_json(self, url: str, params: dict[str, Any] | None = None, *, name: str,
                 trim: Callable[[Any], Any] | None = None) -> Any:
        """Fetch and archive JSON. `trim` (for responses that are mostly history no
        chart reads) is applied before both the archive and the return value, so a
        replay sees exactly what the live run saw."""
        if self.replay_dir is not None:
            path = self.replay_dir / f"{_safe(name)}.json"
            if not path.exists():
                raise FetchError(f"{name}: no saved response at {path}")
            return json.loads(path.read_text())

        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise FetchError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                data = resp.json()
                if trim is not None:
                    data = trim(data)
                self._archive(name, data)
                return data
            except (requests.RequestException, ValueError, FetchError) as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
        raise FetchError(f"{name}: could not fetch {url} ({last})")

    def _archive(self, name: str, data: Any) -> None:
        if self.raw_dir is None:
            return
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        (self.raw_dir / f"{_safe(name)}.json").write_text(json.dumps(data))
