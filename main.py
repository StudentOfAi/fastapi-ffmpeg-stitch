import os
import re
import subprocess
from uuid import uuid4

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

app = FastAPI()

TEMP_DIR = "temp"
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
DOWNLOAD_TIMEOUT = 30


class StitchRequest(BaseModel):
    image_urls: list[str]
    audio_url: str
    video_name: str
    shuffle_duration: float = 1.0
    sequential_duration: float = 6.0
    sequential_passes: int = 2

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
        if not SAFE_NAME_RE.match(v):
            raise ValueError(
                "video_name may only contain letters, digits, '.', '_' and '-'"
            )
        if v in {".", ".."}:
            raise ValueError("video_name must be a real file name")
        return v


def download(url: str, dest: str) -> None:
    """Fetch url into dest, failing loudly on a non-2xx response."""
    try:
        r = requests.get(url, timeout=DOWNLOAD_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch {url}: {exc}")
    with open(dest, "wb") as f:
        f.write(r.content)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/stitch")
async def stitch_video(data: StitchRequest):
    os.makedirs(TEMP_DIR, exist_ok=True)

    for i, url in enumerate(data.image_urls):
        download(url, f"{TEMP_DIR}/img{i:02}.png")

    audio_path = f"{TEMP_DIR}/{uuid4()}.mp3"
    download(data.audio_url, audio_path)

    output_path = f"{TEMP_DIR}/{data.video_name}.mp4"
    try:
        subprocess.run(
            [
                "ffmpeg", "-r", "1", "-i", f"{TEMP_DIR}/img%02d.png", "-i", audio_path,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", output_path,
            ],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed")
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace")[-500:]
        raise HTTPException(status_code=500, detail=f"ffmpeg failed: {stderr}")

    return {"status": "success", "video": output_path}
