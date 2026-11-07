from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from hpikit.json import Json
from kloggy import get_logger

from ._http import CsrfParser, HttpClient
from .common import session_metadata, workout_directory_name, workout_files

logger = get_logger(__name__)

_API_URL = "https://v2.api.wattbike.com/v1"
_AUTH_URL = "https://auth.wattbike.com"

# These identifiers are embedded in Wattbike Hub's browser code.
# They select the public OAuth client and are not account secrets.
_AUTH_CLIENT_ID = "1dbc8ki8qvla56e899t03bjl9p"
_AUTH_REDIRECT_URI = "https://v2.hub.wattbike.com/auth-callback"


class WattbikeClient(HttpClient):
    def __init__(self, *, email: str, password: str) -> None:
        super().__init__()
        self.email = email
        self.password = password
        self.headers = {"Content-Type": "application/json", "X-App-Version": "5.1.0+5917058"}

    def authenticate(self) -> None:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        login_page = self.request(
            "GET",
            f"{_AUTH_URL}/oauth2/authorize",
            params={
                "response_type": "code",
                "client_id": _AUTH_CLIENT_ID,
                "redirect_uri": _AUTH_REDIRECT_URI,
                "scope": "openid",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "audience": "https://interactivestrength-cloud",
            },
            timeout=30,
        )
        login_page.raise_for_status()
        assert urlsplit(login_page.url).scheme == "https"
        assert urlsplit(login_page.url).netloc == "auth.wattbike.com"
        parser = CsrfParser()
        parser.feed(login_page.text)
        assert parser.token is not None
        login = self.request(
            "POST",
            login_page.url,
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
        assert login.is_redirect, login.status_code
        location = urlsplit(login.headers["Location"])
        assert location._replace(query="", fragment="").geturl() == _AUTH_REDIRECT_URI
        query = parse_qs(location.query)
        assert query["state"] == [state]
        token = self.request(
            "POST",
            f"{_AUTH_URL}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": _AUTH_CLIENT_ID,
                "code": query["code"][0],
                "redirect_uri": _AUTH_REDIRECT_URI,
                "code_verifier": verifier,
            },
            timeout=30,
        )
        token.raise_for_status()
        self.headers["Authorization"] = f"Bearer {token.json()['access_token']}"

    def get(self, endpoint: str, *, params: dict[str, Any] | None = None) -> Json:
        response = self.request(
            "GET",
            f"{_API_URL}/{endpoint}",
            headers=self.headers,
            params={"flatDescriptions": 1} | ({} if params is None else params),
            timeout=60,
        )
        response.raise_for_status()
        value = response.json()
        assert isinstance(value, dict), type(value)
        return value

    def list_workouts(self, *, page_size: int = 100) -> list[Json]:
        assert page_size > 0, page_size
        workouts = []
        page = 1
        while True:
            result = self.get(
                "workouts/me",
                params={
                    "join": ["class", "class.hotspots", "workoutWb", "device"],
                    "sort": ["startDate,ASC", "id,ASC"],
                    "page": page,
                    "limit": page_size,
                },
            )
            assert result["page"] == page, result["page"]
            workouts.extend(result["data"])
            if page >= result["pageCount"]:
                break
            assert len(result["data"]) > 0
            page += 1
        ids = [workout["id"] for workout in workouts]
        assert len(ids) == len(set(ids)) == result["total"], (len(ids), result["total"])
        return workouts

    def download(self, *, asset_id: str, destination: Path) -> None:
        if destination.exists():
            return
        result = self.get(f"assets/{quote(asset_id, safe='')}/download")
        url = result["url"]
        assert urlsplit(url).scheme == "https"
        # The signed storage URL authenticates the download itself.
        # Keep the API bearer token out of storage requests.
        self.download_file(url=url, destination=destination, headers={})


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    temporary.replace(path)


def export_account(
    *,
    client: WattbikeClient,
    output_directory: Path,
    after: str | None = None,
    before: str | None = None,
) -> None:
    client.authenticate()
    profile = client.get("users/me", params={"join": "profileImageAsset"})
    profile_path = output_directory / "profile.json"
    legacy_profile_bytes = None
    if profile_path.exists():
        previous_profile_bytes = profile_path.read_bytes()
        previous_profile = json.loads(previous_profile_bytes)
        is_v2_api = "objectId" not in previous_profile
        if is_v2_api:
            assert profile["user"]["id"] == previous_profile["user"]["id"], "Archive belongs to a different account"
        else:
            assert profile["user"]["wbParseId"] == previous_profile["objectId"], (
                "Archive belongs to a different account"
            )
            legacy_profile_bytes = previous_profile_bytes

    legacy_ids = set()
    current_ids = set()
    for _directory, session in session_metadata(output_directory):
        is_v2_api = "objectId" not in session
        if is_v2_api:
            current_ids.add(session["id"])
        else:
            legacy_ids.add(session["objectId"])
    workouts = client.list_workouts()
    lower = None if after is None else datetime.fromisoformat(after)
    upper = None if before is None else datetime.fromisoformat(before)
    workouts = [
        workout
        for workout in workouts
        if (lower is None or datetime.fromisoformat(workout["startDate"]) >= lower)
        and (upper is None or datetime.fromisoformat(workout["startDate"]) < upper)
    ]
    logger.info(f"Found {len(workouts)} workouts")
    for index, workout in enumerate(workouts, start=1):
        if workout["id"] in current_ids or workout["legacyId"] in legacy_ids:
            logger.info(f"[{index}/{len(workouts)}] {workout['startDate']} (already archived)")
            continue
        if workout["status"] != "complete":
            logger.info(f"[{index}/{len(workouts)}] {workout['startDate']} (not complete yet)")
            continue
        directory = output_directory / "sessions" / workout_directory_name(workout)
        assert not directory.exists(), directory
        staging = output_directory / ".partial" / directory.name
        staging.mkdir(parents=True, exist_ok=True)
        files = workout_files(workout)
        assert any(extension == "wbs" for extension, _asset_id, _name in files), workout["id"]
        logger.info(f"[{index}/{len(workouts)}] {workout['startDate']} ({len(files)} files)")
        for _extension, asset_id, name in files:
            client.download(asset_id=asset_id, destination=staging / name)
        # Publish the complete directory once, leaving archived sessions untouched on later runs.
        _write_json(staging / "metadata.json", workout)
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(directory)

    output_directory.mkdir(parents=True, exist_ok=True)
    if legacy_profile_bytes is not None:
        legacy_profile_path = output_directory / "legacy" / "profile.json"
        if not legacy_profile_path.exists():
            legacy_profile_path.parent.mkdir(exist_ok=True)
            temporary = legacy_profile_path.with_name(f"{legacy_profile_path.name}.tmp")
            temporary.write_bytes(legacy_profile_bytes)
            temporary.replace(legacy_profile_path)
    _write_json(profile_path, profile)
    # Keep the latest API listing separately from the immutable session metadata.
    _write_json(output_directory / "workouts.json", workouts)
    logger.info(f"Archive written to {output_directory.resolve()}")


def _parse_datetime(value: str) -> str:
    """
    Normalize an ISO date or datetime to UTC.

    Date-only and timezone-naive values are interpreted as UTC.
    Timezone-aware values are converted to UTC.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
    credentials = json.loads(args.secrets.read_text())
    assert set(credentials) >= {"email", "password"}, sorted(credentials)
    assert not (args.after is not None and args.before is not None and args.after >= args.before)
    export_account(
        client=WattbikeClient(email=credentials["email"], password=credentials["password"]),
        output_directory=args.output,
        after=args.after,
        before=args.before,
    )


if __name__ == "__main__":
    main()
