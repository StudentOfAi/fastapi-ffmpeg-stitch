import os
import random
import re
import shutil
import subprocess
from uuid import uuid4

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

app = FastAPI()

TEMP_DIR = "temp"
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
DOWNLOAD_TIMEOUT = 30
OUTPUT_FPS = "30"


def validate_safe_name(v: str) -> str:
    """Reject anything that could escape TEMP_DIR (path separators, "..", empty)."""
    if not SAFE_NAME_RE.match(v):
        raise ValueError(
            "video_name may only contain letters, digits, '.', '_' and '-'"
        )
    if v in {".", ".."}:
        raise ValueError("video_name must be a real file name")
    return v


class StitchRequest(BaseModel):
    image_urls: list[str]
    audio_url: str
    video_name: str
    shuffle_duration: float = Field(default=1.0, ge=0)
    sequential_duration: float = Field(default=6.0, gt=0)
    sequential_passes: int = Field(default=2, ge=0)

    @field_validator("image_urls")
    @classmethod
    def at_least_one_image(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("image_urls must contain at least one URL")
        return v

    @field_validator("video_name")
    @classmethod
    def safe_video_name(cls, v: str) -> str:
        # The name becomes a filesystem path, so reject anything that could
        # escape TEMP_DIR (path separators, "..", empty).
        return validate_safe_name(v)

    @model_validator(mode="after")
    def at_least_one_segment(self) -> "StitchRequest":
        if self.shuffle_duration == 0 and self.sequential_passes == 0:
            raise ValueError(
                "video would be empty: enable shuffle_duration or sequential_passes"
            )
        return self


def download(url: str, dest: str) -> None:
    """Fetch url into dest, failing loudly on a non-2xx response."""
    try:
        r = requests.get(url, timeout=DOWNLOAD_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch {url}: {exc}")
    with open(dest, "wb") as f:
        f.write(r.content)


def build_frame_schedule(image_count: int, data: StitchRequest) -> list[tuple[int, float]]:
    """Return (image_index, display_seconds) pairs describing the video.

    A shuffled montage segment (shuffle_duration per image, random order) comes
    first when shuffle_duration > 0, followed by sequential_passes in-order
    passes showing each image for sequential_duration seconds.
    """
    schedule: list[tuple[int, float]] = []
    if data.shuffle_duration > 0:
        for idx in random.sample(range(image_count), image_count):
            schedule.append((idx, data.shuffle_duration))
    for _ in range(data.sequential_passes):
        for idx in range(image_count):
            schedule.append((idx, data.sequential_duration))
    return schedule


def write_concat_file(path: str, schedule: list[tuple[int, float]]) -> None:
    """Write an ffmpeg concat-demuxer list with per-frame durations.

    Entries are relative to the list file's directory. The final file is
    repeated without a duration because the concat demuxer ignores the
    duration directive on the last entry otherwise.
    """
    lines = ["ffconcat version 1.0"]
    for idx, duration in schedule:
        lines.append(f"file 'img{idx:04d}.png'")
        lines.append(f"duration {duration}")
    lines.append(f"file 'img{schedule[-1][0]:04d}.png'")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/stitch")
async def stitch_video(data: StitchRequest):
    os.makedirs(TEMP_DIR, exist_ok=True)

    # Per-request working directory so concurrent requests never share frames.
    job_dir = os.path.join(TEMP_DIR, uuid4().hex)
    os.makedirs(job_dir)

    try:
        for i, url in enumerate(data.image_urls):
            download(url, os.path.join(job_dir, f"img{i:04d}.png"))

        audio_path = os.path.join(job_dir, "audio.mp3")
        download(data.audio_url, audio_path)

        schedule = build_frame_schedule(len(data.image_urls), data)
        concat_path = os.path.join(job_dir, "frames.txt")
        write_concat_file(concat_path, schedule)

        output_path = os.path.join(TEMP_DIR, f"{data.video_name}.mp4")
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0", "-i", concat_path,
                    "-i", audio_path,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", OUTPUT_FPS,
                    "-shortest", output_path,
                ],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="ffmpeg is not installed")
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode("utf-8", "replace")[-500:]
            raise HTTPException(status_code=500, detail=f"ffmpeg failed: {stderr}")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)

    return {
        "status": "success",
        "video": output_path,
        "download_url": f"/video/{data.video_name}",
    }


@app.get("/video/{video_name}")
async def get_video(video_name: str):
    try:
        validate_safe_name(video_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    path = os.path.join(TEMP_DIR, f"{video_name}.mp4")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"No video named {video_name}")
    return FileResponse(path, media_type="video/mp4", filename=f"{video_name}.mp4")
