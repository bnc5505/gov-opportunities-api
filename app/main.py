from fastapi import FastAPI

app = FastAPI(title="Gov Opportunities API")

@app.get("/")
def root():
    return {
        "message": "Gov Opportunities API is running"
    }

@app.get("/health")
def health():
    return {
        "status": "ok"
    }

@app.get("/opportunities")
def list_opportunities():
    return {
        "count": 0,
        "data": []
    }

for r in app.routes:
    print(r.path, r.name, r.methods)
