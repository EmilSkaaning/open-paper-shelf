from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI(title="Open Paper Shelf")

papers_dir = Path(__file__).resolve().parent.parent.parent / "papers"
papers_dir.mkdir(exist_ok=True)
app.mount("/papers", StaticFiles(directory=str(papers_dir)), name="papers")


class Paper(BaseModel):
    id: str
    title: str
    authors: List[str]


@app.get("/")
def read_root():
    return {"message": "Welcome to Open Paper Shelf!"}
