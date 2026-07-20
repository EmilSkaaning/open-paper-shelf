from fastapi import FastAPI

app = FastAPI(title="Open Paper Shelf API")


@app.get("/")
def read_root():
    return {"message": "Welcome to Open Paper Shelf API"}
