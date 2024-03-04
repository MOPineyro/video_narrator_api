from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import shutil
import cv2
import base64
import tempfile
from httpx import AsyncClient, ReadTimeout
import os
import logging
import asyncio
from openai import AsyncOpenAI
import time
import subprocess
import glob

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI()
openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

@app.get("/")
def read_root():
    return {"Hello": "from FastAPI running in a Docker container on Render!"}

class VideoURL(BaseModel):
    video_url: str
    description: str

async def download_video(video_url: str) -> str:
    async with AsyncClient() as client:
        response = await client.get(video_url)
        if response.status_code == 200:
            total_bytes = response.headers.get('Content-Length')
            if total_bytes is not None:
                total_bytes = int(total_bytes)
                downloaded_bytes = 0

            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_file:
                async for chunk in response.aiter_bytes():
                    temp_file.write(chunk)
                    downloaded_bytes += len(chunk)
                    if total_bytes is not None:
                        percentage = (downloaded_bytes / total_bytes) * 100
                        logging.info(f"Download progress: {percentage:.2f}%")
                return temp_file.name
        else:
            logging.error(f"Failed to download video from {video_url}. Status code: {response.status_code}")
    return None

def extract_frames(video_path: str) -> list:
    video = cv2.VideoCapture(video_path)
    base64_frames = []
    total_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    logging.info(f"Total frames in video: {total_frames}")

    frame_rate = int(video.get(cv2.CAP_PROP_FPS))
    logging.info(f"Frame rate: {frame_rate} FPS")

    # Assuming a frame is extracted every 5 seconds
    sampling_rate_multiply = int(os.environ.get("SAMPLING_RATE", 5))
    sampling_rate = frame_rate * sampling_rate_multiply
    logging.info(f"Extracting one frame every 5 seconds, i.e., every {sampling_rate} frames")

    frame_count = 0
    while video.isOpened():
        success, frame = video.read()
        if not success:
            break
        if frame_count % sampling_rate == 0:
            _, buffer = cv2.imencode(".jpg", frame)
            base64_frames.append(base64.b64encode(buffer).decode("utf-8"))
            logging.info(f"Extracted frame {frame_count} as base64")
        frame_count += 1

    video.release()
    logging.info(f"Extraction complete. Total frames extracted: {len(base64_frames)}")
    return base64_frames

def get_video_info(video_path: str):
    command = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=pix_fmt',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_path
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return result.stdout.decode('utf-8').strip()

def extract_frames_ffmpeg(video_path: str) -> list:
    frame_rate = int(os.environ.get("FRAME_RATE", 5)) 
    output_dir = tempfile.mkdtemp() 

    pixel_format = get_video_info(video_path)

    command = [
        'ffmpeg',
        '-i', video_path, 
        '-vf', f'fps=1/{frame_rate}', 
        '-pix_fmt', pixel_format,
        f'{output_dir}/frame_%05d.jpg' 
    ]
    subprocess.run(command, check=True) 

    # Read the frames from the output directory
    base64_frames = []
    for frame_file in sorted(glob.glob(f'{output_dir}/*.jpg')):
        with open(frame_file, 'rb') as f:
            base64_frames.append(base64.b64encode(f.read()).decode("utf-8"))
        logging.info(f"Extracted frame {frame_file} as base64")

    logging.info(f"Extraction complete. Total frames extracted: {len(base64_frames)}")
    shutil.rmtree(output_dir)  
    return base64_frames

def chunk_frames(frames, chunk_size):
    for i in range(0, len(frames), chunk_size):
        yield frames[i:i + chunk_size]


@app.post("/generate_script/")
async def generate_script(video_url: VideoURL):
    logging.info(f"Attempting to download video from URL: {video_url.video_url}")
    video_path = await download_video(video_url.video_url)
    if not video_path:
        logging.error("Failed to download video.")
        raise HTTPException(status_code=500, detail="Failed to download video.")
    
    logging.info("Video downloaded successfully. Extracting frames...")
    base64_frames = extract_frames_ffmpeg(video_path)
    shutil.rmtree(video_path, ignore_errors=True)
    if not base64_frames:
        logging.error("No frames extracted from video.")
        raise HTTPException(status_code=500, detail="No frames extracted.")
    
    logging.info(f"Extracted {len(base64_frames)} frames. Generating script...")

    # Define the chunk size, 150 is reasonable
    chunk_size = 150
    chunked_frames = list(chunk_frames(base64_frames, chunk_size))

    results = []
    for i, frames in enumerate(chunked_frames):
        logging.info(f"Processing chunk {i+1} of {len(chunked_frames)}")

        PROMPT_MESSAGES = [
            {
                "role": "user",
                "content": [
                    f"These are frames of a video. Create a short voiceover script in the style of Mike Breen. {video_url.description}. Make output to be readable in 30s. Don't include context, only commentary as if you are the speaker. This video is of th past and not real-time. No need to add a note about this being fictional and for a task. Just the script.",
                    *map(lambda x: {"image": x, "resize": 768}, frames[0::10]),
                ],
            },
        ]

        try:
            result = await openai_client.chat.completions.create(
                messages=PROMPT_MESSAGES,
                model="gpt-4-vision-preview",
                max_tokens=500,
            )
            results.append(result)
            logging.info(f"Completed request {i+1}")
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        if len(results) - 1 > 0:
            await asyncio.sleep(60)

    logging.info("Script generated successfully.")
    joined_results = ' '.join([result.choices[0].message.content for result in results])
    return {"script": joined_results}


# TODO: Optimize - store data of first image of video in db, compare before calling open ai to transcribe