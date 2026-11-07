from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from wattbikeexport._http import CsrfParser, HttpClient


def test_csrf_parser() -> None:
    parser = CsrfParser()
    parser.feed('<input type="hidden" name="_csrf" value="token">')
    assert parser.token == "token"


@pytest.mark.parametrize("status", [200, 206, 416])
def test_download_resumes_or_restarts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    client = HttpClient()
    path = tmp_path / "ride.wbs"
    partial = tmp_path / "ride.wbs.part"
    partial.write_bytes(b"abc")
    response = Mock(
        status_code=status,
        headers={"Content-Range": "bytes 3-5/6" if status == 206 else "bytes */3"},
        iter_content=Mock(return_value=iter([b"def" if status == 206 else b"abcdef"])),
    )
    monkeypatch.setattr(client, "request", Mock(return_value=response))
    client.download_file(url="https://storage.example/file", destination=path, headers={})
    assert path.read_bytes() == (b"abc" if status == 416 else b"abcdef")
    assert not partial.exists()


def test_missing_asset_raises_http_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = HttpClient()
    response = requests.Response()
    response.status_code = 404
    response.url = "https://storage.example/missing.wbs"
    monkeypatch.setattr(client, "request", Mock(return_value=response))
    path = tmp_path / "ride.wbs"
    partial = tmp_path / "ride.wbs.part"
    partial.write_bytes(b"abc")

    with pytest.raises(requests.HTTPError) as error:
        client.download_file(url=response.url, destination=path, headers={})

    assert error.value.response is response
    assert not path.exists()
    assert partial.read_bytes() == b"abc"
