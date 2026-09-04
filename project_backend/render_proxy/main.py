import os

import httpx
from fastapi import FastAPI, Request, Response

app = FastAPI()

BACKEND_URL = os.environ["BACKEND_URL"].rstrip("/")


@app.get("/")
async def health():
    return {"status": "proxy online"}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(path: str, request: Request):
    url = f"{BACKEND_URL}/{path}"

    body = await request.body()

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }

    async with httpx.AsyncClient(timeout=600.0) as client:
        upstream = await client.request(
            method=request.method,
            url=url,
            params=request.query_params,
            content=body,
            headers=headers,
        )

    excluded = {
        "content-encoding",
        "content-length",
        "transfer-encoding",
        "connection",
    }

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in excluded
    }

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )