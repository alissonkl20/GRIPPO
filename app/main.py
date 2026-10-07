import uvicorn
from fastapi import FastAPI

from app.api.router import api_router
from app.config import settings

app = FastAPI(
    title="GRIPPO",
    description="Git agent harness — skills, tools, shell-backed Git execution",
    version="0.1.0",
)
app.include_router(api_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "grippo", "docs": "/docs"}


def run() -> None:
    uvicorn.run("app.main:app", host="127.0.0.1", port=8765, reload=True)


if __name__ == "__main__":
    run()
