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

# Each entry may also carry:
#   "provider": the Hermes model-provider id this CLI/plan maps to (so the
#               dashboard can offer "use as model / add to fallback chain"), or None.
#   "plan":     human label for the cost tier (Subscription / Free tier / BYO key / Local).
# The thesis: aggregating many free + subscription tiers here gives you more
# capacity than any single paid API plan — that's what the chain/governor exploits.
DEFAULT_CATALOG: List[Dict[str, Any]] = [
    # ── AI coding agents (model-backed; chain/delegation candidates) ──────────
    {
        "id": "claude", "name": "Claude Code", "category": "AI Coding",
        "bin": "claude", "version_args": ["--version"],
        "auth": {"file": "~/.claude/.credentials.json"},
        "install": {"npm": "npm install -g @anthropic-ai/claude-code"},
        "provider": "anthropic", "plan": "Subscription (Claude Pro/Max)",
        "docs": "https://docs.claude.com/en/docs/claude-code",
    },
    {
        "id": "codex", "name": "OpenAI Codex CLI", "category": "AI Coding",
        "bin": "codex", "version_args": ["--version"],
        "auth": {"file": "~/.codex/auth.json"},
        "install": {"npm": "npm install -g @openai/codex", "brew": "brew install codex"},
        "provider": "openai-codex", "plan": "Subscription (ChatGPT Plus/Pro)",
        "docs": "https://github.com/openai/codex",
    },
    {
        "id": "gemini", "name": "Gemini CLI", "category": "AI Coding",
        "bin": "gemini", "version_args": ["--version"],
        "auth": {"file": "~/.gemini/oauth_creds.json"},
        "install": {"npm": "npm install -g @google/gemini-cli"},
        "provider": "gemini", "plan": "Free tier + API key (Google)",
        "docs": "https://github.com/google-gemini/gemini-cli",
    },
    {
        "id": "qwen", "name": "Qwen Code", "category": "AI Coding",
        "bin": "qwen", "version_args": ["--version"], "auth": None,
        "install": {"npm": "npm install -g @qwen-code/qwen-code"},
        "provider": "qwen-oauth", "plan": "Free OAuth (Qwen, ~2k req/day)",
        "docs": "https://github.com/QwenLM/qwen-code",
    },
    {
        "id": "copilot", "name": "GitHub Copilot CLI", "category": "AI Coding",
        "bin": "copilot", "version_args": ["--version"], "auth": None,
        "install": {"npm": "npm install -g @github/copilot"},
        "provider": "copilot", "plan": "Subscription (GitHub Copilot)",
        "docs": "https://github.com/github/copilot-cli",
    },
    {
        "id": "opencode", "name": "OpenCode", "category": "AI Coding",
        "bin": "opencode", "version_args": ["--version"], "auth": None,
        "install": {"npm": "npm install -g opencode-ai", "brew": "brew install sst/tap/opencode"},
        "provider": "opencode-zen", "plan": "BYO key / free models",
        "docs": "https://github.com/sst/opencode",
    },
    {
        "id": "cursor-agent", "name": "Cursor CLI", "category": "AI Coding",
        "bin": "cursor-agent", "version_args": ["--version"], "auth": None,
        "install": {"script": "curl https://cursor.com/install -fsS | bash"},
        "provider": None, "plan": "Subscription (Cursor)",
        "docs": "https://docs.cursor.com/en/cli/overview",
    },
    {
        "id": "amp", "name": "Amp (Sourcegraph)", "category": "AI Coding",
        "bin": "amp", "version_args": ["--version"], "auth": None,
        "install": {"npm": "npm install -g @sourcegraph/amp"},
        "provider": None, "plan": "Free credits (Sourcegraph)",
        "docs": "https://ampcode.com",
    },
    {
        "id": "crush", "name": "Crush (Charm)", "category": "AI Coding",
        "bin": "crush", "version_args": ["--version"], "auth": None,
        "install": {"npm": "npm install -g @charmland/crush",
                    "brew": "brew install charmbracelet/tap/crush"},
        "provider": None, "plan": "BYO key (multi-provider)",
        "docs": "https://github.com/charmbracelet/crush",
    },
    {
        "id": "goose", "name": "Goose (Block)", "category": "AI Coding",
        "bin": "goose", "version_args": ["--version"], "auth": None,
        "install": {"script": "curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh | bash"},
        "provider": None, "plan": "BYO key (Block)",
        "docs": "https://block.github.io/goose/",
    },
    {
        "id": "aider", "name": "Aider", "category": "AI Coding",
        "bin": "aider", "version_args": ["--version"], "auth": None,
        "install": {"brew": "brew install aider", "pipx": "pipx install aider-chat"},
        "provider": None, "plan": "BYO key",
        "docs": "https://aider.chat",
    },
    # ── AI tools / chat ───────────────────────────────────────────────────────
    {
        "id": "mods", "name": "Mods (Charm)", "category": "AI Tools",
        "bin": "mods", "version_args": ["--version"], "auth": None,
        "install": {"brew": "brew install charmbracelet/tap/mods",
                    "go": "go install github.com/charmbracelet/mods@latest"},
        "provider": None, "plan": "BYO key",
        "docs": "https://github.com/charmbracelet/mods",
    },
    {
        "id": "llm", "name": "llm (Datasette)", "category": "AI Tools",
        "bin": "llm", "version_args": ["--version"], "auth": None,
        "install": {"pipx": "pipx install llm", "brew": "brew install llm",
                    "uv": "uv tool install llm"},
        "provider": None, "plan": "BYO key (plugin ecosystem)",
        "docs": "https://llm.datasette.io",
    },
    # ── Version control ───────────────────────────────────────────────────────
    {
        "id": "gh", "name": "GitHub CLI", "category": "Version Control",
        "bin": "gh", "version_args": ["--version"],
        "auth": {"cmd": ["gh", "auth", "status"]},
        "install": {"brew": "brew install gh"},
        "provider": None, "plan": "Free (GitHub account)",
        "docs": "https://cli.github.com",
    },
    {
        "id": "glab", "name": "GitLab CLI", "category": "Version Control",
        "bin": "glab", "version_args": ["--version"],
        "auth": {"cmd": ["glab", "auth", "status"]},
        "install": {"brew": "brew install glab"},
        "provider": None, "plan": "Free (GitLab account)",
        "docs": "https://gitlab.com/gitlab-org/cli",
    },
    # ── Local models (the free floor) ─────────────────────────────────────────
    {
        "id": "ollama", "name": "Ollama", "category": "Local Models",
        "bin": "ollama", "version_args": ["--version"], "auth": None,
        "install": {"brew": "brew install ollama"},
        "provider": "custom", "plan": "Local / free (offline floor)",
        "docs": "https://ollama.com",
    },
    # ── Agent host ────────────────────────────────────────────────────────────
    {
        "id": "hermes", "name": "Hermes Agent", "category": "Agent",
        "bin": "hermes", "version_args": ["--version"], "auth": None,
        "install": {}, "provider": None, "plan": "—",
        "docs": "https://hermes-agent.nousresearch.com/docs",
    },
]


# Delegation-capable worker CLIs, in fallback priority order (mirrors
# __init__.py DELEGATE_ARGV + CODING_PRIORITY). Used to mark "workers" in scan.
_DELEGATE_PRIORITY = ["codex", "claude", "qwen", "opencode", "gemini"]


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
            "provider": c.get("provider"),
            "plan": c.get("plan"),
            "worker": c["id"] in _DELEGATE_PRIORITY,
            "worker_rank": (_DELEGATE_PRIORITY.index(c["id"]) + 1) if c["id"] in _DELEGATE_PRIORITY else None,
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


def _model_provider_map() -> Dict[str, str]:
    """Map model id -> provider from the active config chain (best effort)."""
    out: Dict[str, str] = {}
    try:
        import yaml
        cfg = yaml.safe_load(open(hermes_home() / "config.yaml")) or {}
        m = cfg.get("model") or {}
        if isinstance(m, dict):
            name = m.get("default") or m.get("model")
            if name:
                out[str(name)] = m.get("provider") or "?"
        for f in (cfg.get("fallback_providers") or []):
            if f.get("model"):
                out[str(f["model"])] = f.get("provider") or "?"
    except Exception:
        pass
    return out


@router.get("/model-usage")
async def model_usage():
    """Per-model/provider turn counts — 'what's burning my subscription'."""
    state = read_state()
    mu = state.get("model_usage", {})
    prov = _model_provider_map()
    rows = []
    for model, events in mu.items():
        w = _windows(events)
        rows.append({"model": model, "provider": prov.get(model, "?"), **w})
    rows.sort(key=lambda r: -r["day"])
    return {"models": rows, "total_today": sum(r["day"] for r in rows)}


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
    # Worker fleet: installed delegation workers, delegations today, active one.
    by_id = {c["id"]: c for c in data}
    workers_installed = sum(1 for w in _DELEGATE_PRIORITY
                            if by_id.get(w, {}).get("installed"))
    delegations_today = sum(by_id.get(w, {}).get("usage", {}).get("day", 0)
                            for w in _DELEGATE_PRIORITY)
    active_worker = None
    for w in _DELEGATE_PRIORITY:
        row = by_id.get(w)
        if row and row["installed"]:
            cap = (row.get("limits") or {}).get("daily", 0) or 0
            if not (cap > 0 and row["usage"]["day"] >= cap):
                active_worker = w
                break
    # Active brain (the model Hermes is running as its orchestrator).
    brain = None
    try:
        import yaml
        cfg = yaml.safe_load(open(hermes_home() / "config.yaml")) or {}
        mc = cfg.get("model") or {}
        if isinstance(mc, dict):
            brain = mc.get("default") or mc.get("model")
    except Exception:
        pass
    return {
        "total": total,
        "installed": installed,
        "install_pct": round(100 * installed / total) if total else 0,
        "authenticated": authed,
        "auth_supported": auth_supported,
        "daily_budget_used_pct": round(100 * worst),
        "usage_today": sum(c["usage"]["day"] for c in data),
        "workers_installed": workers_installed,
        "delegations_today": delegations_today,
        "active_worker": active_worker,
        "brain": brain,
    }


# ===========================================================================
# Media & Integrations — TTS / STT / image / video / music backends.
#
# Hermes covers a lot of this natively (TTS providers incl. ElevenLabs; image
# backends fal/Krea/OpenAI/xAI/OpenRouter; STT). Others (PicLumen, Suno, video
# backends) need a custom plugin/skill. Each entry is labelled "native" vs
# "plugin" so the UI can show the distinction. This panel's job is to put
# detection + key entry in ONE place instead of scattered config.yaml + .env.
# ===========================================================================

MEDIA_CATALOG: List[Dict[str, Any]] = [
    # ── Voice / TTS ──────────────────────────────────────────────────────────
    {"id": "edge", "name": "Edge TTS (free)", "category": "Voice / TTS", "kind": "native",
     "mechanism": "Hermes default TTS — free, no key (verified)", "env": [], "keyless": True,
     "signup": "", "docs": "https://github.com/rany2/edge-tts"},
    {"id": "elevenlabs", "name": "ElevenLabs", "category": "Voice / TTS", "kind": "native",
     "mechanism": "TTS provider", "env": ["ELEVENLABS_API_KEY"],
     "signup": "https://elevenlabs.io/app/settings/api-keys", "docs": "https://elevenlabs.io"},
    {"id": "openai-tts", "name": "OpenAI TTS", "category": "Voice / TTS", "kind": "native",
     "mechanism": "TTS provider", "env": ["OPENAI_API_KEY"],
     "signup": "https://platform.openai.com/api-keys", "docs": "https://platform.openai.com/docs/guides/text-to-speech"},
    {"id": "cartesia", "name": "Cartesia", "category": "Voice / TTS", "kind": "native",
     "mechanism": "TTS provider", "env": ["CARTESIA_API_KEY"],
     "signup": "https://play.cartesia.ai/keys", "docs": "https://cartesia.ai"},
    {"id": "mistral-tts", "name": "Mistral Voxtral", "category": "Voice / TTS", "kind": "native",
     "mechanism": "TTS provider", "env": ["MISTRAL_API_KEY"],
     "signup": "https://console.mistral.ai/api-keys", "docs": "https://mistral.ai"},
    {"id": "piper", "name": "Piper (local)", "category": "Voice / TTS", "kind": "native",
     "mechanism": "local command TTS", "env": [], "bin": "piper",
     "signup": "", "docs": "https://github.com/rhasspy/piper"},
    {"id": "kokoro", "name": "Kokoro (local)", "category": "Voice / TTS", "kind": "native",
     "mechanism": "local command TTS", "env": [], "bin": "kokoro",
     "signup": "", "docs": "https://github.com/hexgrad/kokoro"},
    # ── Speech-to-text ───────────────────────────────────────────────────────
    {"id": "whisper-openai", "name": "OpenAI Whisper", "category": "Speech-to-Text", "kind": "native",
     "mechanism": "STT", "env": ["OPENAI_API_KEY"],
     "signup": "https://platform.openai.com/api-keys", "docs": "https://platform.openai.com/docs/guides/speech-to-text"},
    {"id": "whisper-local", "name": "faster-whisper (local)", "category": "Speech-to-Text", "kind": "native",
     "mechanism": "Hermes default STT — free, no key (verified)", "env": [], "module": "faster_whisper",
     "signup": "", "docs": "https://github.com/SYSTRAN/faster-whisper"},
    # ── Image generation ─────────────────────────────────────────────────────
    {"id": "pollinations", "name": "Pollinations (free)", "category": "Image", "kind": "plugin",
     "mechanism": "image_gen plugin — free, no key (verified, bundled with this tool)",
     "env": [], "keyless": True, "signup": "", "docs": "https://pollinations.ai"},
    {"id": "fal", "name": "fal.ai", "category": "Image", "kind": "native",
     "mechanism": "image_gen plugin", "env": ["FAL_KEY"],
     "signup": "https://fal.ai/dashboard/keys", "docs": "https://fal.ai"},
    {"id": "krea", "name": "Krea", "category": "Image", "kind": "native",
     "mechanism": "image_gen plugin", "env": ["KREA_API_KEY"],
     "signup": "https://www.krea.ai/settings/api", "docs": "https://krea.ai"},
    {"id": "openai-image", "name": "OpenAI (gpt-image)", "category": "Image", "kind": "native",
     "mechanism": "image_gen plugin", "env": ["OPENAI_API_KEY"],
     "signup": "https://platform.openai.com/api-keys", "docs": "https://platform.openai.com/docs/guides/images"},
    {"id": "xai-image", "name": "xAI Grok Image", "category": "Image", "kind": "native",
     "mechanism": "image_gen plugin", "env": ["XAI_API_KEY"],
     "signup": "https://console.x.ai", "docs": "https://x.ai"},
    {"id": "openrouter-image", "name": "OpenRouter Image", "category": "Image", "kind": "native",
     "mechanism": "image_gen plugin", "env": ["OPENROUTER_API_KEY"],
     "signup": "https://openrouter.ai/keys", "docs": "https://openrouter.ai"},
    {"id": "piclumen", "name": "PicLumen", "category": "Image", "kind": "plugin",
     "mechanism": "needs image_gen plugin (not bundled)", "env": ["PICLUMEN_API_KEY"],
     "signup": "https://www.piclumen.com", "docs": "https://www.piclumen.com"},
    # ── Video generation — many providers; stack their free trials/quotas ─────
    # All key-configurable here. Actual generation needs a per-provider Hermes
    # video_gen backend (pollinations image is the proven pattern); the backend
    # is written + verified once a key for that provider is available.
    {"id": "fal-video", "name": "fal Video", "category": "Video", "kind": "plugin",
     "mechanism": "key-configurable — free trial credits", "env": ["FAL_KEY"],
     "signup": "https://fal.ai/dashboard/keys", "docs": "https://fal.ai/models?categories=text-to-video"},
    {"id": "replicate-video", "name": "Replicate (video)", "category": "Video", "kind": "plugin",
     "mechanism": "many hosted video models — free trial", "env": ["REPLICATE_API_TOKEN"],
     "signup": "https://replicate.com/account/api-tokens", "docs": "https://replicate.com/collections/text-to-video"},
    {"id": "runway", "name": "Runway Gen-3", "category": "Video", "kind": "plugin",
     "mechanism": "key-configurable — free trial credits", "env": ["RUNWAYML_API_SECRET"],
     "signup": "https://dev.runwayml.com", "docs": "https://runwayml.com"},
    {"id": "luma", "name": "Luma Dream Machine", "category": "Video", "kind": "plugin",
     "mechanism": "key-configurable — free monthly quota", "env": ["LUMAAI_API_KEY"],
     "signup": "https://lumalabs.ai/dream-machine/api", "docs": "https://lumalabs.ai"},
    {"id": "kling", "name": "Kling (PiAPI)", "category": "Video", "kind": "plugin",
     "mechanism": "key-configurable — free daily credits", "env": ["PIAPI_KEY"],
     "signup": "https://piapi.ai/workspace", "docs": "https://piapi.ai/kling-api"},
    {"id": "minimax-video", "name": "MiniMax / Hailuo", "category": "Video", "kind": "plugin",
     "mechanism": "key-configurable — free credits", "env": ["MINIMAX_API_KEY"],
     "signup": "https://www.minimax.io/platform", "docs": "https://www.minimax.io"},
    {"id": "pika", "name": "Pika", "category": "Video", "kind": "plugin",
     "mechanism": "key-configurable", "env": ["PIKA_API_KEY"],
     "signup": "https://pika.art", "docs": "https://pika.art"},
    {"id": "haiper", "name": "Haiper", "category": "Video", "kind": "plugin",
     "mechanism": "key-configurable — free tier", "env": ["HAIPER_API_KEY"],
     "signup": "https://haiper.ai", "docs": "https://haiper.ai"},
    {"id": "veo", "name": "Google Veo", "category": "Video", "kind": "plugin",
     "mechanism": "key-configurable (Gemini API)", "env": ["GEMINI_API_KEY"],
     "signup": "https://aistudio.google.com/apikey", "docs": "https://ai.google.dev/gemini-api/docs/video"},
    {"id": "sora", "name": "OpenAI Sora", "category": "Video", "kind": "plugin",
     "mechanism": "key-configurable (OpenAI API)", "env": ["OPENAI_API_KEY"],
     "signup": "https://platform.openai.com/api-keys", "docs": "https://platform.openai.com/docs/guides/video-generation"},
    # ── Music generation (key-based services) ────────────────────────────────
    {"id": "suno", "name": "Suno", "category": "Music", "kind": "plugin",
     "mechanism": "via third-party API key", "env": ["SUNO_API_KEY"],
     "signup": "https://sunoapi.org", "docs": "https://suno.com"},
    {"id": "udio", "name": "Udio", "category": "Music", "kind": "plugin",
     "mechanism": "via third-party API key", "env": ["UDIO_API_KEY"],
     "signup": "https://udioapi.pro", "docs": "https://udio.com"},
    {"id": "elevenlabs-music", "name": "ElevenLabs Music", "category": "Music", "kind": "plugin",
     "mechanism": "key-configurable", "env": ["ELEVENLABS_API_KEY"],
     "signup": "https://elevenlabs.io/app/settings/api-keys", "docs": "https://elevenlabs.io/music"},
    {"id": "stable-audio", "name": "Stable Audio", "category": "Music", "kind": "plugin",
     "mechanism": "key-configurable (Stability AI)", "env": ["STABILITY_API_KEY"],
     "signup": "https://platform.stability.ai/account/keys", "docs": "https://stability.ai/stable-audio"},
    {"id": "replicate-music", "name": "Replicate (MusicGen)", "category": "Music", "kind": "plugin",
     "mechanism": "hosted MusicGen — free trial", "env": ["REPLICATE_API_TOKEN"],
     "signup": "https://replicate.com/account/api-tokens", "docs": "https://replicate.com/meta/musicgen"},
    {"id": "mubert", "name": "Mubert", "category": "Music", "kind": "plugin",
     "mechanism": "key-configurable — free tier", "env": ["MUBERT_API_KEY"],
     "signup": "https://mubert.com/render/api", "docs": "https://mubert.com"},
    {"id": "loudly", "name": "Loudly", "category": "Music", "kind": "plugin",
     "mechanism": "key-configurable — free tier", "env": ["LOUDLY_API_KEY"],
     "signup": "https://www.loudly.com/developers", "docs": "https://www.loudly.com"},
    {"id": "beatoven", "name": "Beatoven.ai", "category": "Music", "kind": "plugin",
     "mechanism": "key-configurable", "env": ["BEATOVEN_API_KEY"],
     "signup": "https://www.beatoven.ai", "docs": "https://www.beatoven.ai"},
]

_MEDIA_ENV_KEYS = {e for m in MEDIA_CATALOG for e in m.get("env", [])}


def _env_file() -> Path:
    return hermes_home() / ".env"


def _read_env_file() -> Dict[str, str]:
    out: Dict[str, str] = {}
    f = _env_file()
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _key_present(env_name: str, env_file: Dict[str, str]) -> bool:
    return bool(os.environ.get(env_name)) or bool(env_file.get(env_name))


@router.get("/media/scan")
async def media_scan():
    """Detect every media backend: configured (key present / local bin) + label."""
    env_file = _read_env_file()
    rows: List[Dict[str, Any]] = []
    for m in MEDIA_CATALOG:
        envs = m.get("env", [])
        if m.get("module"):  # local python backend — configured if importable
            import importlib.util as _il
            configured = _il.find_spec(m["module"]) is not None
        elif m.get("keyless"):
            configured = True  # free, no key required — works out of the box
        elif envs:
            configured = all(_key_present(e, env_file) for e in envs)
        else:  # local command backend
            configured = shutil.which(m.get("bin", "")) is not None if m.get("bin") else False
        rows.append({
            "id": m["id"], "name": m["name"], "category": m["category"],
            "kind": m["kind"], "mechanism": m["mechanism"],
            "env": envs, "needs_key": bool(envs),
            "configured": configured,
            "signup": m.get("signup"), "docs": m.get("docs"),
        })
    return {"media": rows, "scanned_at": int(time.time())}


class MediaKeyBody(BaseModel):
    env: str
    value: str


@router.post("/media/key")
async def media_set_key(body: MediaKeyBody):
    """Save an API key into <HERMES_HOME>/.env (chmod 600). Only env names
    declared in MEDIA_CATALOG are accepted — no arbitrary env writes."""
    key = body.env.strip()
    if key not in _MEDIA_ENV_KEYS:
        raise HTTPException(400, f"Unknown / disallowed env key: {key}")
    value = body.value.strip()
    if not value:
        raise HTTPException(400, "Empty value")
    f = _env_file()
    lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except Exception:
        pass
    return {"ok": True, "env": key, "saved": True}  # never echo the value


# ===========================================================================
# Model Governor — free-provider registry, fallback chain, limits & cooldowns.
#
# This is the heart of the "governor": know every free/cheap model backend,
# where to get a key, whether it's authenticated, its place in the fallback
# chain, and — critically — when an exhausted provider becomes callable again
# (e.g. "Codex — retry in 28d"). Metadata is curated from Hermes's own provider
# profiles so it's accurate, and refreshable.
# ===========================================================================

# tier: free | subscription | trial | cheap | local
PROVIDERS_CATALOG: List[Dict[str, Any]] = [
    {"id": "gemini", "name": "Google Gemini", "tier": "free", "auth": "api_key",
     "env": "GEMINI_API_KEY", "model": "gemini-3.5-flash",
     "signup": "https://aistudio.google.com/apikey",
     "limit": "Free AI Studio tier (per-min + daily caps)"},
    {"id": "qwen-oauth", "name": "Qwen (OAuth)", "tier": "free", "auth": "oauth",
     "env": "", "model": "qwen3-coder-plus", "signup": "https://chat.qwen.ai",
     "limit": "~2000 requests/day (free OAuth)"},
    {"id": "openrouter", "name": "OpenRouter (:free models)", "tier": "free", "auth": "api_key",
     "env": "OPENROUTER_API_KEY", "model": "deepseek/deepseek-chat-v3.1:free",
     "signup": "https://openrouter.ai/keys",
     "limit": "Free `:free` model variants, rate-limited"},
    {"id": "huggingface", "name": "Hugging Face Inference", "tier": "free", "auth": "api_key",
     "env": "HF_TOKEN", "model": "Qwen/Qwen3.5-72B-Instruct",
     "signup": "https://huggingface.co/settings/tokens",
     "limit": "Free inference API, rate-limited"},
    {"id": "zai", "name": "Z.ai / GLM", "tier": "free", "auth": "api_key",
     "env": "GLM_API_KEY", "model": "glm-4-9b", "signup": "https://z.ai/",
     "limit": "Free GLM-flash tier"},
    {"id": "nvidia", "name": "NVIDIA NIM", "tier": "trial", "auth": "api_key",
     "env": "NVIDIA_API_KEY", "model": "nvidia/llama-3.1-nemotron-70b-instruct",
     "signup": "https://build.nvidia.com/", "limit": "Free credits on signup"},
    {"id": "novita", "name": "Novita", "tier": "trial", "auth": "api_key",
     "env": "NOVITA_API_KEY", "model": "moonshotai/kimi-k2.5",
     "signup": "https://novita.ai/settings/key-management", "limit": "Free credits on signup"},
    {"id": "gmi", "name": "GMI Cloud", "tier": "trial", "auth": "api_key",
     "env": "GMI_API_KEY", "model": "zai-org/GLM-5.1-FP8",
     "signup": "https://www.gmicloud.ai/", "limit": "Free credits"},
    {"id": "nous", "name": "Nous Research", "tier": "free", "auth": "oauth",
     "env": "NOUS_API_KEY", "model": "hermes-3-70b",
     "signup": "https://nousresearch.com/", "limit": "Nous Portal tier"},
    {"id": "openai-codex", "name": "OpenAI Codex (ChatGPT)", "tier": "subscription", "auth": "oauth",
     "env": "", "model": "gpt-5.5", "signup": "https://chatgpt.com",
     "limit": "ChatGPT plan usage cap (weekly/5h)"},
    {"id": "copilot", "name": "GitHub Copilot", "tier": "subscription", "auth": "oauth",
     "env": "GITHUB_TOKEN", "model": "gpt-5.4",
     "signup": "https://github.com/features/copilot", "limit": "Copilot plan limits"},
    {"id": "deepseek", "name": "DeepSeek", "tier": "cheap", "auth": "api_key",
     "env": "DEEPSEEK_API_KEY", "model": "deepseek-chat",
     "signup": "https://platform.deepseek.com/", "limit": "Very cheap (not free)"},
    {"id": "custom", "name": "Local (Ollama)", "tier": "local", "auth": "none",
     "env": "", "model": "qwen3.5:latest", "signup": "https://ollama.com",
     "limit": "Local — free, hardware-bound"},
]


def _config_path() -> Path:
    return hermes_home() / "config.yaml"


def _read_config() -> Dict[str, Any]:
    try:
        import yaml
        return yaml.safe_load(open(_config_path())) or {}
    except Exception:
        return {}


def _write_config(cfg: Dict[str, Any]) -> None:
    import yaml
    f = _config_path()
    tmp = f.with_suffix(".yaml.tmp")
    with open(tmp, "w") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False)
    tmp.replace(f)


def _oauth_authed(provider: str) -> bool:
    """Best-effort: is an oauth provider logged in? Check the Hermes auth store."""
    try:
        data = json.loads((hermes_home() / "auth.json").read_text(encoding="utf-8"))
        blob = json.dumps(data)
        return provider in blob or provider.replace("-", "_") in blob
    except Exception:
        return False


def _chain_positions() -> Dict[str, str]:
    """Map provider -> 'primary' | 'fallback #N' from config."""
    cfg = _read_config()
    out: Dict[str, str] = {}
    m = cfg.get("model") or {}
    if isinstance(m, dict) and m.get("provider"):
        out[str(m["provider"])] = "primary"
    for i, f in enumerate(cfg.get("fallback_providers") or [], start=1):
        if f.get("provider") and str(f["provider"]) not in out:
            out[str(f["provider"])] = f"fallback #{i}"
    return out


@router.get("/providers/scan")
async def providers_scan():
    """Every free/cheap model backend: tier, get-key link, auth status, chain
    position, and cooldown (when an exhausted one is callable again)."""
    env_file = _read_env_file()
    positions = _chain_positions()
    cooldowns = read_state().get("cooldowns", {})
    now = time.time()
    rows: List[Dict[str, Any]] = []
    for p in PROVIDERS_CATALOG:
        auth = p["auth"]
        if auth == "api_key":
            authed = _key_present(p["env"], env_file) if p.get("env") else False
        elif auth == "oauth":
            authed = _oauth_authed(p["id"])
        elif auth == "none":
            authed = True
        else:
            authed = False
        cd = cooldowns.get(p["id"])
        cooling = bool(cd and cd.get("until", 0) > now)
        rows.append({
            **{k: p[k] for k in ("id", "name", "tier", "auth", "env", "model", "signup", "limit")},
            "authed": authed,
            "position": positions.get(p["id"]),  # primary / fallback #N / None
            "cooling_down": cooling,
            "cooldown_until": cd.get("until") if cd else None,
            "cooldown_reason": cd.get("reason") if cd else None,
            "cooldown_remaining_s": int(cd["until"] - now) if cooling else 0,
        })
    # sort: free first, then trial, cheap, subscription, local
    order = {"free": 0, "trial": 1, "cheap": 2, "subscription": 3, "local": 4}
    rows.sort(key=lambda r: (order.get(r["tier"], 9), r["name"]))
    return {"providers": rows, "scanned_at": int(now)}


class ChainEntry(BaseModel):
    provider: str
    model: str
    base_url: Optional[str] = None


class ChainBody(BaseModel):
    fallback: List[ChainEntry]


@router.get("/providers/chain")
async def get_chain():
    cfg = _read_config()
    return {"primary": cfg.get("model"), "fallback": cfg.get("fallback_providers") or []}


@router.post("/providers/chain")
async def set_chain(body: ChainBody):
    """Replace the fallback chain (primary is set separately via `hermes model`)."""
    cfg = _read_config()
    chain = []
    for e in body.fallback:
        entry = {"provider": e.provider, "model": e.model}
        if e.base_url:
            entry["base_url"] = e.base_url
        elif e.provider in ("custom", "ollama"):
            entry["base_url"] = "http://localhost:11434/v1"
        chain.append(entry)
    cfg["fallback_providers"] = chain
    _write_config(cfg)
    return {"ok": True, "fallback": chain}


class CooldownBody(BaseModel):
    provider: str
    seconds: int
    reason: Optional[str] = ""


@router.post("/providers/cooldown")
async def set_cooldown(body: CooldownBody):
    """Record that a provider is exhausted until now+seconds (e.g. a 429
    retry-after). The governor uses this to skip + auto-restore it."""
    st = read_state()
    st.setdefault("cooldowns", {})[body.provider] = {
        "until": time.time() + max(0, int(body.seconds)),
        "reason": body.reason or "rate limit",
        "set_at": time.time(),
    }
    write_state(st)
    return {"ok": True, "provider": body.provider, "until": st["cooldowns"][body.provider]["until"]}


@router.post("/providers/cooldown/clear")
async def clear_cooldown(body: CooldownBody):
    st = read_state()
    if body.provider in st.get("cooldowns", {}):
        del st["cooldowns"][body.provider]
        write_state(st)
    return {"ok": True, "provider": body.provider}
