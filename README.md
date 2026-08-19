# FastAPI FFmpeg Stitch

[![CI](https://github.com/StudentOfAi/fastapi-ffmpeg-stitch/actions/workflows/ci.yml/badge.svg)](https://github.com/StudentOfAi/fastapi-ffmpeg-stitch/actions/workflows/ci.yml)

A REST API that takes a list of image URLs and an audio URL, then stitches them into a single `.mp4` video using FFmpeg.

Built to automate content generation — no manual video editing, just POST your assets and get a video back.

## Usage

```bash
# Install dependencies
pip install fastapi uvicorn requests

# Run the server
uvicorn main:app --host 0.0.0.0 --port 8000
```

### API Endpoint

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

**Response:**
```json
{
  "status": "success",
  "video": "temp/output_video.mp4"
}
```

## How It Works

1. Downloads all images and audio from provided URLs
2. Uses FFmpeg to combine images into a video sequence with configurable timing
3. Merges the audio track with the video
4. Returns the output file path

## Requirements

- Python 3.8+
- FFmpeg installed on the system
- FastAPI + Uvicorn

## Deployment

Includes `.render.yaml` for one-click deployment to Render.com.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests -v
```

14 tests cover request validation, download failures, ffmpeg error handling, and
the constructed ffmpeg command. Network calls and ffmpeg are mocked, so the suite
runs without either.
