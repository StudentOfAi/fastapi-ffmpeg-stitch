# FastAPI FFmpeg Stitch

[![CI](https://github.com/StudentOfAi/fastapi-ffmpeg-stitch/actions/workflows/ci.yml/badge.svg)](https://github.com/StudentOfAi/fastapi-ffmpeg-stitch/actions/workflows/ci.yml)

A REST API that takes a list of image URLs and an audio URL, then stitches them into a single `.mp4` video using FFmpeg.

Built to automate content generation — no manual video editing, just POST your assets and get a video back. This is the rendering microservice behind [StudentOfAi/viralcut](https://github.com/StudentOfAi/viralcut).

## Usage

```bash
# Install dependencies
pip install fastapi uvicorn requests

# Run the server
uvicorn main:app --host 0.0.0.0 --port 8000
```

### API Endpoints

#### `POST /stitch`

```
POST /stitch
Content-Type: application/json

{
  "image_urls": ["https://example.com/img1.png", "https://example.com/img2.png"],
  "audio_url": "https://example.com/audio.mp3",
  "video_name": "output_video",
  "shuffle_duration": 1.0,
  "sequential_duration": 6.0,
  "sequential_passes": 2
}
```

**Timing parameters** (all optional):

| Parameter | Default | Meaning |
|---|---|---|
| `shuffle_duration` | `1.0` | Seconds per image in the opening shuffled montage (images in random order). Set to `0` to skip the shuffle segment. |
| `sequential_duration` | `6.0` | Seconds per image during each sequential pass. |
| `sequential_passes` | `2` | How many times the full image sequence is shown in order after the shuffle segment. `0` skips the sequential segment. |

At least one segment must be enabled (`shuffle_duration > 0` or `sequential_passes > 0`).

**Response:**
```json
{
  "status": "success",
  "video": "temp/output_video.mp4",
  "download_url": "/video/output_video"
}
```

#### `GET /video/{video_name}`

Returns a previously stitched video as `video/mp4`. `video_name` is the same
name used in the POST (no `.mp4` extension). Unknown names return `404`;
names with path separators or other unsafe characters are rejected.

```bash
curl -o output_video.mp4 http://localhost:8000/video/output_video
```

## How It Works

1. Downloads all images and audio into a per-request temp directory (safe for concurrent requests)
2. Builds an FFmpeg concat list: a shuffled montage segment (`shuffle_duration` per image), then `sequential_passes` in-order passes (`sequential_duration` per image)
3. Renders with FFmpeg (`libx264`, 30 fps) and merges the audio track, trimmed to the shorter stream
4. Returns the output path and a `download_url` for retrieval via `GET /video/{video_name}`

## Requirements

- Python 3.10+
- FFmpeg installed on the system
- FastAPI + Uvicorn

## Deployment

Includes `.render.yaml` for one-click deployment to Render.com.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests -v
```

24 tests cover request validation, download failures, ffmpeg error handling,
the constructed ffmpeg command and concat timing list, per-request temp
directory isolation, and the download endpoint. Network calls and ffmpeg are
mocked, so the suite runs without either.
