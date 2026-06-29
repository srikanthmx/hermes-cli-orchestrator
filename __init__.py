"""CLI Orchestrator — Hermes general-plugin side.

The dashboard UI/backend lives in ``dashboard/``. This module wires the plugin
into the *agent runtime*:

  * a ``post_tool_call`` hook that records every time the agent drives a tracked
    CLI through the ``terminal`` tool (this is what populates the usage gauges
    you see in the CLI Matrix tab — real counts, not simulated), and
  * a ``/cli`` slash command for a quick status read inside any session.

State is shared with the dashboard backend via the same JSON file under
``<HERMES_HOME>/cli-orchestrator/state.json``. We re-implement the tiny path
logic here rather than importing the standalone ``dashboard/plugin_api.py``
(which is loaded by the web server out-of-package), keeping this file
self-contained and resilient to Hermes refactors.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_ID = "cli-orchestrator"

# Binaries we count. Mirrors dashboard/plugin_api.py DEFAULT_CATALOG ids/bins.
TRACKED_BINS = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "opencode": "opencode",
    "aider": "aider",
    "copilot": "copilot",
    "gh": "gh",
    "glab": "glab",
    "ollama": "ollama",
    "hermes": "hermes",
}
# bin name -> catalog id (here they're identical, but keep the indirection)
_BIN_TO_ID = {v: k for k, v in TRACKED_BINS.items()}


def _hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "").strip()
    return Path(raw) if raw else (Path.home() / ".hermes")


def _state_file() -> Path:
    d = _hermes_home() / PLUGIN_ID
    d.mkdir(parents=True, exist_ok=True)
    return d / "state.json"


def _read_state() -> dict:
    f = _state_file()
    if not f.exists():
        return {"limits": {}, "routing": [], "usage": {}}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        data.setdefault("usage", {})
        return data
    except Exception:
        return {"limits": {}, "routing": [], "usage": {}}


def _write_state(data: dict) -> None:
    f = _state_file()
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(f)


def _first_binary(command: str) -> str | None:
    """Best-effort: pull the invoked program name out of a shell command,
    skipping leading ``env VAR=...`` and ``sudo`` prefixes."""
    try:
        tokens = shlex.split(command)
    except Exception:
        tokens = command.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("sudo", "command", "nice", "nohup"):
            i += 1
            continue
        if tok == "env":
            i += 1
            while i < len(tokens) and "=" in tokens[i]:
                i += 1
            continue
        return os.path.basename(tok)
    return None


def _record_usage(cli_id: str) -> None:
    state = _read_state()
    usage = state.setdefault("usage", {})
    events = usage.setdefault(cli_id, [])
    now = time.time()
    events.append(now)
    usage[cli_id] = [t for t in events if t >= now - 3456000]  # keep ~40 days
    _write_state(state)


def _on_post_tool_call(tool_name=None, args=None, result=None, task_id=None, **kwargs):
    """Fires after every tool call. We only care about ``terminal`` commands
    whose program is one of our tracked CLIs."""
    try:
        if tool_name != "terminal":
            return
        command = ""
        if isinstance(args, dict):
            command = str(args.get("command") or args.get("cmd") or "")
        elif isinstance(args, str):
            command = args
        if not command.strip():
            return
        binary = _first_binary(command)
        if binary and binary in _BIN_TO_ID:
            _record_usage(_BIN_TO_ID[binary])
    except Exception as exc:  # never break the tool pipeline
        logger.debug("cli-orchestrator usage hook skipped: %s", exc)


def _handle_cli_command(raw_args: str):
    """/cli — quick status of tracked CLIs and their usage today."""
    import shutil

    state = _read_state()
    usage = state.get("usage", {})
    limits = state.get("limits", {})
    now = time.time()
    lines = ["CLI Matrix — local CLI status", ""]
    for cli_id, binary in TRACKED_BINS.items():
        present = shutil.which(binary) is not None
        events = usage.get(cli_id, [])
        day = sum(1 for t in events if t >= now - 86400)
        cap = (limits.get(cli_id) or {}).get("daily", 0) or 0
        mark = "●" if present else "○"
        cap_str = f"/{cap}" if cap else ""
        lines.append(f"  {mark} {binary:<10} today: {day}{cap_str}")
    lines.append("")
    lines.append("Open the 'CLI Matrix' tab in `hermes dashboard` for the full UI.")
    return "\n".join(lines)


def register(ctx):
    """Wire the runtime hook + slash command. Called once at startup; if it
    raises, Hermes disables this plugin but keeps running."""
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    try:
        ctx.register_command(
            "cli",
            handler=_handle_cli_command,
            description="Show local CLI status + usage (CLI Orchestrator)",
        )
    except Exception as exc:
        # register_command is newer; tolerate older Hermes that lack it.
        logger.debug("cli-orchestrator: /cli command not registered: %s", exc)
