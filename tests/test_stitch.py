"""Tests for the /stitch endpoint.

Network and ffmpeg are both mocked: these tests verify the service's own
contract (validation, error mapping, command construction), not FFmpeg.
"""

import subprocess
from unittest.mock import patch

import pytest
import requests
from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)

VALID_PAYLOAD = {
    "image_urls": ["http://example.com/a.png", "http://example.com/b.png"],
    "audio_url": "http://example.com/track.mp3",
    "video_name": "my-clip",
}


class FakeResponse:
    def __init__(self, content=b"data", status=200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


@pytest.fixture
def temp_workdir(tmp_path, monkeypatch):
    """Run each test in its own directory so temp/ never leaks between tests."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_stitch_success_returns_output_path(temp_workdir):
    with patch.object(main.requests, "get", return_value=FakeResponse()), \
         patch.object(main.subprocess, "run") as run:
        resp = client.post("/stitch", json=VALID_PAYLOAD)

    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "video": "temp/my-clip.mp4"}
    run.assert_called_once()


def test_stitch_downloads_every_image_and_the_audio(temp_workdir):
    with patch.object(main.requests, "get", return_value=FakeResponse()) as get, \
         patch.object(main.subprocess, "run"):
        client.post("/stitch", json=VALID_PAYLOAD)

    # two images + one audio track
    assert get.call_count == 3
    assert get.call_args_list[-1].args[0] == VALID_PAYLOAD["audio_url"]


def test_ffmpeg_receives_the_expected_output_path(temp_workdir):
    with patch.object(main.requests, "get", return_value=FakeResponse()), \
         patch.object(main.subprocess, "run") as run:
        client.post("/stitch", json=VALID_PAYLOAD)

    cmd = run.call_args.args[0]
    assert cmd[0] == "ffmpeg"
    assert cmd[-1] == "temp/my-clip.mp4"
    assert run.call_args.kwargs["check"] is True


def test_failed_download_returns_502(temp_workdir):
    with patch.object(main.requests, "get", return_value=FakeResponse(status=404)), \
         patch.object(main.subprocess, "run"):
        resp = client.post("/stitch", json=VALID_PAYLOAD)

    assert resp.status_code == 502
    assert "Could not fetch" in resp.json()["detail"]


def test_ffmpeg_failure_returns_500(temp_workdir):
    err = subprocess.CalledProcessError(1, "ffmpeg", stderr=b"invalid codec")
    with patch.object(main.requests, "get", return_value=FakeResponse()), \
         patch.object(main.subprocess, "run", side_effect=err):
        resp = client.post("/stitch", json=VALID_PAYLOAD)

    assert resp.status_code == 500
    assert "invalid codec" in resp.json()["detail"]


def test_missing_ffmpeg_binary_returns_500(temp_workdir):
    with patch.object(main.requests, "get", return_value=FakeResponse()), \
         patch.object(main.subprocess, "run", side_effect=FileNotFoundError):
        resp = client.post("/stitch", json=VALID_PAYLOAD)

    assert resp.status_code == 500
    assert resp.json()["detail"] == "ffmpeg is not installed"


@pytest.mark.parametrize(
    "video_name",
    ["../escape", "sub/dir", "..", "", "name with spaces", "name;rm -rf /"],
)
def test_unsafe_video_names_are_rejected(video_name, temp_workdir):
    resp = client.post("/stitch", json={**VALID_PAYLOAD, "video_name": video_name})
    assert resp.status_code == 422


def test_empty_image_list_is_rejected(temp_workdir):
    resp = client.post("/stitch", json={**VALID_PAYLOAD, "image_urls": []})
    assert resp.status_code == 422
