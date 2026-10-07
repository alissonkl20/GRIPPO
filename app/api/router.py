from fastapi import APIRouter

from app.api.routes import health, repo, skills, shell

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(repo.router, tags=["repo"])
api_router.include_router(skills.router, tags=["skills"])
api_router.include_router(shell.router, tags=["shell"])
