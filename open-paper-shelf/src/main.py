from fastapi import FastAPI
from pydantic import BaseModel
from typing import List

app = FastAPI(title="Open Paper Shelf")

class Paper(BaseModel):
    id: str
    title: str
    authors: List[str]

@app.get("/")
def read_root():
    return {"message": "Welcome to Open Paper Shelf!"}
