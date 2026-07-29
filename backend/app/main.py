from fastapi import FastAPI

from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)


@app.get("/health")
async def health_check() -> dict:
    """Liveness probe used by orchestration/monitoring."""
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}
