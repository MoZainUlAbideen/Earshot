"""Download episode audio to a temporary file for processing (never kept or served).

Streams to disk in pieces with a timeout and a size cap, so a slow or endless
response can neither hang the worker nor fill the disk.
"""

import os

import httpx

from earshot.ingest import USER_AGENT

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)  # per network operation, not the whole download
MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024  # ~7 h of 128 kbps MP3; far above any curated episode


class DownloadError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def download_audio(
    url: str,
    dest_dir: str,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    client: httpx.Client | None = None,
) -> str:
    """Stream `url` into a file in `dest_dir` and return its path.

    404/410 and other 4xx are permanent (retryable=False); 5xx, network errors
    and timeouts are retryable. A partial file is removed on any failure.
    """
    path = os.path.join(dest_dir, "episode.audio")
    http = client or httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)
    try:
        with http.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
            if response.status_code >= 400:
                raise DownloadError(
                    f"audio download returned {response.status_code}",
                    retryable=response.status_code >= 500 or response.status_code == 429,
                )
            written = 0
            with open(path, "wb") as f:
                for block in response.iter_bytes():
                    written += len(block)
                    if written > max_bytes:
                        raise DownloadError(
                            f"audio larger than {max_bytes // (1024 * 1024)} MB cap", retryable=False
                        )
                    f.write(block)
        return path
    except httpx.TransportError as e:
        _remove(path)
        raise DownloadError(f"network error downloading audio: {type(e).__name__}", retryable=True) from None
    except DownloadError:
        _remove(path)
        raise
    finally:
        if client is None:
            http.close()


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
