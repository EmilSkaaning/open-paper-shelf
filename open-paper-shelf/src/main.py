from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
from fastapi.staticfiles import StaticFiles

from backend.drive import PAPERS_DIR

app = FastAPI(title="Open Paper Shelf")

PAPERS_DIR.mkdir(exist_ok=True)
app.mount("/papers", StaticFiles(directory=str(PAPERS_DIR)), name="papers")


class Paper(BaseModel):
    id: str
    title: str
    authors: List[str]


@app.get("/")
def read_root():
    return {"message": "Welcome to Open Paper Shelf!"}
