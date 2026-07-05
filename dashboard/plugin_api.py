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

import asyncio
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

PLUGIN_ID = "cli-orchestrator"

PROVIDER_KEY_ENV: Dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "copilot": "GITHUB_TOKEN",
    "openrouter": "OPENROUTER_API_KEY",
    "huggingface": "HF_TOKEN",
    "zai": "GLM_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "custom": "",
    "openai-codex": "",
    "qwen-oauth": "",
}

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
        "auth_command": "claude login",
        "auth_hint": "Run Claude Code login once; subscription auth is stored under ~/.claude.",
        "install": {"npm": "npm install -g @anthropic-ai/claude-code"},
        "provider": "anthropic", "plan": "Subscription (Claude Pro/Max)",
        "docs": "https://docs.claude.com/en/docs/claude-code",
    },
    {
        "id": "codex", "name": "OpenAI Codex CLI", "category": "AI Coding",
        "bin": "codex", "version_args": ["--version"],
        "auth": {"file": "~/.codex/auth.json"},
        "auth_command": "codex login",
        "auth_hint": "Run Codex login once; ChatGPT subscription auth is stored under ~/.codex.",
        "install": {"npm": "npm install -g @openai/codex", "brew": "brew install codex"},
        "provider": "openai-codex", "plan": "Subscription (ChatGPT Plus/Pro)",
        "docs": "https://github.com/openai/codex",
        "verified": True,  # confirmed working as delegation worker + install assist
    },
    {
        # The Antigravity CLI is the headless terminal agent, binary `agy`
        # (distinct from the IDE below). Non-interactive via `agy -p "..."`.
        "id": "antigravity", "name": "Antigravity CLI (agy)", "category": "AI Coding",
        "bin": "agy",
        "version_args": ["--version"], "auth": None,
        "auth_command": "agy",
        "auth_hint": "The first `agy` run signs you in via your system keyring / Google Sign-In. Use Check sign-in to test it.",
        "install": {
            "script": "curl -fsSL https://antigravity.google/cli/install.sh | bash",
            "windows-powershell": "irm https://antigravity.google/cli/install.ps1 | iex",
            "windows-cmd": "curl -fsSL https://antigravity.google/cli/install.cmd -o install.cmd && install.cmd && del install.cmd",
        },
        "install_meta": {
            "script": {"label": "macOS / Linux", "platforms": ["mac", "linux"]},
            "windows-powershell": {"label": "Windows PowerShell", "platforms": ["windows"]},
            "windows-cmd": {"label": "Windows CMD", "platforms": ["windows"]},
        },
        "provider": "gemini", "plan": "Google Antigravity",
        "docs": "https://antigravity.google/docs/cli",
    },
    {
        # The Antigravity IDE is the GUI editor (a VS Code fork). Its `code`-style
        # launcher opens the app; sign-in happens in the app, so it's "Open app".
        "id": "antigravity-ide", "name": "Antigravity IDE", "category": "AI IDE",
        "bin": "antigravity-ide", "version_args": ["--version"], "auth": None,
        "auth_hint": "The IDE signs you in through the app. Use Open app to launch it, then sign in there.",
        "install": {"script": "curl -fsSL https://antigravity.google/cli/install.sh | bash"},
        "install_meta": {"script": {"label": "macOS / Linux", "platforms": ["mac", "linux"]}},
        "provider": "gemini", "plan": "Google Antigravity (IDE)",
        "docs": "https://antigravity.google",
        "hide_when_missing": True,
    },
    {
        "id": "gemini", "name": "Gemini CLI (legacy)", "category": "AI Coding",
        "bin": "gemini", "version_args": ["--version"],
        "auth": {"file": "~/.gemini/oauth_creds.json"},
        "auth_command": "gemini",
        "auth_hint": "Consumer/free and Google AI Pro/Ultra Gemini CLI requests stop on June 18, 2026. Keep only for Enterprise/API-key access; use Antigravity CLI otherwise.",
        "install": {"npm": "npm install -g @google/gemini-cli"},
        "provider": "gemini", "plan": "Legacy / Enterprise or API key",
        "hide_when_missing": True,
        "deprecated": True,
        "docs": "https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/",
    },
    {
        "id": "qwen", "name": "Qwen Code", "category": "AI Coding",
        "bin": "qwen", "version_args": ["--version"], "auth": None,
        "auth_command": "qwen",
        "auth_hint": "Start Qwen Code and complete its OAuth/login flow if it prompts.",
        "install": {"npm": "npm install -g @qwen-code/qwen-code"},
        "provider": "qwen-oauth", "plan": "Free OAuth (Qwen, ~2k req/day)",
        "docs": "https://github.com/QwenLM/qwen-code",
    },
    {
        "id": "copilot", "name": "GitHub Copilot CLI", "category": "AI Coding",
        "bin": "copilot", "version_args": ["--version"], "auth": None,
        "auth_hint": "Requires GitHub/Copilot authentication. Use GitHub auth if the CLI prompts.",
        "install": {"npm": "npm install -g @github/copilot"},
        "provider": "copilot", "plan": "Subscription (GitHub Copilot)",
        "docs": "https://github.com/github/copilot-cli",
    },
    {
        "id": "opencode", "name": "OpenCode", "category": "AI Coding",
        "bin": "opencode", "version_args": ["--version"], "auth": None,
        "auth_hint": "Configure provider credentials used by OpenCode before routing work here.",
        "install": {"npm": "npm install -g opencode-ai", "brew": "brew install sst/tap/opencode"},
        "provider": "opencode-zen", "plan": "BYO key / free models",
        "docs": "https://github.com/sst/opencode",
        "verified": True,  # confirmed working as a governed worker (opencode run)
    },
    {
        "id": "cursor-agent", "name": "Cursor CLI", "category": "AI Coding",
        "bin": "cursor-agent", "version_args": ["--version"], "auth": None,
        "auth_command": "cursor-agent login",
        "auth_hint": "Sign in with `cursor-agent login` (opens a browser), then Check sign-in.",
        "install": {"script": "curl https://cursor.com/install -fsS | bash"},
        "provider": None, "plan": "Subscription (Cursor)",
        "docs": "https://docs.cursor.com/en/cli/overview",
    },
    {
        "id": "amp", "name": "Amp (Sourcegraph)", "category": "AI Coding",
        "bin": "amp", "version_args": ["--version"], "auth": None,
        "auth_command": "amp login",
        "auth_hint": "Sign in with `amp login` (opens a browser), then Check sign-in.",
        "install": {"npm": "npm install -g @sourcegraph/amp"},
        "provider": None, "plan": "Free credits (Sourcegraph)",
        "docs": "https://ampcode.com",
    },
    {
        "id": "crush", "name": "Crush (Charm)", "category": "AI Coding",
        "bin": "crush", "version_args": ["--version"], "auth": None,
        "auth_command": "crush login",
        "auth_hint": "Sign in with `crush login` (or configure a provider key), then Check sign-in.",
        "install": {"npm": "npm install -g @charmland/crush",
                    "brew": "brew install charmbracelet/tap/crush"},
        "provider": None, "plan": "BYO key (multi-provider)",
        "docs": "https://github.com/charmbracelet/crush",
    },
    {
        "id": "goose", "name": "Goose (Block)", "category": "AI Coding",
        "bin": "goose", "version_args": ["--version"], "auth": None,
        "auth_hint": "Configure Goose/provider credentials before routing work here.",
        "install": {"script": "curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh | bash"},
        "provider": None, "plan": "BYO key (Block)",
        "docs": "https://block.github.io/goose/",
    },
    # aider removed — interactive, git-repo-centric pair-programmer, not a
    # clean stateless delegation worker for this plugin's use case. See the
    # catalog-refresh skill for the pruning criteria.
    # ── AI tools / chat ───────────────────────────────────────────────────────
    {
        "id": "mods", "name": "Mods (Charm)", "category": "AI Tools",
        "bin": "mods", "version_args": ["--version"], "auth": None,
        "auth_hint": "Configure Mods with the provider key it should use.",
        "install": {"brew": "brew install charmbracelet/tap/mods",
                    "go": "go install github.com/charmbracelet/mods@latest"},
        "provider": None, "plan": "BYO key",
        "docs": "https://github.com/charmbracelet/mods",
    },
    {
        "id": "llm", "name": "llm (Datasette)", "category": "AI Tools",
        "bin": "llm", "version_args": ["--version"], "auth": None,
        "auth_hint": "Configure llm keys with its native key store or add provider env keys here.",
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
        "auth_command": "gh auth login",
        "auth_hint": "Run GitHub auth login after installation.",
        "install": {"brew": "brew install gh"},
        "provider": None, "plan": "Free (GitHub account)",
        "docs": "https://cli.github.com",
    },
    {
        "id": "glab", "name": "GitLab CLI", "category": "Version Control",
        "bin": "glab", "version_args": ["--version"],
        "auth": {"cmd": ["glab", "auth", "status"]},
        "auth_command": "glab auth login",
        "auth_hint": "Run GitLab auth login after installation.",
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
        "hide_when_missing": True,
        "docs": "https://ollama.com",
        "verified": True,  # confirmed working as free local fallback brain
    },
]
# NOTE: `hermes` (the host agent) is NOT catalogued — it's not a governed worker.
# `ollama` stays but is a brain, surfaced primarily as the `custom` provider.


# Delegation-capable worker CLIs, in fallback priority order (mirrors
# __init__.py DELEGATE_ARGV + CODING_PRIORITY). Used to mark "workers" in scan.
_DELEGATE_PRIORITY = ["codex", "claude", "qwen", "opencode", "antigravity",
                      "cursor-agent", "amp", "crush"]

# Non-interactive argv for each worker CLI (mirrors __init__.py DELEGATE_ARGV).
# Used by /install/assist to answer install questions through a governed worker
# instead of a paid API — the same CLIs this plugin already orchestrates.
_ASSIST_ARGV = {
    "codex": lambda p: ["codex", "exec", "--skip-git-repo-check", p],
    "claude": lambda p: ["claude", "-p", p],
    "qwen": lambda p: ["qwen", "-p", p],
    "opencode": lambda p: ["opencode", "run", p],
}

# Live sign-in test per CLI: a tiny non-interactive call. If it returns output
# without an auth error, the CLI is signed in and working. `b` is the resolved
# binary. Non-AI CLIs (gh/glab) use their own `auth status`.
_CLI_PING = "Reply with exactly the word: PONG"
_CLI_TEST_ARGV = {
    "codex": lambda b: [b, "exec", "--skip-git-repo-check", _CLI_PING],
    "opencode": lambda b: [b, "run", _CLI_PING],
    "qwen": lambda b: [b, "-p", _CLI_PING],
    "claude": lambda b: [b, "-p", _CLI_PING],
    "gemini": lambda b: [b, "-p", _CLI_PING],
    "antigravity": lambda b: [b, "-p", _CLI_PING],  # bin resolves to `agy`
    "cursor-agent": lambda b: [b, "-p", _CLI_PING],
    "amp": lambda b: [b, "-x", _CLI_PING],
    "crush": lambda b: [b, "run", _CLI_PING],
    "gh": lambda b: [b, "auth", "status"],
    "glab": lambda b: [b, "auth", "status"],
}
# CLIs that are GUI/IDE launchers — no headless AI call; the UI offers "Open app".
_GUI_CLIS = {"antigravity-ide"}
# Substrings that mean "not signed in" in a test's output.
_AUTH_FAIL_PAT = (
    "not logged in", "please log in", "log in to", "sign in", "signin",
    "unauthorized", "not authenticated", "authentication required", "login required",
    "no api key", "invalid api key", "missing api key", "401", "token expired",
    "please authenticate", "run `", "ineligible", "no longer supported",
)

# Per-package-manager prerequisite so the UI can show "Step 1: install X first".
_MANAGER_PREREQ = {
    "npm": {"label": "Node.js + npm", "check": "node --version", "bin": "npm",
            "get": "https://nodejs.org"},
    "brew": {"label": "Homebrew", "check": "brew --version", "bin": "brew",
             "get": "https://brew.sh"},
    "pipx": {"label": "pipx", "check": "pipx --version", "bin": "pipx",
             "get": "https://pipx.pypa.io"},
    "uv": {"label": "uv", "check": "uv --version", "bin": "uv",
           "get": "https://docs.astral.sh/uv/"},
    "go": {"label": "Go toolchain", "check": "go version", "bin": "go",
           "get": "https://go.dev/dl/"},
    "script": {"label": "curl + bash", "check": "curl --version", "bin": "curl",
               "get": ""},
    "windows-powershell": {"label": "PowerShell", "check": "", "bin": "", "get": ""},
    "windows-cmd": {"label": "Windows cmd", "check": "", "bin": "", "get": ""},
}


def load_catalog() -> List[Dict[str, Any]]:
    """Default catalog merged with an optional user catalog.json (by id).

    Entries whose id is not part of the built-in catalog are tagged
    ``_user_added`` so the UI can label them "custom" vs "catalog"."""
    default_ids = {c["id"] for c in DEFAULT_CATALOG}
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
    for cid, entry in catalog.items():
        entry["_user_added"] = cid not in default_ids
    return list(catalog.values())


def _provenance(entry: Dict[str, Any], verified_set: set, target_key: str) -> str:
    """One of 'custom' (user-added), 'verified' (proven working / user-marked),
    or 'catalog' (known default, not yet verified here)."""
    if entry.get("_user_added"):
        return "custom"
    if entry.get("verified") or target_key in verified_set:
        return "verified"
    return "catalog"


# ---------------------------------------------------------------------------
# State (limits / routing / usage) — small JSON, read-modify-write.
# ---------------------------------------------------------------------------

USE_CASES: List[Dict[str, Any]] = [
    {
        "id": "coding",
        "name": "Coding",
        "intent": "code generation, refactor, debugging, tests, multi-file edits",
        "default_mode": "cli",
        "description": "Delegate heavy software work to local coding CLIs with cap-aware fallback.",
    },
    {
        "id": "chat",
        "name": "Chat",
        "intent": "general chat, summaries, reasoning, planning, answers",
        "default_mode": "model",
        "description": "Use the active model chain for normal assistant turns.",
    },
    {
        "id": "image",
        "name": "Image",
        "intent": "generate image, draw, create picture, text to image, image edit",
        "default_mode": "media",
        "description": "Route image requests to configured image backends instead of the default chat model.",
    },
    {
        "id": "audio",
        "name": "Audio",
        "intent": "voice, transcription, text to speech, speech to text, music, audio generation",
        "default_mode": "media",
        "description": "Route speech, voice, transcription, and music requests to audio-capable backends.",
    },
    {
        "id": "video",
        "name": "Video",
        "intent": "generate video, animate image, text to video, image to video",
        "default_mode": "media",
        "description": "Route video generation and animation requests to configured video backends.",
    },
    {
        "id": "research",
        "name": "Research",
        "intent": "web research, compare options, gather sources, summarize findings",
        "default_mode": "model",
        "description": "Use model providers or research-capable CLIs for source-heavy work.",
    },
    {
        "id": "docs",
        "name": "Docs",
        "intent": "write docs, summarize files, draft reports, edit documents",
        "default_mode": "model",
        "description": "Prefer model providers for writing, summarizing, and document-style tasks.",
    },
    {
        "id": "automation",
        "name": "Automation",
        "intent": "long running, batch, monitor, scheduled, autonomous, workflow",
        "default_mode": "cli",
        "description": "Use CLI workers for durable multi-step or background-style tasks.",
    },
    {
        "id": "other",
        "name": "Other",
        "intent": "anything else, custom tasks, etc",
        "default_mode": "model",
        "description": "Catch-all route for custom use cases not covered by the named groups.",
    },
]

_DEFAULT_STATE: Dict[str, Any] = {
    "limits": {},
    "routing": [],
    "use_case_routes": [],
    "show_custom_local": False,
    "usage": {},
}


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


def _limits(hourly: int, daily: int, monthly: int, source: str = "catalog") -> Dict[str, Any]:
    return {"hourly": hourly, "daily": daily, "monthly": monthly, "source": source}


def _default_limits_for_cli(c: Dict[str, Any]) -> Dict[str, Any]:
    cid = c.get("id", "")
    if cid == "qwen":
        return _limits(120, 1800, 50000)
    if cid in ("codex", "claude", "copilot"):
        return _limits(20, 120, 2500)
    if cid in ("gemini", "opencode"):
        return _limits(60, 500, 10000)
    if cid in ("ollama", "hermes"):
        return _limits(999, 9999, 999999)
    if cid in ("gh", "glab"):
        return _limits(1000, 5000, 100000)
    if c.get("category") == "AI Coding":
        return _limits(20, 100, 2000)
    if c.get("category") == "AI Tools":
        return _limits(60, 300, 5000)
    return _limits(100, 1000, 30000)


def _default_limits_for_provider(p: Dict[str, Any]) -> Dict[str, Any]:
    pid = p.get("id", "")
    tier = p.get("tier", "")
    if pid == "qwen-oauth":
        return _limits(120, 1800, 50000)
    if tier == "subscription":
        return _limits(20, 120, 2500)
    if tier == "free":
        return _limits(60, 500, 10000)
    if tier == "trial":
        return _limits(30, 200, 3000)
    if tier == "cheap":
        return _limits(120, 2000, 50000)
    if tier == "local":
        return _limits(999, 9999, 999999)
    return _limits(60, 500, 10000)


def _default_limits_for_media(m: Dict[str, Any]) -> Dict[str, Any]:
    cat = m.get("category", "")
    if m.get("keyless"):
        return _limits(120, 1000, 30000)
    if cat == "Image":
        return _limits(30, 200, 3000)
    if cat == "Video":
        return _limits(10, 50, 500)
    if cat == "Music":
        return _limits(10, 80, 1000)
    if cat in ("Voice / TTS", "Speech-to-Text"):
        return _limits(60, 500, 10000)
    return _limits(30, 200, 3000)


def _effective_limits(state: Dict[str, Any], key: str, defaults: Dict[str, Any],
                      legacy_key: Optional[str] = None) -> Dict[str, Any]:
    saved = state.get("limits", {}).get(key)
    if not isinstance(saved, dict) and legacy_key:
        saved = state.get("limits", {}).get(legacy_key)
    if not isinstance(saved, dict):
        return dict(defaults)
    out = dict(defaults)
    out.update({
        "hourly": int(saved.get("hourly", out["hourly"]) or out["hourly"]),
        "daily": int(saved.get("daily", out["daily"]) or out["daily"]),
        "monthly": int(saved.get("monthly", out["monthly"]) or out["monthly"]),
        "source": "custom",
    })
    return out


def _target_key(kind: str, raw_id: str) -> str:
    return f"{kind}:{raw_id}"


def _media_usage(media_id: str, category: str, state: Dict[str, Any]) -> Dict[str, int]:
    events: List[float] = list(state.get("usage", {}).get(_target_key("media", media_id), []))
    if category == "Image":
        events.extend(state.get("usage", {}).get("media:image", []))
    elif category == "Video":
        events.extend(state.get("usage", {}).get("media:video", []))
    elif category == "Music":
        events.extend(state.get("usage", {}).get("media:music", []))
    return _windows(events)


def _current_platform() -> str:
    name = platform.system().lower()
    if name == "darwin":
        return "mac"
    if name.startswith("win"):
        return "windows"
    if name == "linux":
        return "linux"
    return name or "unknown"


def _install_options(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    current = _current_platform()
    meta = entry.get("install_meta") or {}
    options: List[Dict[str, Any]] = []
    for key, cmd in (entry.get("install") or {}).items():
        item = meta.get(key, {})
        platforms = item.get("platforms") or ["mac", "linux", "windows"]
        prereq = _MANAGER_PREREQ.get(key, {})
        prereq_bin = prereq.get("bin") or ""
        options.append({
            "manager": key,
            "label": item.get("label") or key,
            "command": cmd,
            "platforms": platforms,
            "executable": current in platforms,
            "prereq": prereq.get("label") or "",
            "prereq_check": prereq.get("check") or "",
            "prereq_get": prereq.get("get") or "",
            # None = unknown (no probe); True/False = probed presence.
            "prereq_ok": (shutil.which(prereq_bin) is not None) if prereq_bin else None,
        })
    return options


def _resolve_bin(entry: Dict[str, Any]) -> Optional[str]:
    """Return the binary name that actually resolves on PATH for this entry,
    trying the primary ``bin`` then any ``alt_bins`` (some tools ship under a
    different command name, e.g. Antigravity installs as ``antigravity-ide``).
    Broken symlinks are skipped because shutil.which() ignores them."""
    for name in [entry.get("bin")] + list(entry.get("alt_bins") or []):
        if name and shutil.which(name):
            return name
    return None


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
    verified_set = set(state.get("verified", []))
    rows: List[Dict[str, Any]] = []
    for c in catalog:
        resolved = _resolve_bin(c)
        binary = resolved or c["bin"]
        path = shutil.which(binary) if resolved else None
        installed = path is not None
        if c.get("hide_when_missing") and not installed:
            continue
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
            "auth_command": c.get("auth_command"),
            "auth_hint": c.get("auth_hint"),
            "status": status,
            "provider": c.get("provider"),
            "provider_env": PROVIDER_KEY_ENV.get(c.get("provider") or "", ""),
            "plan": c.get("plan"),
            "deprecated": bool(c.get("deprecated")),
            "worker": c["id"] in _DELEGATE_PRIORITY,
            "worker_rank": (_DELEGATE_PRIORITY.index(c["id"]) + 1) if c["id"] in _DELEGATE_PRIORITY else None,
            "install_managers": list((c.get("install") or {}).keys()),
            "install_options": _install_options(c),
            "install_hint": c.get("install_hint"),
            "docs": c.get("docs"),
            "provenance": _provenance(c, verified_set, _target_key("cli", c["id"])),
            "user_added": bool(c.get("_user_added")),
            "verified_builtin": bool(c.get("verified")),
            "testable": c["id"] in _CLI_TEST_ARGV,   # can we run a live sign-in test?
            "gui": c["id"] in _GUI_CLIS,              # IDE/launcher → "Open app" instead
            "limits": _effective_limits(state, _target_key("cli", c["id"]), _default_limits_for_cli(c), c["id"]),
            "usage": _windows(usage.get(_target_key("cli", c["id"]), usage.get(c["id"], []))),
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
    key = body.id.strip()
    state.setdefault("limits", {})[key] = {
        "hourly": max(0, int(body.hourly)),
        "daily": max(0, int(body.daily)),
        "monthly": max(0, int(body.monthly)),
        "source": "custom",
    }
    write_state(state)
    return {"ok": True, "limits": state["limits"][key]}


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
    key = body.id.strip()
    events = usage.setdefault(key, [])
    now = time.time()
    events.append(now)
    # Trim anything older than ~40 days to keep the file small.
    usage[key] = [t for t in events if t >= now - 3456000]
    write_state(state)
    return {"ok": True, "usage": _windows(usage[key], now)}


class CustomLocalBody(BaseModel):
    enabled: bool = True


@router.post("/custom-local")
async def set_custom_local(body: CustomLocalBody):
    state = read_state()
    state["show_custom_local"] = bool(body.enabled)
    write_state(state)
    return {"ok": True, "show_custom_local": state["show_custom_local"]}


class VerifyBody(BaseModel):
    id: str            # target key, e.g. "cli:codex"
    verified: bool = True


@router.post("/verify-mark")
async def verify_mark(body: VerifyBody):
    """User-toggled 'verified' label. Stored as a set of target keys in state so
    it survives re-scans; catalog entries with a built-in verified flag are
    always verified regardless."""
    state = read_state()
    marks = set(state.get("verified", []))
    key = body.id.strip()
    if body.verified:
        marks.add(key)
    else:
        marks.discard(key)
    state["verified"] = sorted(marks)
    write_state(state)
    return {"ok": True, "verified": state["verified"]}


# Categories a backend may be enabled to serve (mirrors the UI use-case tabs,
# minus the "other" catch-all which is always available).
CAPABILITY_CATEGORIES = [
    "coding", "chat", "image", "audio", "video", "research", "docs", "automation",
]


class CapabilitiesBody(BaseModel):
    id: str                    # target key, e.g. "cli:codex"
    enabled: List[str] = []    # categories this backend is enabled to serve


@router.get("/capabilities")
async def get_capabilities():
    """Per-backend enabled categories, keyed by target id. Absent id → the UI
    falls back to that backend's sensible defaults."""
    return {"capabilities": read_state().get("capabilities", {})}


@router.post("/capabilities")
async def set_capabilities(body: CapabilitiesBody):
    """Set which categories a backend serves. Only enabled categories make it
    eligible in that category's routing tab."""
    state = read_state()
    key = body.id.strip()
    valid = [c for c in body.enabled if c in CAPABILITY_CATEGORIES]
    state.setdefault("capabilities", {})[key] = valid
    write_state(state)
    return {"ok": True, "capabilities": state["capabilities"][key]}


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
    option = next((o for o in _install_options(entry) if o["manager"] == manager), None)
    if option and not option["executable"]:
        platforms = ", ".join(option["platforms"])
        raise HTTPException(400, f"Installer '{manager}' is for {platforms}, not {_current_platform()}")

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


class AssistBody(BaseModel):
    id: str
    manager: Optional[str] = None
    question: Optional[str] = None
    log: Optional[str] = None


def _assist_path() -> str:
    """PATH augmented with common CLI install dirs, so worker resolution works
    even when the dashboard was launched with a minimal environment."""
    dirs = [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
    ]
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.exists():
        dirs.extend(str(p) for p in sorted(nvm.glob("*/bin")))
    return os.pathsep.join([os.environ.get("PATH", "")] + dirs)


def _pick_assist_worker(path: str) -> Optional[str]:
    """First installed worker CLI in priority order (the free 'AI help' brain)."""
    for w in _DELEGATE_PRIORITY:
        if w in _ASSIST_ARGV and shutil.which(w, path=path):
            return w
    return None


@router.post("/install/assist")
async def install_assist(body: AssistBody):
    """Answer an install/auth question by delegating to an installed worker CLI
    (codex/claude/qwen/opencode) — or Ollama as the free floor. This is the
    'get help from AI' action: it runs through the same governed CLIs this
    plugin orchestrates, so it stays free and needs no extra API key."""
    cat = {c["id"]: c for c in load_catalog()}
    entry = cat.get(body.id)
    if not entry:
        raise HTTPException(404, f"Unknown CLI id: {body.id}")

    opts = _install_options(entry)
    opt = next((o for o in opts if o["manager"] == body.manager), opts[0] if opts else None)
    cmd = opt["command"] if opt else ""
    plat = _current_platform()
    parts = [
        f"I'm installing the '{entry['name']}' command-line tool "
        f"(binary: {entry['bin']}) on {plat}.",
    ]
    if cmd:
        parts.append(f"The intended install command is: {cmd}")
    if entry.get("auth_command"):
        parts.append(f"After install it is authenticated with: {entry['auth_command']}")
    if body.log:
        parts.append("Here is the tail of the install log (may contain the error):\n"
                     + body.log[-1500:])
    parts.append(body.question.strip() if (body.question and body.question.strip())
                 else "Give me the exact, ordered shell steps to install and "
                      "authenticate it, plus fixes for the most common errors. "
                      "Be concise and command-first.")
    prompt = "\n\n".join(parts)

    path = _assist_path()
    worker = _pick_assist_worker(path)
    argv = None
    used = None
    if worker:
        argv = _ASSIST_ARGV[worker](prompt)
        used = worker
    elif shutil.which("ollama", path=path):
        model = os.environ.get("CLI_ASSIST_OLLAMA_MODEL", "qwen3.5:latest")
        argv = ["ollama", "run", model, prompt]
        used = f"ollama/{model}"

    if not argv:
        return {
            "ok": False,
            "worker": None,
            "text": "No worker CLI is installed to answer. Install one of "
                    "codex / claude / qwen / opencode (or Ollama for a free local "
                    "answer), then try again. Meanwhile, the steps and the setup "
                    "docs link on this card cover the standard path.",
        }

    env = dict(os.environ)
    env["PATH"] = path

    def _run() -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, timeout=90, env=env)

    try:
        # Run off the event loop so a slow CLI never freezes the whole dashboard.
        proc = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        return {"ok": False, "worker": used,
                "text": f"{used} took too long to answer (>90s). Try again, or "
                        "follow the written steps on this card."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "worker": used, "text": f"Could not run {used}: {exc}"}

    text = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if not text:
        text = f"{used} returned no output (exit {proc.returncode})."
    return {"ok": proc.returncode == 0, "worker": used, "text": text[-6000:]}


class CliActionBody(BaseModel):
    id: str


@router.post("/cli/test")
async def cli_test(body: CliActionBody):
    """Live sign-in test: run a tiny call through the CLI itself and read the
    result. On success, auto-mark the CLI verified. GUI/launcher CLIs can't be
    tested headlessly → status 'manual'."""
    cat = {c["id"]: c for c in load_catalog()}
    entry = cat.get(body.id)
    if not entry:
        raise HTTPException(404, f"Unknown CLI id: {body.id}")

    path = _assist_path()
    resolved = _resolve_bin(entry)
    if not (resolved and shutil.which(resolved, path=path)):
        return {"status": "error", "detail": f"{entry['name']} is not on PATH — install it first."}

    if body.id in _GUI_CLIS:
        return {
            "status": "manual",
            "detail": f"{entry['name']} is a GUI app — open it and sign in there, "
                      "then re-check. It can't be tested from a headless call.",
        }
    if body.id not in _CLI_TEST_ARGV:
        cmd = entry.get("auth_command")
        return {
            "status": "manual",
            "detail": ("No automatic test for this CLI. Sign in with: " + cmd)
                      if cmd else "No automatic sign-in test is available for this CLI.",
        }

    argv = _CLI_TEST_ARGV[body.id](resolved)
    env = dict(os.environ)
    env["PATH"] = path

    def _run() -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60,
                              env=env, stdin=subprocess.DEVNULL)

    try:
        proc = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        return {"status": "error", "detail": f"{entry['name']} test timed out (>60s)."}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": f"Could not run {entry['name']}: {exc}"}

    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    low = out.lower()
    failed = any(p in low for p in _AUTH_FAIL_PAT)

    if proc.returncode == 0 and out and not failed:
        # Signed in and working → record it as verified.
        state = read_state()
        marks = set(state.get("verified", []))
        marks.add(_target_key("cli", body.id))
        state["verified"] = sorted(marks)
        write_state(state)
        return {"status": "signed_in", "detail": out[:500]}
    if failed:
        return {"status": "needs_auth", "detail": out[:500]}
    return {"status": "error", "detail": out[:500] or f"exit {proc.returncode}, no output"}


@router.post("/cli/open")
async def cli_open(body: CliActionBody):
    """Launch a GUI/launcher CLI (e.g. Antigravity) so the user can sign in."""
    cat = {c["id"]: c for c in load_catalog()}
    entry = cat.get(body.id)
    if not entry:
        raise HTTPException(404, f"Unknown CLI id: {body.id}")
    path = _assist_path()
    resolved = _resolve_bin(entry)
    if not (resolved and shutil.which(resolved, path=path)):
        raise HTTPException(400, f"{entry['name']} is not on PATH")
    env = dict(os.environ)
    env["PATH"] = path
    try:
        subprocess.Popen([resolved], env=env, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Could not launch {entry['name']}: {exc}")
    return {"ok": True, "launched": resolved}


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


def _provider_usage(provider_id: str, state: Dict[str, Any]) -> Dict[str, int]:
    events: List[float] = list(state.get("usage", {}).get(_target_key("provider", provider_id), []))
    prov = _model_provider_map()
    for model, model_events in state.get("model_usage", {}).items():
        if prov.get(model) == provider_id and isinstance(model_events, list):
            events.extend(model_events)
    return _windows(events)


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
     "mechanism": "Hermes default TTS — free, no key", "env": [], "module": "edge_tts",
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
    return bool(os.environ.get(env_name)) or bool(env_file.get(env_name)) or any(
        k.startswith(f"{env_name}_") and bool(v) for k, v in env_file.items()
    )


def _key_count(env_name: str, env_file: Dict[str, str]) -> int:
    values: List[str] = []
    raw_values = [os.environ.get(env_name), env_file.get(env_name)]
    raw_values.extend(v for k, v in env_file.items() if k.startswith(f"{env_name}_"))
    for raw in raw_values:
        if not raw:
            continue
        values.append(str(raw).strip())
    return len([p for p in values if p])


def _write_env_value(key: str, value: str, append: bool = False) -> None:
    f = _env_file()
    lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    existing_keys = {
        line.split("=", 1)[0].strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }
    if append and key in existing_keys:
        idx = 2
        while f"{key}_{idx}" in existing_keys:
            idx += 1
        lines.append(f"{key}_{idx}={value}")
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(f, 0o600)
        except Exception:
            pass
        return
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().split("=", 1)[0].strip() == key:
            if append and line.split("=", 1)[1].strip():
                existing = line.split("=", 1)[1].strip()
                lines[i] = f"{key}={existing},{value}"
            else:
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


@router.get("/media/scan")
async def media_scan():
    """Detect every media backend: configured (key present / local bin) + label."""
    env_file = _read_env_file()
    state = read_state()
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
            "key_count": sum(_key_count(e, env_file) for e in envs),
            "limits": _effective_limits(state, _target_key("media", m["id"]), _default_limits_for_media(m), m["id"]),
            "usage": _media_usage(m["id"], m["category"], state),
            "configured": configured,
            "signup": m.get("signup"), "docs": m.get("docs"),
        })
    return {"media": rows, "scanned_at": int(time.time())}


class MediaKeyBody(BaseModel):
    env: str
    value: str
    append: bool = False


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
    _write_env_value(key, value, append=body.append)
    env_file = _read_env_file()
    return {"ok": True, "env": key, "saved": True, "key_count": _key_count(key, env_file)}  # never echo the value


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

_PROVIDER_ENV_KEYS = {p.get("env") for p in PROVIDERS_CATALOG if p.get("env")}


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
    state = read_state()
    cooldowns = state.get("cooldowns", {})
    show_custom_local = bool(state.get("show_custom_local"))
    ollama_detected = shutil.which("ollama") is not None
    now = time.time()
    rows: List[Dict[str, Any]] = []
    for p in PROVIDERS_CATALOG:
        if p["id"] == "custom" and not (show_custom_local or ollama_detected or positions.get("custom")):
            continue
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
            "key_count": _key_count(p["env"], env_file) if p.get("env") else 0,
            "limits": _effective_limits(state, _target_key("provider", p["id"]), _default_limits_for_provider(p), p["id"]),
            "usage": _provider_usage(p["id"], state),
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


class ProviderKeyBody(BaseModel):
    env: str
    value: str
    append: bool = False


@router.post("/providers/key")
async def provider_set_key(body: ProviderKeyBody):
    key = body.env.strip()
    if key not in _PROVIDER_ENV_KEYS:
        raise HTTPException(400, f"Unknown / disallowed provider env key: {key}")
    value = body.value.strip()
    if not value:
        raise HTTPException(400, "Empty value")
    _write_env_value(key, value, append=body.append)
    env_file = _read_env_file()
    return {"ok": True, "env": key, "saved": True, "key_count": _key_count(key, env_file)}


class UseCaseRoute(BaseModel):
    use_case: str
    mode: str
    target: str
    fallback: Optional[str] = ""
    enabled: bool = True


class UseCaseRoutesBody(BaseModel):
    routes: List[UseCaseRoute]


@router.get("/use-cases")
async def get_use_cases():
    state = read_state()
    return {
        "use_cases": USE_CASES,
        "routes": state.get("use_case_routes", []),
        "show_custom_local": bool(state.get("show_custom_local")),
    }


@router.post("/use-cases")
async def set_use_cases(body: UseCaseRoutesBody):
    known = {u["id"] for u in USE_CASES}
    clean = []
    for r in body.routes:
        uc = r.use_case.strip()
        if uc not in known or not r.target.strip():
            continue
        clean.append({
            "use_case": uc,
            "mode": (r.mode or "auto").strip(),
            "target": r.target.strip(),
            "fallback": (r.fallback or "").strip(),
            "enabled": bool(r.enabled),
        })
    st = read_state()
    st["use_case_routes"] = clean
    write_state(st)
    return {"ok": True, "routes": clean}


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


# ── Brain routing: set the primary model, restart the gateway ──────────────

# Base URLs some providers need written into config.
_PROVIDER_BASE_URL = {
    "openai-codex": "https://chatgpt.com/backend-api/codex",
    "custom": "http://localhost:11434/v1",
}


def _provider_default_model(pid: str) -> str:
    for p in PROVIDERS_CATALOG:
        if p["id"] == pid:
            return p.get("model") or ""
    return ""


def _gateway_status() -> Dict[str, Any]:
    try:
        out = subprocess.run(["pgrep", "-f", "gateway run"], capture_output=True, text=True, timeout=5)
        pids = [x for x in out.stdout.split() if x]
        started = None
        if pids:
            ps = subprocess.run(["ps", "-o", "lstart=", "-p", pids[0]], capture_output=True, text=True, timeout=5)
            started = ps.stdout.strip() or None
        return {"running": bool(pids), "pid": pids[0] if pids else None, "started": started}
    except Exception:
        return {"running": None, "pid": None, "started": None}


def _repin_enabled_crons(provider: str, model: str) -> int:
    """Re-pin every ENABLED cron to the new primary so Hermes' drift-guard
    doesn't skip them after a brain switch. Returns the number re-pinned."""
    try:
        p = hermes_home() / "cron" / "jobs.json"
        if not p.exists():
            return 0
        d = json.loads(p.read_text(encoding="utf-8"))
        jobs = d.get("jobs") if isinstance(d, dict) else d
        n = 0
        for j in (jobs or []):
            if isinstance(j, dict) and j.get("enabled"):
                j["provider"] = provider
                j["model"] = model
                j["provider_snapshot"] = provider
                j["model_snapshot"] = model
                n += 1
        if n and isinstance(d, dict):
            d["updated_at"] = time.time()
            p.write_text(json.dumps(d, indent=2), encoding="utf-8")
        return n
    except Exception:
        return 0


class BrainPrimaryBody(BaseModel):
    provider: str
    model: Optional[str] = None


@router.get("/brain")
async def get_brain():
    cfg = _read_config()
    m = cfg.get("model") or {}
    return {
        "primary": {"provider": m.get("provider"), "model": m.get("default") or m.get("model")},
        "fallback": cfg.get("fallback_providers") or [],
        "gateway": _gateway_status(),
    }


@router.post("/brain/primary")
async def set_primary(body: BrainPrimaryBody):
    """Promote a provider to primary (demote current to front of fallbacks) and
    re-pin enabled crons. The gateway must restart to pick it up."""
    cfg = _read_config()
    m = cfg.get("model") or {}
    cur_p, cur_m, cur_url = m.get("provider"), (m.get("default") or m.get("model")), m.get("base_url")
    new_p = body.provider.strip()
    if not new_p:
        raise HTTPException(400, "provider required")
    new_m = (body.model or "").strip() or _provider_default_model(new_p) or cur_m
    newm = {"provider": new_p, "default": new_m}
    if _PROVIDER_BASE_URL.get(new_p):
        newm["base_url"] = _PROVIDER_BASE_URL[new_p]
    fps = [f for f in (cfg.get("fallback_providers") or [])
           if f.get("provider") not in (new_p, cur_p)]
    if cur_p and cur_p != new_p:
        old = {"provider": cur_p, "model": cur_m}
        if cur_url or cur_p in _PROVIDER_BASE_URL:
            old["base_url"] = cur_url or _PROVIDER_BASE_URL[cur_p]
        fps.insert(0, old)
    cfg["model"] = newm
    cfg["fallback_providers"] = fps
    _write_config(cfg)
    repinned = _repin_enabled_crons(new_p, new_m)
    return {"ok": True, "primary": newm, "crons_repinned": repinned,
            "gateway_restart_required": True}


@router.post("/gateway/restart")
async def gateway_restart():
    """Restart the Hermes gateway so a brain switch takes effect (its config is
    loaded at start). launchd KeepAlive brings it back."""
    uid = os.getuid()
    try:
        r = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/ai.hermes.gateway"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return {"ok": True, "method": "launchctl", "gateway": _gateway_status()}
    except Exception:
        pass
    try:
        subprocess.run(["pkill", "-f", "gateway run"], capture_output=True, text=True, timeout=10)
        return {"ok": True, "method": "pkill (launchd will relaunch)", "gateway": _gateway_status()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"could not restart gateway: {exc}")


# ── Credential pool: multiple accounts/keys per provider (hermes auth) ──────

def _hermes_bin() -> Optional[str]:
    return shutil.which("hermes", path=_assist_path())


def _run_hermes_auth(args: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
    hb = _hermes_bin()
    if not hb:
        raise HTTPException(500, "`hermes` CLI not found on PATH")
    env = dict(os.environ)
    env["PATH"] = _assist_path()
    return subprocess.run([hb, "auth"] + args, capture_output=True, text=True,
                          timeout=timeout, env=env, stdin=subprocess.DEVNULL)


_EXHAUST_WORDS = ("rate-limited", "usage_limit", "429", "exhausted", "quota")


def _parse_auth_list(text: str) -> Dict[str, List[Dict[str, Any]]]:
    """Parse `hermes auth list` text into {provider: [ {index, raw, active,
    exhausted, detail} ]}."""
    import re
    pool: Dict[str, List[Dict[str, Any]]] = {}
    cur = None
    hdr = re.compile(r"^(\S+)\s+\(\d+\s+credential")
    cred = re.compile(r"^\s+#(\d+)\s+(.*)$")
    for line in (text or "").splitlines():
        mh = hdr.match(line)
        if mh:
            cur = mh.group(1)
            pool[cur] = []
            continue
        mc = cred.match(line)
        if mc and cur is not None:
            raw = mc.group(2).replace("←", "").strip()
            low = raw.lower()
            tl = re.search(r"\(([^)]*left)\)", raw)
            # Split "<label>  <type> <mechanism> <status…>" — label may have spaces.
            label, ctype = raw, ""
            mt = re.search(r"\s(api[_-]?key|oauth)\s", " " + raw + " ", re.I)
            if mt:
                label = raw[: mt.start()].strip() or raw
                ctype = mt.group(1).lower().replace("-", "_")
            pool[cur].append({
                "index": int(mc.group(1)),
                "raw": raw,
                "label": label,
                "ctype": ctype,
                "active": "←" in mc.group(2),
                "exhausted": any(w in low for w in _EXHAUST_WORDS),
                "detail": tl.group(1) if tl else "",
            })
    return pool


@router.get("/auth/pool")
async def auth_pool():
    """Pooled credentials per provider (multiple accounts/keys, exhaustion)."""
    try:
        p = _run_hermes_auth(["list"], timeout=20)
        return {"pool": _parse_auth_list(p.stdout or p.stderr or "")}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "hermes auth list timed out")


class AuthAddKeyBody(BaseModel):
    provider: str
    api_key: str
    label: Optional[str] = None


@router.post("/auth/add-key")
async def auth_add_key(body: AuthAddKeyBody):
    """Add another API-key account to a provider's pool (non-interactive).
    Works for api-key providers and Copilot/GitHub tokens."""
    prov = body.provider.strip()
    if not prov or not body.api_key.strip():
        raise HTTPException(400, "provider and api_key required")
    args = ["add", prov, "--type", "api-key", "--api-key", body.api_key.strip()]
    if body.label:
        args += ["--label", body.label.strip()]
    p = _run_hermes_auth(args, timeout=60)
    out = (p.stdout or "").strip() + (("\n" + p.stderr.strip()) if p.stderr else "")
    return {"ok": p.returncode == 0, "output": out[-1500:]}


class AuthProviderBody(BaseModel):
    provider: str
    target: Optional[str] = None  # for remove: index / id / label


@router.post("/auth/add-oauth")
async def auth_add_oauth(body: AuthProviderBody):
    """Start an OAuth device-code login to add another account (e.g. a second
    Codex/ChatGPT). Runs detached; poll /auth/add-oauth/status for the code+URL."""
    prov = body.provider.strip()
    if not prov:
        raise HTTPException(400, "provider required")
    hb = _hermes_bin()
    if not hb:
        raise HTTPException(500, "`hermes` CLI not found")
    env = dict(os.environ); env["PATH"] = _assist_path()
    log = logs_dir() / f"authadd-{prov}.log"
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            [hb, "auth", "add", prov, "--type", "oauth", "--no-browser"],
            stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env=env, start_new_session=True)
    (logs_dir() / f"authadd-{prov}.pid").write_text(str(proc.pid), encoding="utf-8")
    return {"ok": True, "provider": prov, "note": "Login started — poll status for the code + URL."}


@router.get("/auth/add-oauth/status")
async def auth_add_oauth_status(provider: str):
    log = logs_dir() / f"authadd-{provider}.log"
    pid_f = logs_dir() / f"authadd-{provider}.pid"
    running = False
    if pid_f.exists():
        try:
            os.kill(int(pid_f.read_text().strip()), 0)
            running = True
        except Exception:
            running = False
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    return {"running": running, "log": text[-3000:]}


@router.post("/auth/reset")
async def auth_reset(body: AuthProviderBody):
    """Clear the exhaustion/rate-limited flag for a provider's credentials so
    Hermes retries them. (Does NOT create quota — only clears a stale flag.)"""
    p = _run_hermes_auth(["reset", body.provider.strip()], timeout=30)
    return {"ok": p.returncode == 0, "output": ((p.stdout or "") + (p.stderr or "")).strip()[-800:]}


@router.post("/auth/remove")
async def auth_remove(body: AuthProviderBody):
    if not body.target:
        raise HTTPException(400, "target (index/id/label) required")
    p = _run_hermes_auth(["remove", body.provider.strip(), str(body.target).strip()], timeout=30)
    return {"ok": p.returncode == 0, "output": ((p.stdout or "") + (p.stderr or "")).strip()[-800:]}
