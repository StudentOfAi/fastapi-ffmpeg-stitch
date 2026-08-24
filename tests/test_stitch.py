"""Tests for the /stitch endpoint.

Network and ffmpeg are both mocked: these tests verify the service's own
contract (validation, error mapping, command construction), not FFmpeg.
"""

import os
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
    assert resp.json() == {
        "status": "success",
        "video": "temp/my-clip.mp4",
        "download_url": "/video/my-clip",
    }
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


# --- timing parameters ---------------------------------------------------


def read_concat_file(cmd):
    """Given a captured ffmpeg command, return the concat list's lines."""
    concat_path = cmd[cmd.index("-f") + 5]  # -f concat -safe 0 -i <path>
    with open(concat_path) as f:
        return f.read().splitlines()


def post_and_capture_concat(payload):
    """POST /stitch with mocks, returning (response, ffmpeg cmd, concat lines).

    The concat file lives in the per-request job dir, which is removed after
    the request, so it has to be read at the moment ffmpeg would run.
    """
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["lines"] = read_concat_file(cmd)

    with patch.object(main.requests, "get", return_value=FakeResponse()), \
         patch.object(main.subprocess, "run", side_effect=fake_run):
        resp = client.post("/stitch", json=payload)
    return resp, captured.get("cmd"), captured.get("lines")


def test_sequential_duration_and_passes_drive_the_concat_list(temp_workdir):
    payload = {
        **VALID_PAYLOAD,
        "shuffle_duration": 0,
        "sequential_duration": 2.5,
        "sequential_passes": 3,
    }
    resp, cmd, lines = post_and_capture_concat(payload)

    assert resp.status_code == 200
    assert "-r" not in cmd[:cmd.index("-i")]  # no hardcoded input frame rate
    # 2 images x 3 passes, in order, 2.5s each
    files = [l for l in lines if l.startswith("file ")]
    assert files[:-1] == ["file 'img0000.png'", "file 'img0001.png'"] * 3
    assert lines.count("duration 2.5") == 6


def test_shuffle_segment_precedes_sequential_and_uses_sampled_order(temp_workdir):
    payload = {
        **VALID_PAYLOAD,
        "shuffle_duration": 0.5,
        "sequential_duration": 4.0,
        "sequential_passes": 1,
    }
    with patch.object(main.random, "sample", return_value=[1, 0]) as sample:
        resp, _, lines = post_and_capture_concat(payload)

    assert resp.status_code == 200
    sample.assert_called_once_with(range(2), 2)
    files = [l for l in lines if l.startswith("file ")]
    # shuffled segment first (sampled order), then one sequential pass
    assert files[:2] == ["file 'img0001.png'", "file 'img0000.png'"]
    assert files[2:4] == ["file 'img0000.png'", "file 'img0001.png'"]
    assert lines.count("duration 0.5") == 2
    assert lines.count("duration 4.0") == 2


def test_zero_shuffle_and_zero_passes_is_rejected(temp_workdir):
    payload = {**VALID_PAYLOAD, "shuffle_duration": 0, "sequential_passes": 0}
    assert client.post("/stitch", json=payload).status_code == 422


# --- per-request tempdir isolation ---------------------------------------


def test_each_request_gets_its_own_workdir_and_it_is_cleaned_up(temp_workdir):
    _, cmd_a, _ = post_and_capture_concat(VALID_PAYLOAD)
    _, cmd_b, _ = post_and_capture_concat(VALID_PAYLOAD)

    dir_a = os.path.dirname(cmd_a[cmd_a.index("-f") + 5])
    dir_b = os.path.dirname(cmd_b[cmd_b.index("-f") + 5])
    assert dir_a != dir_b
    assert os.path.dirname(dir_a) == "temp"
    # job dirs are removed after the request; nothing shared is left behind
    assert [p for p in os.listdir("temp") if os.path.isdir(os.path.join("temp", p))] == []


def test_frames_use_four_digit_padding(temp_workdir):
    payload = {**VALID_PAYLOAD, "shuffle_duration": 0, "sequential_passes": 1}
    _, _, lines = post_and_capture_concat(payload)
    assert "file 'img0000.png'" in lines
    assert not any("img00.png" in l or "img01.png" in l for l in lines)


# --- GET /video/{video_name} ---------------------------------------------


def test_download_returns_existing_video(temp_workdir):
    os.makedirs("temp", exist_ok=True)
    with open("temp/my-clip.mp4", "wb") as f:
        f.write(b"mp4-bytes")

    resp = client.get("/video/my-clip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == b"mp4-bytes"


def test_download_missing_video_returns_404(temp_workdir):
    assert client.get("/video/nope").status_code == 404


@pytest.mark.parametrize("video_name", ["..", "name with spaces", "name;rm"])
def test_download_rejects_unsafe_names(video_name, temp_workdir):
    resp = client.get(f"/video/{video_name}")
    assert resp.status_code in (400, 404)
    # whatever the status, nothing outside temp/ may be served
    assert resp.headers.get("content-type") != "video/mp4"
