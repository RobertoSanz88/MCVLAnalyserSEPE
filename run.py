"""Punto de entrada del ejecutable: levanta uvicorn de forma programatica."""

import multiprocessing

import uvicorn

from app.main import app
from config import get_puerto

if __name__ == "__main__":
    multiprocessing.freeze_support()
    uvicorn.run(app, host="127.0.0.1", port=get_puerto())
