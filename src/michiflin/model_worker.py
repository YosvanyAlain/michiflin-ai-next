from __future__ import annotations

import json
import sys
from pathlib import Path

from . import agent
from .config import Settings
from .errors import AppError
from .gutenberg import GutenbergClient
from .models import ModelGateway
from .network import JsonClient


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(100001)
        if len(raw) > 100000:
            raise AppError("invalid_prompt")
        request = json.loads(raw)
        settings = Settings(**request["settings"])
        toolset = agent.tools(JsonClient(contact=settings.contact), local_only=request["local_only"]
                              or request["model"] == "local", locale=request["locale"],
                              book_client=GutenbergClient(Path(settings.state_dir) / "catalog")) if request["use_tools"] else None
        result = ModelGateway(settings).complete(request["text"], request["model"], request["history"],
                                                  local_only=request["local_only"], toolset=toolset)
        response = {"ok": True, "data": result}
    except AppError as error:
        response = {"ok": False, "error": error.code, "retryable": error.retryable}
    except Exception:
        response = {"ok": False, "error": "model_outcome_unconfirmed", "retryable": False}
    sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
