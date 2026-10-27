"""python -m qiban starts both the web app and its background task loop."""

import os

import uvicorn

from .web import create_app

if __name__ == "__main__":
    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=int(os.getenv("PORT", "4317")),
        workers=1,
        access_log=False,
    )
