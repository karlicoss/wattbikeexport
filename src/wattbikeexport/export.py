from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import requests

from .common import (
    Json,
    JsonObject,
    safe_file_name,
    session_directory_name,
    session_files,
)

_API_URL = "https://api.wattbike.com/v2"
_AUTH_URL = "https://auth.wattbike.com/login"

# These identifiers are embedded in Wattbike Hub's browser code.
# They select the public OAuth and Parse clients and are not account secrets.
_AUTH_CLIENT_ID = "2o35ocqkd3i7al5umvie51dc99"
_AUTH_REDIRECT_URI = "https://hub.wattbike.com/cauth/callback"
_APPLICATION_ID = "Gopo4QrWEmTWefKMXjlT6GAN4JqafpvD"
_JAVASCRIPT_KEY = "p1$h@M10Tkzw#"

_TRANSIENT_STATUS_CODES = {
    429,  # rate limited
    500,  # server error
    502,  # gateway error
    503,  # unavailable
    504,  # gateway timeout
}


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


def _parse_datetime(value: str) -> str:
    """
    Normalize an ISO date or datetime to the UTC format expected by Parse.

    Date-only and timezone-naive values are interpreted as UTC.
    Timezone-aware values are converted to UTC.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _date_pointer(value: str) -> JsonObject:
    return {"__type": "Date", "iso": value}


def _assert_unchanged_metadata(*, path: Path, session: JsonObject) -> None:
    if not path.exists():
        return
    existing = json.loads(path.read_text())
    assert existing == session, (path, existing, session)


def _write_json(path: Path, value: Json) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False))


def _build_session_where(
    *,
    user_id: str,
    after: str | None = None,
    before: str | None = None,
) -> JsonObject:
    where: JsonObject = {
        "user": {
            "__type": "Pointer",
            "className": "_User",
            "objectId": user_id,
        }
    }
    bounds: JsonObject = {}
    if after is not None:
        bounds["$gte"] = _date_pointer(after)
    if before is not None:
        bounds["$lt"] = _date_pointer(before)
    if len(bounds) > 0:
        where["startDate"] = bounds
    return where


def _retry_delay(response: requests.Response, attempt: int) -> int:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None and retry_after.isdigit():
        return int(retry_after)
    return 1 << attempt


class WattbikeClient:
    def __init__(self, *, email: str, password: str) -> None:
        self.email = email
        self.password = password
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "wattbike-export/1"
        self.parse_headers = {
            "X-Parse-Application-Id": _APPLICATION_ID,
            "X-Parse-Javascript-Key": _JAVASCRIPT_KEY,
        }
        self.user: JsonObject | None = None

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

    def authenticate(self) -> None:
        params = {
            "client_id": _AUTH_CLIENT_ID,
            "response_type": "token",
            "scope": "email openid",
            "redirect_uri": _AUTH_REDIRECT_URI,
        }
        login_page = self.request("GET", _AUTH_URL, params=params, timeout=30)
        login_page.raise_for_status()

        parser = CsrfParser()
        parser.feed(login_page.text)
        assert parser.token is not None

        login_response = self.request(
            "POST",
            _AUTH_URL,
            params=params,
            data={
                "_csrf": parser.token,
                "username": self.email,
                "password": self.password,
                "cognitoAsfData": "",
                "signInSubmitButton": "Sign in",
            },
            allow_redirects=False,
            timeout=30,
        )
        assert login_response.is_redirect, login_response.status_code

        location = login_response.headers["Location"]
        fragment = parse_qs(urlsplit(location).fragment)
        id_token = fragment["id_token"][0]

        exchange_response = self.request(
            "GET",
            f"{_API_URL}/custom/session",
            headers=self.parse_headers | {"Authorization": f"Bearer {id_token}"},
            timeout=30,
        )
        exchange_response.raise_for_status()
        session_token = exchange_response.json()["token"]
        self.parse_headers["X-Parse-Session-Token"] = session_token

    def get_profile(self) -> JsonObject:
        profile_response = self.request(
            "GET",
            f"{_API_URL}/users/me",
            headers=self.parse_headers,
            timeout=30,
        )
        profile_response.raise_for_status()
        self.user = profile_response.json()
        self.user.pop("sessionToken", None)
        return self.user

    def list_sessions(
        self,
        *,
        after: str | None = None,
        before: str | None = None,
        page_size: int = 100,
    ) -> list[JsonObject]:
        assert self.user is not None
        assert page_size > 0, page_size
        where = _build_session_where(
            user_id=self.user["objectId"],
            after=after,
            before=before,
        )
        sessions = []
        skip = 0

        while True:
            response = self.request(
                "GET",
                f"{_API_URL}/classes/RideSession",
                headers=self.parse_headers,
                params={
                    "where": json.dumps(where, separators=(",", ":")),
                    "order": "startDate,objectId",
                    # Wattbike Hub includes these fields in its RideSession query.
                    # Parse expands the pointers, preserving their data in sessions.json.
                    "include": "sessionSummary,userPerformanceState,training",
                    "limit": page_size,
                    "skip": skip,
                },
                timeout=60,
            )
            response.raise_for_status()
            page = response.json()["results"]
            sessions.extend(page)
            if len(page) < page_size:
                break
            skip += len(page)

        object_ids = [session["objectId"] for session in sessions]
        assert len(object_ids) == len(set(object_ids)), object_ids
        return sessions

    def get_object(self, pointer: JsonObject) -> JsonObject:
        assert pointer["__type"] == "Pointer", pointer
        class_name = pointer["className"]
        object_id = pointer["objectId"]
        assert isinstance(class_name, str), class_name
        assert isinstance(object_id, str), object_id
        assert re.fullmatch(r"[A-Za-z0-9_]+", class_name), class_name
        assert re.fullmatch(r"[A-Za-z0-9_-]+", object_id), object_id
        response = self.request(
            "GET",
            f"{_API_URL}/classes/{class_name}/{object_id}",
            headers=self.parse_headers,
            timeout=30,
        )
        response.raise_for_status()
        value = response.json()
        assert isinstance(value, dict), value
        return value

    def get_profile_objects(self) -> dict[str, JsonObject]:
        assert self.user is not None
        objects = {}
        for field, pointer in self.user.items():
            if isinstance(pointer, dict) and pointer.get("__type") == "Pointer":
                objects[field] = self.get_object(pointer)
        return objects

    def download(
        self,
        *,
        source_name: str,
        destination: Path,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.name}.part")

        if destination.exists():
            return

        offset = temporary.stat().st_size if temporary.exists() else 0
        headers = dict(self.parse_headers)
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"

        # Use a short connect timeout but allow two minutes between file chunks.
        response = self.request(
            "GET",
            f"{_API_URL}/files/{quote(source_name, safe='')}",
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
            with temporary.open("ab" if append else "wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if len(chunk) > 0:
                        output.write(chunk)

        temporary.replace(destination)


def export_account(
    *,
    client: WattbikeClient,
    output_directory: Path,
    after: str | None = None,
    before: str | None = None,
) -> None:
    client.authenticate()
    profile = client.get_profile()
    profile_objects = client.get_profile_objects()
    sessions = client.list_sessions(
        after=after,
        before=before,
    )

    for session in sessions:
        session_directory = output_directory / "sessions" / session_directory_name(session)
        _assert_unchanged_metadata(
            path=session_directory / "metadata.json",
            session=session,
        )

    output_directory.mkdir(parents=True, exist_ok=True)
    _write_json(output_directory / "profile.json", profile)
    _write_json(output_directory / "profile-objects.json", profile_objects)
    _write_json(output_directory / "sessions.json", sessions)

    print(f"Found {len(sessions)} sessions", file=sys.stderr)
    for index, session in enumerate(sessions, start=1):
        session_directory = output_directory / "sessions" / session_directory_name(session)
        metadata_path = session_directory / "metadata.json"
        existing = metadata_path.exists()
        if not existing:
            session_directory.mkdir(parents=True, exist_ok=True)
            _write_json(metadata_path, session)
        files = session_files(session)
        status = "existing" if existing else "new"
        print(
            f"[{index}/{len(sessions)}] {session['startDate']['iso']} "
            f"{session.get('title', 'Untitled')} "
            f"({status}, {len(files)} files)",
            file=sys.stderr,
        )

        for _field, source_name in files:
            # Filenames come from Wattbike metadata and must stay in this session.
            destination = session_directory / safe_file_name(source_name)
            client.download(
                source_name=source_name,
                destination=destination,
            )


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export raw Wattbike Hub profile, session metadata, and ride files.")
    parser.add_argument(
        "--secrets",
        type=Path,
        required=True,
        help="JSON file containing email and password",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Archive directory",
    )
    parser.add_argument(
        "--after",
        type=_parse_datetime,
        help="Include sessions at or after this date/time (ISO 8601)",
    )
    parser.add_argument(
        "--before",
        type=_parse_datetime,
        help="Include sessions before this date/time (ISO 8601)",
    )
    return parser


def main() -> None:
    args = _make_parser().parse_args()
    secrets = json.loads(args.secrets.read_text())
    assert set(secrets) >= {"email", "password"}, sorted(secrets)
    assert not (args.after is not None and args.before is not None and args.after >= args.before)

    client = WattbikeClient(
        email=secrets["email"],
        password=secrets["password"],
    )
    export_account(
        client=client,
        output_directory=args.output,
        after=args.after,
        before=args.before,
    )
    print(f"Archive written to {args.output.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
