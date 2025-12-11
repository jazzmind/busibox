import uvicorn
from fastapi import FastAPI

from routes import authz

app = FastAPI(title="Authz Service", version="1.0.0")


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    return {"status": "ok"}


app.include_router(authz.router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8010, reload=False)


