from fastapi import FastAPI
from pydantic import BaseModel
import requests, os, subprocess
from uuid import uuid4

app = FastAPI()

class StitchRequest(BaseModel):
    image_urls: list[str]
    audio_url: str
    video_name: str

@app.post("/stitch")
async def stitch_video(data: StitchRequest):
    os.makedirs("temp", exist_ok=True)

    for i, url in enumerate(data.image_urls):
        r = requests.get(url)
        with open(f"temp/img{i:02}.png", "wb") as f:
            f.write(r.content)

    audio_path = f"temp/{uuid4()}.mp3"
    r = requests.get(data.audio_url)
    with open(audio_path, "wb") as f:
        f.write(r.content)

    output_path = f"temp/{data.video_name}.mp4"
    subprocess.run([
        "ffmpeg", "-r", "1", "-i", "temp/img%02d.png", "-i", audio_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", output_path
    ])

    return {"status": "success", "video": output_path}
