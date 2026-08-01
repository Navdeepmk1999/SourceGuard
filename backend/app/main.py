from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints import documents, query, workspaces
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(workspaces.router)
app.include_router(documents.router)
app.include_router(query.router)


@app.get("/health")
async def health_check() -> dict:
    """Liveness probe used by orchestration/monitoring."""
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}
