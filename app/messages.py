"""User-facing messages travel as keys ("@key" or "@key:{json params}"); the UI translates them.

Keeping text out of the backend lets stored errors follow whichever language the viewer picks later.
"""
import json


def msg(key: str, **params) -> str:
    return f"@{key}:{json.dumps(params, ensure_ascii=False)}" if params else f"@{key}"
