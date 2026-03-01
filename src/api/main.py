from fastapi import FastAPI
from src.api.routes.health import router as health_router
from src.api.routes.analyze import router as analyze_router

app = FastAPI(
    title="FinSight AI",
    version="1.0.0"
)

app.include_router(health_router)
app.include_router(analyze_router)