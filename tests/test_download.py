"""Audio download: streaming to disk, size cap, error classification, cleanup."""

import os

import httpx
import pytest

from earshot import download
from earshot.download import DownloadError, download_audio

URL = "https://example.com/ep.mp3"


def fake_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_streams_the_body_to_a_file(tmp_path):
    client = fake_client(lambda r: httpx.Response(200, content=b"ID3-audio-bytes"))
    path = download_audio(URL, str(tmp_path), client=client)
    with open(path, "rb") as f:
        assert f.read() == b"ID3-audio-bytes"


@pytest.mark.parametrize("status, retryable", [(404, False), (410, False), (403, False),
                                               (429, True), (500, True), (503, True)])
def test_http_errors_are_classified(tmp_path, status, retryable):
    client = fake_client(lambda r: httpx.Response(status))
    with pytest.raises(DownloadError) as exc:
        download_audio(URL, str(tmp_path), client=client)
    assert exc.value.retryable is retryable


def test_oversized_download_is_stopped_and_partial_file_removed(tmp_path):
    client = fake_client(lambda r: httpx.Response(200, content=b"x" * 5000))
    with pytest.raises(DownloadError, match="cap") as exc:
        download_audio(URL, str(tmp_path), max_bytes=1000, client=client)
    assert exc.value.retryable is False
    assert os.listdir(tmp_path) == []


def test_network_error_is_retryable_and_leaves_no_file(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(DownloadError) as exc:
        download_audio(URL, str(tmp_path), client=fake_client(handler))
    assert exc.value.retryable is True
    assert os.listdir(tmp_path) == []


def test_default_client_has_a_timeout(monkeypatch, tmp_path):
    captured = {}
    real_client = httpx.Client

    def spy(**kwargs):
        captured.update(kwargs)
        return real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"a")))

    monkeypatch.setattr(download.httpx, "Client", spy)
    download_audio(URL, str(tmp_path))
    assert captured["timeout"] == download.DOWNLOAD_TIMEOUT
    assert captured["follow_redirects"] is True
