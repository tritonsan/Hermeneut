from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import (
    catalog,
    catalog_curator,
    evidence,
    health,
    libraries,
    library,
    mcp_proxy,
    openai_proxy,
    runs,
    sources,
)
from app.settings import get_settings

settings = get_settings()

app = FastAPI(
    title="Hermeneut API",
    description="Evidence-first research agent for ambiguous references in classical texts.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(runs.router)
app.include_router(evidence.router)
app.include_router(library.router)
app.include_router(libraries.router)
app.include_router(sources.router)
app.include_router(catalog.router)
app.include_router(catalog_curator.router)
app.include_router(openai_proxy.router)
app.include_router(mcp_proxy.router)


@app.get("/")
def root():
    return {"name": "Hermeneut API", "docs": "/docs"}
