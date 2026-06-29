"""CLI Orchestrator — dashboard backend.

Mounted by the Hermes dashboard at ``/api/plugins/cli-orchestrator/``.

This module is imported *standalone* by the web server (via
``importlib.util.spec_from_file_location``), so it must NOT use relative
imports and must be self-contained (stdlib + FastAPI/Pydantic only). It also
deliberately avoids importing Hermes internals so it keeps working across
Hermes refactors — the only coupling is the documented mount path and the
``router`` symbol the loader looks for.

What it does (all real, no fabricated data):
  * GET  /scan            — detect catalog CLIs via shutil.which + version probe
  * GET  /catalog         — raw catalog (no detection)
  * GET  /limits          — per-CLI hourly/daily/monthly caps
  * POST /limits          — set caps for one CLI
  * GET  /usage           — rolling-window usage counts (fed by the hook)
  * POST /usage/incr      — record one invocation (used by the plugin hook)
  * GET  /routing         — orchestration intent -> CLI rules
  * POST /routing         — replace routing rules
  * POST /install         — launch a real install command (detached, logged)
  * GET  /install/status  — poll an install job's log + completion
  * GET  /health          — aggregate health summary for the metrics bar
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

PLUGIN_ID = "cli-orchestrator"

# ---------------------------------------------------------------------------
# Paths — mutable state lives under HERMES_HOME, never inside the plugin repo,
# so the plugin directory stays a clean, git-pull-able artifact.
# ---------------------------------------------------------------------------


def hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "").strip()
    return Path(raw) if raw else (Path.home() / ".hermes")


def state_dir() -> Path:
    d = hermes_home() / PLUGIN_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_file() -> Path:
    return state_dir() / "state.json"


def logs_dir() -> Path:
    d = state_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Catalog — the set of CLIs we know how to detect / install / authenticate.
# Users can extend/override it by dropping a `catalog.json` (same shape) into
# <HERMES_HOME>/cli-orchestrator/. We never execute arbitrary install strings
# from the client: /install only runs commands recorded here, keyed by id.
# ---------------------------------------------------------------------------

DEFAULT_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "claude",
        "name": "Claude Code",
        "category": "AI Coding",
        "bin": "claude",
        "version_args": ["--version"],
        "auth": {"file": "~/.claude/.credentials.json"},
        "install": {"npm": "npm install -g @anthropic-ai/claude-code"},
        "docs": "https://docs.claude.com/en/docs/claude-code",
    },
    {
        "id": "codex",
        "name": "OpenAI Codex CLI",
        "category": "AI Coding",
        "bin": "codex",
        "version_args": ["--version"],
        "auth": {"file": "~/.codex/auth.json"},
        "install": {"npm": "npm install -g @openai/codex", "brew": "brew install codex"},
        "docs": "https://github.com/openai/codex",
    },
    {
        "id": "gemini",
        "name": "Gemini CLI",
        "category": "AI Coding",
        "bin": "gemini",
        "version_args": ["--version"],
        "auth": {"file": "~/.gemini/oauth_creds.json"},
        "install": {"npm": "npm install -g @google/gemini-cli"},
        "docs": "https://github.com/google-gemini/gemini-cli",
    },
    {
        "id": "opencode",
        "name": "OpenCode",
        "category": "AI Coding",
        "bin": "opencode",
        "version_args": ["--version"],
        "auth": None,
        "install": {"npm": "npm install -g opencode-ai", "brew": "brew install sst/tap/opencode"},
        "docs": "https://github.com/sst/opencode",
    },
    {
        "id": "aider",
        "name": "Aider",
        "category": "AI Coding",
        "bin": "aider",
        "version_args": ["--version"],
        "auth": None,
        "install": {"brew": "brew install aider", "pipx": "pipx install aider-chat"},
        "docs": "https://aider.chat",
    },
    {
        "id": "copilot",
        "name": "GitHub Copilot CLI",
        "category": "AI Coding",
        "bin": "copilot",
        "version_args": ["--version"],
        "auth": None,
        "install": {"npm": "npm install -g @github/copilot"},
        "docs": "https://github.com/github/copilot-cli",
    },
    {
        "id": "gh",
        "name": "GitHub CLI",
        "category": "Version Control",
        "bin": "gh",
        "version_args": ["--version"],
        "auth": {"cmd": ["gh", "auth", "status"]},
        "install": {"brew": "brew install gh"},
        "docs": "https://cli.github.com",
    },
    {
        "id": "glab",
        "name": "GitLab CLI",
        "category": "Version Control",
        "bin": "glab",
        "version_args": ["--version"],
        "auth": {"cmd": ["glab", "auth", "status"]},
        "install": {"brew": "brew install glab"},
        "docs": "https://gitlab.com/gitlab-org/cli",
    },
    {
        "id": "ollama",
        "name": "Ollama",
        "category": "Local Models",
        "bin": "ollama",
        "version_args": ["--version"],
        "auth": None,
        "install": {"brew": "brew install ollama"},
        "docs": "https://ollama.com",
    },
    {
        "id": "hermes",
        "name": "Hermes Agent",
        "category": "Agent",
        "bin": "hermes",
        "version_args": ["--version"],
        "auth": None,
        "install": {},
        "docs": "https://hermes-agent.nousresearch.com/docs",
    },
]


def load_catalog() -> List[Dict[str, Any]]:
    """Default catalog merged with an optional user catalog.json (by id)."""
    catalog = {c["id"]: dict(c) for c in DEFAULT_CATALOG}
    user_file = state_dir() / "catalog.json"
    if user_file.exists():
        try:
            extra = json.loads(user_file.read_text(encoding="utf-8"))
            for c in extra if isinstance(extra, list) else []:
                if isinstance(c, dict) and c.get("id"):
                    catalog[c["id"]] = {**catalog.get(c["id"], {}), **c}
        except Exception:
            pass
    return list(catalog.values())


# ---------------------------------------------------------------------------
# State (limits / routing / usage) — small JSON, read-modify-write.
# ---------------------------------------------------------------------------

_DEFAULT_STATE: Dict[str, Any] = {"limits": {}, "routing": [], "usage": {}}


def read_state() -> Dict[str, Any]:
    f = state_file()
    if not f.exists():
        return json.loads(json.dumps(_DEFAULT_STATE))
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        for k, v in _DEFAULT_STATE.items():
            data.setdefault(k, json.loads(json.dumps(v)))
        return data
    except Exception:
        return json.loads(json.dumps(_DEFAULT_STATE))


def write_state(data: Dict[str, Any]) -> None:
    f = state_file()
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(f)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _probe_version(binary: str, args: List[str]) -> Optional[str]:
    try:
        proc = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=6,
        )
        out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        return out.splitlines()[0].strip() if out else None
    except Exception:
        return None


def _auth_state(spec: Optional[Dict[str, Any]]) -> str:
    """Return 'authenticated' | 'unauthenticated' | 'unknown'."""
    if not spec:
        return "unknown"
    if "file" in spec:
        p = Path(os.path.expanduser(spec["file"]))
        return "authenticated" if p.exists() else "unauthenticated"
    if "cmd" in spec:
        try:
            proc = subprocess.run(spec["cmd"], capture_output=True, text=True, timeout=8)
            return "authenticated" if proc.returncode == 0 else "unauthenticated"
        except Exception:
            return "unknown"
    return "unknown"


def _windows(events: List[float], now: Optional[float] = None) -> Dict[str, int]:
    now = now or time.time()
    return {
        "hour": sum(1 for t in events if t >= now - 3600),
        "day": sum(1 for t in events if t >= now - 86400),
        "month": sum(1 for t in events if t >= now - 2592000),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/catalog")
async def get_catalog():
    return {"catalog": load_catalog()}


@router.get("/scan")
async def scan():
    """Detect every catalog CLI on the host (real which + version + auth)."""
    catalog = load_catalog()
    state = read_state()
    limits = state.get("limits", {})
    usage = state.get("usage", {})
    rows: List[Dict[str, Any]] = []
    for c in catalog:
        binary = c["bin"]
        path = shutil.which(binary)
        installed = path is not None
        version = _probe_version(binary, c.get("version_args", ["--version"])) if installed else None
        auth_spec = c.get("auth")
        auth = _auth_state(auth_spec) if installed else "unknown"
        if installed:
            status = "online" if auth in ("authenticated", "unknown") else "unauthenticated"
        else:
            status = "missing"
        rows.append({
            "id": c["id"],
            "name": c["name"],
            "category": c.get("category", "Other"),
            "bin": binary,
            "installed": installed,
            "path": path,
            "version": version,
            "auth_supported": auth_spec is not None,
            "auth": auth,
            "status": status,
            "install_managers": list((c.get("install") or {}).keys()),
            "docs": c.get("docs"),
            "limits": limits.get(c["id"], {"hourly": 0, "daily": 0, "monthly": 0}),
            "usage": _windows(usage.get(c["id"], [])),
        })
    return {"clis": rows, "scanned_at": int(time.time())}


class LimitBody(BaseModel):
    id: str
    hourly: int = 0
    daily: int = 0
    monthly: int = 0


@router.get("/limits")
async def get_limits():
    return {"limits": read_state().get("limits", {})}


@router.post("/limits")
async def set_limits(body: LimitBody):
    state = read_state()
    state.setdefault("limits", {})[body.id] = {
        "hourly": max(0, int(body.hourly)),
        "daily": max(0, int(body.daily)),
        "monthly": max(0, int(body.monthly)),
    }
    write_state(state)
    return {"ok": True, "limits": state["limits"][body.id]}


@router.get("/usage")
async def get_usage():
    state = read_state()
    return {"usage": {k: _windows(v) for k, v in state.get("usage", {}).items()}}


class IncrBody(BaseModel):
    id: str


@router.post("/usage/incr")
async def incr_usage(body: IncrBody):
    state = read_state()
    usage = state.setdefault("usage", {})
    events = usage.setdefault(body.id, [])
    now = time.time()
    events.append(now)
    # Trim anything older than ~40 days to keep the file small.
    usage[body.id] = [t for t in events if t >= now - 3456000]
    write_state(state)
    return {"ok": True, "usage": _windows(usage[body.id], now)}


class RoutingRule(BaseModel):
    intent: str
    cli: str


class RoutingBody(BaseModel):
    rules: List[RoutingRule]


@router.get("/routing")
async def get_routing():
    return {"routing": read_state().get("routing", [])}


@router.post("/routing")
async def set_routing(body: RoutingBody):
    state = read_state()
    state["routing"] = [{"intent": r.intent.strip(), "cli": r.cli.strip()}
                        for r in body.rules if r.intent.strip()]
    write_state(state)
    return {"ok": True, "routing": state["routing"]}


class InstallBody(BaseModel):
    id: str
    manager: Optional[str] = None


@router.post("/install")
async def install(body: InstallBody):
    """Launch a real, detached install for a known CLI. The command is taken
    from the catalog (never from the client) so this is not an arbitrary
    command-execution endpoint."""
    cat = {c["id"]: c for c in load_catalog()}
    entry = cat.get(body.id)
    if not entry:
        raise HTTPException(404, f"Unknown CLI id: {body.id}")
    installers = entry.get("install") or {}
    if not installers:
        raise HTTPException(400, f"No install command known for {body.id}")
    manager = body.manager or next(iter(installers))
    cmd = installers.get(manager)
    if not cmd:
        raise HTTPException(400, f"No '{manager}' installer for {body.id}")

    log_path = logs_dir() / f"{body.id}.log"
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(f"$ {cmd}\n\n")
        fh.flush()
        proc = subprocess.Popen(
            cmd, shell=True, stdout=fh, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (logs_dir() / f"{body.id}.pid").write_text(str(proc.pid), encoding="utf-8")
    return {"ok": True, "id": body.id, "manager": manager, "command": cmd, "pid": proc.pid}


@router.get("/install/status")
async def install_status(id: str):
    cat = {c["id"]: c for c in load_catalog()}
    entry = cat.get(id)
    binary = entry["bin"] if entry else id
    log_path = logs_dir() / f"{id}.log"
    pid_path = logs_dir() / f"{id}.pid"
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    running = False
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)  # signal 0 = liveness probe
            running = True
        except Exception:
            running = False
    return {
        "id": id,
        "running": running,
        "installed": shutil.which(binary) is not None,
        "log": log[-8000:],  # tail
    }


@router.get("/health")
async def health():
    data = (await scan())["clis"]
    total = len(data)
    installed = sum(1 for c in data if c["installed"])
    authed = sum(1 for c in data if c["auth"] == "authenticated")
    auth_supported = sum(1 for c in data if c["auth_supported"] and c["installed"])
    # How close are we to the tightest daily cap across all CLIs (safety margin)?
    worst = 0.0
    for c in data:
        cap = (c.get("limits") or {}).get("daily", 0) or 0
        if cap > 0:
            worst = max(worst, min(1.0, c["usage"]["day"] / cap))
    return {
        "total": total,
        "installed": installed,
        "install_pct": round(100 * installed / total) if total else 0,
        "authenticated": authed,
        "auth_supported": auth_supported,
        "daily_budget_used_pct": round(100 * worst),
        "usage_today": sum(c["usage"]["day"] for c in data),
    }
