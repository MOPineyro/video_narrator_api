from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import shutil
import cv2
import base64
import tempfile
import httpx
import os

app = FastAPI()

@app.get("/")
def read_root():
    return {"Hello": "from FastAPI running in a Docker container on Render!"}

class VideoURL(BaseModel):
    video_url: str

async def download_video(video_url: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.get(video_url)
        if response.status_code == 200:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_file:
                temp_file.write(response.content)
                return temp_file.name
    return None

def extract_frames(video_path: str) -> list:
    video = cv2.VideoCapture(video_path)
    base64_frames = []
    while video.isOpened():
        success, frame = video.read()
        if not success:
            break
        _, buffer = cv2.imencode(".jpg", frame)
        base64_frames.append(base64.b64encode(buffer).decode("utf-8"))
    video.release()
    return base64_frames

@app.post("/generate_script/")
async def generate_script(video_url: VideoURL):
    video_path = await download_video(video_url.video_url)
    if not video_path:
        raise HTTPException(status_code=500, detail="Failed to download video.")
    
    base64_frames = extract_frames(video_path)
    shutil.rmtree(video_path, ignore_errors=True)
    if not base64_frames:
        raise HTTPException(status_code=500, detail="No frames extracted.")

    # Selecting a sample of frames to avoid rate limits, modify as needed
    sample_frames = base64_frames[0::150]

    PROMPT_MESSAGES = [
    {
            "role": "user",
            "content": [
                "These are frames of a video. Create a short voiceover script in the style of Mike Breen. Damian Lillard is the player who scored the buzzer beater, series winner, against Paul George. Make output to be readable in 30s. Don't include context, just commentary.",
                *map(lambda x: {"image": x, "resize": 768}, sample_frames[0::150]),
            ]
        },
    ]

    params = {
        "model": "gpt-4-vision-preview",
        "messages": PROMPT_MESSAGES,
        "api_key": os.environ["OPENAI_API_KEY"],
        "headers": {"Openai-Version": "2020-11-07"},
        "max_tokens": 500,
    }
    
    # prompt_messages = {
    #     "prompts": [
    #         {
    #             "role": "system",
    #             "content": "You are a highly knowledgeable and articulate sports commentator."
    #         },
    #         *[
    #             {
    #                 "role": "user",
    #                 "content": {
    #                     "image": frame,
    #                     "resize": "768"
    #                 }
    #             } for frame in sample_frames
    #         ]
    #     ]
    # }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json"
            },
            json=params,
        )

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="OpenAI API call failed.")

    script_content = response.json()
    return {"script": script_content}


# TODO: Optimize - store data of first image of video in db, compare before calling open ai to transcribe