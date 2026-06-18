#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "requests>=2.32,<3",
# ]
# ///

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import requests


API_URL = "https://api.wattbike.com/v2"
AUTH_URL = "https://auth.wattbike.com/login"
AUTH_CLIENT_ID = "2o35ocqkd3i7al5umvie51dc99"
AUTH_REDIRECT_URI = "https://hub.wattbike.com/cauth/callback"
APPLICATION_ID = "Gopo4QrWEmTWefKMXjlT6GAN4JqafpvD"
JAVASCRIPT_KEY = "p1$h@M10Tkzw#"
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class CsrfParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attributes = dict(attrs)
        if attributes.get("name") == "_csrf":
            self.token = attributes.get("value")


def parse_datetime(value):
    if len(value) == 10:
        parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def date_pointer(value):
    return {"__type": "Date", "iso": value}


def build_session_where(*, user_id, after=None, before=None):
    where = {
        "user": {
            "__type": "Pointer",
            "className": "_User",
            "objectId": user_id,
        }
    }
    bounds = {}
    if after:
        bounds["$gte"] = date_pointer(after)
    if before:
        bounds["$lt"] = date_pointer(before)
    if bounds:
        where["startDate"] = bounds
    return where


def iter_named_files(value, location="sessionData"):
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str):
            yield location, name
        for key, child in value.items():
            if key != "name":
                yield from iter_named_files(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_named_files(child, f"{location}[{index}]")


def session_files(session):
    seen = set()
    files = []
    for field, name in iter_named_files(session.get("sessionData", {})):
        if name not in seen:
            files.append((field, name))
            seen.add(name)
    return files


def build_revolution_export(wbs):
    rows = [
        row
        for lap in wbs.get("laps", [])
        for row in lap.get("data", [])
    ]
    include_polar = any(isinstance(row.get("polar"), dict) for row in rows)
    output = []

    for row in rows:
        revolution = {}
        for key in (
            "time",
            "heartrate",
            "balance",
            "force",
            "power",
            "speed",
            "distance",
            "cadence",
        ):
            if key in row:
                revolution[key] = row[key]

        pes = row.get("pes")
        if isinstance(pes, dict):
            revolution["pesCombinedCoefficient"] = pes["combinedCoefficient"]
            revolution["pesRightCoefficient"] = pes["rightCoefficient"]
            revolution["pesLeftCoefficient"] = pes["leftCoefficient"]
        else:
            revolution["pesCombinedCoefficient"] = "na"
            revolution["pesRightCoefficient"] = "na"
            revolution["pesLeftCoefficient"] = "na"

        if include_polar:
            polar = row.get("polar")
            if isinstance(polar, dict):
                revolution["polarForces"] = polar["force"]
                revolution["polarLeftCount"] = polar["lcnt"]
                revolution["polarTotalCount"] = polar["cnt"]
            else:
                revolution["polarForces"] = "na"
                revolution["polarLeftCount"] = "na"
                revolution["polarTotalCount"] = "na"

        output.append(revolution)

    return output


def safe_file_name(name):
    assert name not in {"", ".", ".."}, name
    assert "/" not in name and "\\" not in name, name
    return name


def session_directory_name(session):
    start_date = session["startDate"]["iso"]
    timestamp = (
        start_date.replace("-", "")
        .replace(":", "")
        .replace(".000", "")
        .replace("Z", "Z")
    )
    return f"{timestamp}_{session['objectId']}"


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, indent=2, ensure_ascii=False)
        output.write("\n")
    temporary.replace(path)


def retry_delay(response, attempt):
    retry_after = response.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return int(retry_after)
    return 2**attempt


class WattbikeClient:
    def __init__(self, *, email, password):
        self.email = email
        self.password = password
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "wattbike-export/1"
        self.parse_headers = {
            "X-Parse-Application-Id": APPLICATION_ID,
            "X-Parse-Javascript-Key": JAVASCRIPT_KEY,
        }
        self.user = None

    def request(self, method, url, *, attempts=4, **kwargs):
        for attempt in range(attempts):
            try:
                response = self.http.request(method, url, **kwargs)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == attempts - 1:
                    raise
                time.sleep(2**attempt)
                continue

            if response.status_code not in TRANSIENT_STATUS_CODES:
                return response
            if attempt == attempts - 1:
                return response
            time.sleep(retry_delay(response, attempt))
        assert False

    def authenticate(self):
        params = {
            "client_id": AUTH_CLIENT_ID,
            "response_type": "token",
            "scope": "email openid",
            "redirect_uri": AUTH_REDIRECT_URI,
        }
        login_page = self.request("GET", AUTH_URL, params=params, timeout=30)
        login_page.raise_for_status()

        parser = CsrfParser()
        parser.feed(login_page.text)
        assert parser.token

        login_response = self.request(
            "POST",
            AUTH_URL,
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
            f"{API_URL}/custom/session",
            headers=self.parse_headers | {"Authorization": f"Bearer {id_token}"},
            timeout=30,
        )
        exchange_response.raise_for_status()
        session_token = exchange_response.json()["token"]
        self.parse_headers["X-Parse-Session-Token"] = session_token

        profile_response = self.request(
            "GET",
            f"{API_URL}/users/me",
            headers=self.parse_headers,
            params={"include": "performanceState,preferences,statistics"},
            timeout=30,
        )
        profile_response.raise_for_status()
        self.user = profile_response.json()
        return self.user

    def list_sessions(self, *, after=None, before=None, page_size=100):
        assert self.user
        assert 1 <= page_size <= 1000, page_size
        where = build_session_where(
            user_id=self.user["objectId"],
            after=after,
            before=before,
        )
        sessions = []
        skip = 0

        while True:
            response = self.request(
                "GET",
                f"{API_URL}/classes/RideSession",
                headers=self.parse_headers,
                params={
                    "where": json.dumps(where, separators=(",", ":")),
                    "order": "startDate,objectId",
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

    def get_object(self, pointer):
        assert pointer["__type"] == "Pointer", pointer
        class_name = pointer["className"]
        object_id = pointer["objectId"]
        assert re.fullmatch(r"[A-Za-z0-9_]+", class_name), class_name
        assert re.fullmatch(r"[A-Za-z0-9_-]+", object_id), object_id
        response = self.request(
            "GET",
            f"{API_URL}/classes/{class_name}/{object_id}",
            headers=self.parse_headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_profile_objects(self):
        assert self.user
        objects = {}
        for field in ("performanceState", "preferences", "statistics"):
            pointer = self.user.get(field)
            if isinstance(pointer, dict) and pointer.get("__type") == "Pointer":
                objects[field] = self.get_object(pointer)
        return objects

    def download(self, *, source_name, destination):
        source_name = safe_file_name(source_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.name}.part")

        if destination.exists():
            return {
                "size": destination.stat().st_size,
                "sha256": sha256_file(destination),
                "reused": True,
            }

        offset = temporary.stat().st_size if temporary.exists() else 0
        headers = dict(self.parse_headers)
        if offset:
            headers["Range"] = f"bytes={offset}-"

        response = self.request(
            "GET",
            f"{API_URL}/files/{quote(source_name, safe='')}",
            headers=headers,
            stream=True,
            timeout=(30, 120),
        )

        if response.status_code == 416:
            expected_size = int(response.headers["Content-Range"].rsplit("/", 1)[1])
            assert temporary.stat().st_size == expected_size
        else:
            response.raise_for_status()
            append = offset > 0 and response.status_code == 206
            with temporary.open("ab" if append else "wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)

        temporary.replace(destination)
        return {
            "size": destination.stat().st_size,
            "sha256": sha256_file(destination),
            "reused": False,
        }


def export_account(
    *,
    client,
    output_directory,
    after=None,
    before=None,
    page_size=100,
    metadata_only=False,
):
    profile = client.authenticate()
    profile_objects = client.get_profile_objects()
    sessions = client.list_sessions(
        after=after,
        before=before,
        page_size=page_size,
    )

    output_directory.mkdir(parents=True, exist_ok=True)
    write_json(output_directory / "profile.json", profile)
    write_json(output_directory / "profile-objects.json", profile_objects)
    write_json(output_directory / "sessions.json", sessions)

    manifest = {
        "format_version": 1,
        "generated_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "user_id": profile["objectId"],
        "filters": {"after": after, "before": before},
        "session_count": len(sessions),
        "metadata_only": metadata_only,
        "files": [],
    }

    print(f"Found {len(sessions)} sessions")
    for index, session in enumerate(sessions, start=1):
        session_directory = (
            output_directory / "sessions" / session_directory_name(session)
        )
        write_json(session_directory / "metadata.json", session)
        files = session_files(session)
        print(
            f"[{index}/{len(sessions)}] {session['startDate']['iso']} "
            f"{session.get('title', 'Untitled')} ({len(files)} files)"
        )

        if metadata_only:
            continue

        for field, source_name in files:
            destination = session_directory / safe_file_name(source_name)
            details = client.download(
                source_name=source_name,
                destination=destination,
            )
            manifest["files"].append(
                {
                    "kind": "source",
                    "session_id": session["objectId"],
                    "session_field": field,
                    "source_name": source_name,
                    "path": destination.relative_to(output_directory).as_posix(),
                    **details,
                }
            )

        wbs_files = [
            source_name
            for field, source_name in files
            if field == "sessionData.wbs"
        ]
        assert len(wbs_files) <= 1, wbs_files
        if wbs_files:
            wbs_path = session_directory / safe_file_name(wbs_files[0])
            wbs = json.loads(wbs_path.read_text(encoding="utf-8"))
            revolutions_path = session_directory / "revolutions.json"
            write_json(
                revolutions_path,
                build_revolution_export(wbs),
            )
            manifest["files"].append(
                {
                    "kind": "derived",
                    "session_id": session["objectId"],
                    "session_field": "derived.revolutions",
                    "source_name": wbs_files[0],
                    "path": revolutions_path.relative_to(
                        output_directory
                    ).as_posix(),
                    "size": revolutions_path.stat().st_size,
                    "sha256": sha256_file(revolutions_path),
                    "reused": False,
                }
            )
        write_json(output_directory / "manifest.json", manifest)

    write_json(output_directory / "manifest.json", manifest)
    return manifest


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export Wattbike Hub profile, session metadata, and ride files."
    )
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path("secrets.json"),
        help="JSON file containing email and password (default: secrets.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("wattbike-export"),
        help="Archive directory (default: wattbike-export)",
    )
    parser.add_argument(
        "--after",
        type=parse_datetime,
        help="Include sessions at or after this date/time (ISO 8601)",
    )
    parser.add_argument(
        "--before",
        type=parse_datetime,
        help="Include sessions before this date/time (ISO 8601)",
    )
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Export profile and session metadata without ride files",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    secrets = json.loads(args.secrets.read_text(encoding="utf-8"))
    assert set(secrets) >= {"email", "password"}, sorted(secrets)
    assert not (args.after and args.before and args.after >= args.before)

    client = WattbikeClient(
        email=secrets["email"],
        password=secrets["password"],
    )
    export_account(
        client=client,
        output_directory=args.output,
        after=args.after,
        before=args.before,
        page_size=args.page_size,
        metadata_only=args.metadata_only,
    )
    print(f"Archive written to {args.output.resolve()}")


if __name__ == "__main__":
    main()
