"""CLI Orchestrator — Hermes general-plugin side.

The dashboard UI/backend lives in ``dashboard/``. This module wires the plugin
into the *agent runtime*:

  * a ``post_tool_call`` hook that records every time the agent drives a tracked
    CLI through the ``terminal`` tool (this is what populates the usage gauges
    you see in the CLI Matrix tab — real counts, not simulated),
  * a ``pre_llm_call`` hook that injects a routing policy each turn so a cheap
    local orchestrator model DELEGATES high-intensity work (code gen, image gen,
    heavy multi-file tasks) to the capable CLIs you've mapped in the
    orchestration matrix — instead of attempting it on the weak local model, and
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
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_ID = "cli-orchestrator"

# How the agent should invoke each capable CLI when delegating heavy work.
# Kept here (not in the dashboard backend) because it's runtime guidance the
# orchestrator model reads. Only CLIs actually installed are surfaced.
DELEGATION_HINTS = {
    "codex": "run `codex exec \"<task>\"` via the terminal tool (non-interactive, free via ChatGPT sub)",
    "claude": "run `claude -p \"<task>\"` via the terminal tool, or load the `claude-code` skill",
    "gemini": "run `gemini -p \"<task>\"` via the terminal tool",
    "qwen": "run `qwen -p \"<task>\"` via the terminal tool (free Qwen OAuth)",
    "opencode": "run `opencode run \"<task>\"` via the terminal tool, or load the `opencode` skill",
    "crush": "run `crush run \"<task>\"` via the terminal tool",
    "amp": "run `amp -x \"<task>\"` via the terminal tool",
    "cursor-agent": "run `cursor-agent -p \"<task>\"` via the terminal tool",
    "goose": "run `goose run -t \"<task>\"` via the terminal tool",
    "aider": "run `aider --message \"<task>\"` via the terminal tool",
    "copilot": "use the `copilot` CLI via the terminal tool",
}
# Capable coding CLIs, in default priority order (used when no explicit
# orchestration-matrix rule maps an intent to a CLI).
CODING_PRIORITY = ("codex", "claude", "qwen", "opencode", "crush", "amp",
                   "cursor-agent", "goose", "aider", "gemini", "copilot")

# Binaries we count. Mirrors dashboard/plugin_api.py DEFAULT_CATALOG ids/bins.
TRACKED_BINS = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "qwen": "qwen",
    "cursor-agent": "cursor-agent",
    "amp": "amp",
    "crush": "crush",
    "goose": "goose",
    "mods": "mods",
    "llm": "llm",
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


_LOCAL_PROVIDERS = {"custom", "ollama", "local", "vllm", "llamacpp", "llama.cpp", "llama-cpp"}


def _primary_is_local() -> bool:
    """True when the active primary model is a weak local one (Ollama/etc.).

    The delegation policy only makes sense when a cheap local model is
    orchestrating. When the primary is already a capable provider (Codex,
    Copilot, …), it handles heavy work directly and the policy would just be
    redundant noise — so we suppress it.
    """
    try:
        import yaml
        cfg = yaml.safe_load(open(_hermes_home() / "config.yaml")) or {}
        m = cfg.get("model")
        provider = (m.get("provider") if isinstance(m, dict) else "") or ""
        return provider.strip().lower() in _LOCAL_PROVIDERS
    except Exception:
        return True  # unknown → assume local so we don't lose routing guidance


def _over_cap(cli_id: str, state: dict) -> bool:
    """True if this CLI is at/over its daily cap (so we shouldn't route to it)."""
    cap = (state.get("limits", {}).get(cli_id) or {}).get("daily", 0) or 0
    if cap <= 0:
        return False
    events = state.get("usage", {}).get(cli_id, [])
    now = time.time()
    return sum(1 for t in events if t >= now - 86400) >= cap


def _on_pre_llm_call(session_id=None, user_message=None, model=None,
                     is_first_turn=False, **kwargs):
    """Inject a routing policy so a cheap local model delegates heavy work to CLIs.

    Returns a context string (appended to the user turn) listing which capable
    CLIs are available and how to delegate to them. Respects the orchestration
    matrix (intent -> CLI) and daily caps. Returns None (no injection) when no
    capable CLI is installed — then the local model just answers normally.
    """
    try:
        if not _primary_is_local():
            return None  # capable primary handles heavy work directly — no policy needed
        state = _read_state()
        installed = {cid: b for cid, b in TRACKED_BINS.items() if shutil.which(b)}
        # Capable coding CLIs that are installed AND under their cap.
        capable = [c for c in CODING_PRIORITY
                   if c in installed and not _over_cap(c, state)]
        if not capable:
            return None  # nothing to delegate to → local model handles it all

        lines = [
            "[CLI Orchestrator — routing policy]",
            "You are a local orchestrator on a small free model. Do NOT attempt "
            "high-intensity work yourself — DELEGATE it to a capable local CLI by "
            "actually CALLING the `terminal` tool (emit a real tool call; do NOT "
            "just print the command in a code block). After it returns, summarize "
            "or verify the result:",
        ]
        # Explicit orchestration-matrix rules take precedence.
        rules = [r for r in (state.get("routing") or [])
                 if r.get("cli") in capable]
        seen_intents = set()
        for r in rules:
            cli = r["cli"]
            seen_intents.add(r.get("intent", "").lower())
            lines.append(f"- {r.get('intent')}: delegate to `{cli}` — "
                        f"{DELEGATION_HINTS.get(cli, 'via the terminal tool')}.")
        # Default code-gen rule if the matrix didn't cover it.
        if not any("cod" in i or "gen" in i or "dev" in i for i in seen_intents):
            top = capable[0]
            lines.append(f"- Code generation / refactors / multi-file edits / "
                        f"debugging: delegate to `{top}` — {DELEGATION_HINTS.get(top)}.")
        lines.append("- Image generation: use the image_generation tool.")
        lines.append("- Simple questions, planning, summaries: answer directly "
                    "(no delegation).")
        lines.append(f"Capable CLIs available now (priority order): "
                    f"{', '.join(capable)}.")
        return {"context": "\n".join(lines)}
    except Exception as exc:  # never break the turn
        logger.debug("cli-orchestrator routing policy skipped: %s", exc)
        return None


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


# ── Telegram/gateway slash commands (all prefixed `cli-`) ───────────────────
# These let you drive the plugin's settings remotely from any Hermes gateway
# (Telegram, Discord, …). Hermes preserves hyphens in command names, so they
# dispatch when typed; Telegram's autocomplete menu only lists [a-z0-9_] names,
# so hyphenated ones work-when-typed but may not appear in the `/` menu.
# Note: media API KEYS are deliberately NOT settable here — keys would land in
# chat history. Set those on the loopback dashboard.

_BACKEND = None


def _backend():
    """Lazy-load the dashboard backend module to reuse its catalogs + helpers
    (single source of truth; no duplication)."""
    global _BACKEND
    if _BACKEND is None:
        import importlib.util
        path = Path(__file__).parent / "dashboard" / "plugin_api.py"
        spec = importlib.util.spec_from_file_location("cli_orchestrator_backend", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _BACKEND = mod
    return _BACKEND


def _cmd_status(raw_args: str = "") -> str:
    return _handle_cli_command(raw_args)


def _cmd_scan(raw_args: str = "") -> str:
    present = [b for b in TRACKED_BINS.values() if shutil.which(b)]
    missing = [b for b in TRACKED_BINS.values() if not shutil.which(b)]
    return (f"CLI scan — {len(present)}/{len(TRACKED_BINS)} installed\n"
            f"  ● {', '.join(present) or '(none)'}\n"
            f"  ○ {', '.join(missing) or '(none)'}")


def _cmd_limit(raw_args: str = "") -> str:
    parts = (raw_args or "").split()
    if len(parts) < 2:
        return "Usage: /cli-limit <cli> <daily> [hourly] [monthly]"
    cli_id = parts[0]
    try:
        daily = int(parts[1])
        hourly = int(parts[2]) if len(parts) > 2 else 0
        monthly = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        return "Caps must be integers. Usage: /cli-limit <cli> <daily> [hourly] [monthly]"
    state = _read_state()
    state.setdefault("limits", {})[cli_id] = {"hourly": hourly, "daily": daily, "monthly": monthly}
    _write_state(state)
    return f"Set {cli_id} caps → daily {daily}, hourly {hourly}, monthly {monthly}"


def _cmd_route(raw_args: str = "") -> str:
    parts = (raw_args or "").split(maxsplit=1)
    if len(parts) < 2:
        return "Usage: /cli-route <cli> <intent words…>   e.g. /cli-route codex code generation"
    cli_id, intent = parts[0], parts[1].strip()
    state = _read_state()
    rules = [r for r in state.get("routing", []) if r.get("intent", "").lower() != intent.lower()]
    rules.append({"intent": intent, "cli": cli_id})
    state["routing"] = rules
    _write_state(state)
    return f"Routed '{intent}' → {cli_id}"


def _cmd_routes(raw_args: str = "") -> str:
    rules = _read_state().get("routing", [])
    if not rules:
        return "No routing rules. Add one with /cli-route <cli> <intent…>"
    return "Routing rules:\n" + "\n".join(f"  {r['intent']} → {r['cli']}" for r in rules)


def _cmd_install(raw_args: str = "") -> str:
    parts = (raw_args or "").split()
    if not parts:
        return "Usage: /cli-install <cli> [manager]"
    cli_id = parts[0]
    manager = parts[1] if len(parts) > 1 else None
    try:
        cat = {c["id"]: c for c in _backend().load_catalog()}
    except Exception as exc:
        return f"Catalog unavailable: {exc}"
    entry = cat.get(cli_id)
    if not entry:
        return f"Unknown CLI: {cli_id}"
    installers = entry.get("install") or {}
    if not installers:
        return f"No install command known for {cli_id}"
    mgr = manager or next(iter(installers))
    cmd = installers.get(mgr)
    if not cmd:
        return f"No '{mgr}' installer for {cli_id} (have: {', '.join(installers)})"
    import subprocess
    logdir = _hermes_home() / PLUGIN_ID / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    fh = open(logdir / f"{cli_id}.log", "w", encoding="utf-8")
    fh.write(f"$ {cmd}\n\n")
    fh.flush()
    subprocess.Popen(cmd, shell=True, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    return f"Installing {cli_id} via {mgr} …  (`{cmd}`)"


def _cmd_media(raw_args: str = "") -> str:
    try:
        import importlib.util as _il
        b = _backend()
        cat = b.MEDIA_CATALOG
        env_file = b._read_env_file()
    except Exception as exc:
        return f"Media catalog unavailable: {exc}"
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for m in cat:
        envs = m.get("env", [])
        if m.get("module"):
            conf = _il.find_spec(m["module"]) is not None
        elif m.get("keyless"):
            conf = True
        elif envs:
            conf = all(b._key_present(e, env_file) for e in envs)
        else:
            conf = False
        groups[m["category"]].append((m["name"], m["kind"], conf))
    lines = ["Media backends (✓ configured / ○):"]
    for cat_name, items in groups.items():
        lines.append(f" {cat_name}:")
        for name, kind, conf in items:
            lines.append(f"   {'✓' if conf else '○'} {name} [{kind}]")
    lines.append("(Set media keys on the dashboard, not Telegram — keys would be in chat history.)")
    return "\n".join(lines)


def _cmd_help(raw_args: str = "") -> str:
    return (
        "CLI Orchestrator — remote commands:\n"
        "  /cli-status                       CLI status + usage today\n"
        "  /cli-scan                         re-detect installed CLIs\n"
        "  /cli-limit <cli> <daily> [hr] [mo]  set usage caps\n"
        "  /cli-route <cli> <intent…>        map an intent to a CLI\n"
        "  /cli-routes                       list routing rules\n"
        "  /cli-install <cli> [manager]      install a CLI\n"
        "  /cli-media                        media backend status\n"
        "  /cli-help                         this help\n"
        "(Also works as /cli <subcommand>.)"
    )


# Subcommand dispatch so `/cli <sub>` works too (guaranteed Telegram-friendly).
_SUBCMDS = {
    "status": _cmd_status, "scan": _cmd_scan, "limit": _cmd_limit,
    "route": _cmd_route, "routes": _cmd_routes, "install": _cmd_install,
    "media": _cmd_media, "help": _cmd_help,
}

# (name, handler, description) for the cli-* family + the `cli` dispatcher.
_CLI_COMMANDS = [
    ("cli-status", _cmd_status, "CLI status + usage"),
    ("cli-scan", _cmd_scan, "Re-detect installed CLIs"),
    ("cli-limit", _cmd_limit, "Set caps: <cli> <daily> [hourly] [monthly]"),
    ("cli-route", _cmd_route, "Route an intent: <cli> <intent…>"),
    ("cli-routes", _cmd_routes, "List routing rules"),
    ("cli-install", _cmd_install, "Install a CLI: <cli> [manager]"),
    ("cli-media", _cmd_media, "Media backend status"),
    ("cli-help", _cmd_help, "List CLI Orchestrator commands"),
]


def _cmd_dispatch(raw_args: str = "") -> str:
    raw = (raw_args or "").strip()
    if not raw:
        return _cmd_status("")
    parts = raw.split(maxsplit=1)
    fn = _SUBCMDS.get(parts[0].lower())
    if not fn:
        return f"Unknown subcommand '{parts[0]}'.\n\n" + _cmd_help("")
    return fn(parts[1] if len(parts) > 1 else "")


# ── Music generation tool (Hermes has no native music framework) ────────────
# Provides a `generate_music` tool backed by Replicate MusicGen. Requires
# REPLICATE_API_TOKEN (managed via the Media panel). ⚠️ UNVERIFIED: written
# against Replicate's documented predictions API but not run without a token.
MUSIC_SCHEMA = {
    "name": "generate_music",
    "description": (
        "Generate a short instrumental music clip from a text prompt using "
        "Replicate's MusicGen model. Use when the user asks to compose/generate "
        "music or a melody. Requires REPLICATE_API_TOKEN. Returns the path to a "
        "saved audio file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string",
                       "description": "Music description: style, mood, instruments, tempo"},
            "duration": {"type": "integer",
                         "description": "Clip length in seconds (default 8, max 30)"},
        },
        "required": ["prompt"],
    },
}


def _save_audio_bytes(data: bytes, ext: str = "wav") -> str:
    import datetime
    import uuid
    d = _hermes_home() / "cache" / "audio"
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = d / f"musicgen_{ts}_{uuid.uuid4().hex[:8]}.{ext}"
    path.write_bytes(data)
    return str(path)


def _generate_music(args: dict, **kwargs) -> str:
    """Tool handler — always returns a JSON string, never raises."""
    prompt = (args.get("prompt") or "").strip() if isinstance(args, dict) else ""
    try:
        duration = int(args.get("duration") or 8) if isinstance(args, dict) else 8
    except Exception:
        duration = 8
    if not prompt:
        return json.dumps({"error": "prompt is required"})
    token = (os.environ.get("REPLICATE_API_TOKEN") or "").strip()
    if not token:
        return json.dumps({"error": "REPLICATE_API_TOKEN not set — add it in the CLI Matrix Media panel."})
    try:
        import httpx
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json", "Prefer": "wait"}
        body = {"input": {"prompt": prompt,
                          "duration": max(1, min(30, duration)),
                          "model_version": "stereo-melody-large",
                          "output_format": "wav"}}
        url = "https://api.replicate.com/v1/models/meta/musicgen/predictions"
        with httpx.Client(timeout=httpx.Timeout(300.0, read=300.0)) as http:
            r = http.post(url, headers=headers, json=body)
            r.raise_for_status()
            pred = r.json()
            status = pred.get("status")
            get_url = (pred.get("urls") or {}).get("get")
            deadline = time.time() + 300
            while status not in ("succeeded", "failed", "canceled") and get_url and time.time() < deadline:
                time.sleep(3)
                pred = http.get(get_url, headers=headers).json()
                status = pred.get("status")
            if status != "succeeded":
                return json.dumps({"error": f"MusicGen status: {status}", "detail": pred.get("error")})
            out = pred.get("output")
            audio_url = out[0] if isinstance(out, list) and out else out
            if not audio_url:
                return json.dumps({"error": "no audio output from MusicGen"})
            data = http.get(audio_url, follow_redirects=True).content
        path = _save_audio_bytes(data, ext="wav")
        return json.dumps({"ok": True, "audio": path, "prompt": prompt,
                           "provider": "replicate-musicgen"})
    except Exception as exc:
        return json.dumps({"error": f"music generation failed: {exc}"})


def register(ctx):
    """Wire the runtime hook + slash command. Called once at startup; if it
    raises, Hermes disables this plugin but keeps running."""
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    # `/cli <subcommand>` dispatcher (Telegram-friendly) + the cli-* family.
    try:
        ctx.register_command(
            "cli", handler=_cmd_dispatch,
            description="CLI Orchestrator (status|scan|limit|route|routes|install|media|help)",
        )
        for _name, _fn, _desc in _CLI_COMMANDS:
            ctx.register_command(_name, handler=_fn, description=_desc)
    except Exception as exc:
        # register_command is newer; tolerate older Hermes that lack it.
        logger.debug("cli-orchestrator: slash commands not registered: %s", exc)
    try:
        ctx.register_tool(
            name="generate_music",
            toolset="cli-orchestrator",
            schema=MUSIC_SCHEMA,
            handler=_generate_music,
        )
    except Exception as exc:
        logger.debug("cli-orchestrator: generate_music tool not registered: %s", exc)
