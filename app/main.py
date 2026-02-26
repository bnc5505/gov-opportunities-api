from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine
from models import Base
from routers import states, agencies, sources, opportunities, review_queue, users, saved

# Create all tables on startup (non-destructive — only adds missing tables)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Government Grants API",
    description="API for searching and managing government grant opportunities",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(opportunities.router)
app.include_router(states.router)
app.include_router(agencies.router)
app.include_router(sources.router)
app.include_router(review_queue.router)
app.include_router(users.router)
app.include_router(saved.router)


@app.get("/", tags=["Health"])
def root():
    return {"message": "Government Grants API is running", "docs": "/docs"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}
