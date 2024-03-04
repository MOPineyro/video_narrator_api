from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"Hello": "from FastAPI running in a Docker container on Render!"}
