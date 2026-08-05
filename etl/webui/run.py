import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn

from etl.webui.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8010)
