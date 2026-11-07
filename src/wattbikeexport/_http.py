from __future__ import annotations

import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests

_TRANSIENT_STATUS_CODES = {
    429,  # rate limited
    500,  # server error
    502,  # gateway error
    503,  # unavailable
    504,  # gateway timeout
}


def _retry_delay(response: requests.Response, attempt: int) -> int:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None and retry_after.isdigit():
        return int(retry_after)
    return 1 << attempt


class CsrfParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.token: str | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "input":
            return
        attributes = dict(attrs)
        if attributes.get("name") == "_csrf":
            self.token = attributes.get("value")


class HttpClient:
    def __init__(self) -> None:
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "wattbike-export/1"

    def request(
        self,
        method: str,
        url: str,
        *,
        attempts: int = 4,
        **kwargs: Any,
    ) -> requests.Response:
        for attempt in range(attempts):
            try:
                response = self.http.request(method, url, **kwargs)
            except requests.ConnectionError, requests.Timeout:
                if attempt == attempts - 1:
                    raise
                time.sleep(2**attempt)
                continue

            if response.status_code not in _TRANSIENT_STATUS_CODES:
                return response
            if attempt == attempts - 1:
                return response
            time.sleep(_retry_delay(response, attempt))
        raise AssertionError

    def download_file(self, *, url: str, destination: Path, headers: dict[str, str]) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.name}.part")

        if destination.exists():
            return

        offset = temporary.stat().st_size if temporary.exists() else 0
        headers = dict(headers)
        headers["Accept-Encoding"] = "identity"
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"

        # Use a short connect timeout but allow two minutes between file chunks.
        response = self.request(
            "GET",
            url,
            headers=headers,
            stream=True,
            timeout=(30, 120),
        )
        # 416 means the saved partial file already reaches the server's EOF.
        if response.status_code == 416:
            assert offset > 0, offset
            expected_size = int(response.headers["Content-Range"].rsplit("/", 1)[1])
            assert temporary.stat().st_size == expected_size
        else:
            response.raise_for_status()
            append = offset > 0 and response.status_code == 206
            if append:
                assert response.headers["Content-Range"].startswith(f"bytes {offset}-"), response.headers[
                    "Content-Range"
                ]
            with temporary.open("ab" if append else "wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if len(chunk) > 0:
                        output.write(chunk)

        temporary.replace(destination)
