import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from database import engine
from models import Base
from rate_limit import limiter
from auth import get_current_user  # noqa: F401 — imported for side-effect; enforce later with Depends()
from routers import states, agencies, sources, opportunities, review_queue, users, saved
from routers.users import auth_router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Government Grants API", version="1.0.0")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_cors_env = os.getenv("CORS_ORIGINS", "")
allowed_origins = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    if _cors_env
    else ["http://localhost:3000", "http://localhost:8000"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(opportunities.router)
app.include_router(states.router)
app.include_router(agencies.router)
app.include_router(sources.router)
app.include_router(review_queue.router)
app.include_router(users.router)
app.include_router(saved.router)


@app.get("/")
def root():
    return {"message": "Government Grants API is running", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}
