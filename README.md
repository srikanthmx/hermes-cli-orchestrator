# Hermes CLI Orchestrator

A drop-in [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that turns
Hermes into a **governor for free/subscription CLIs and media backends** — so you can
run Hermes at ~$0 by stacking the tiers you already pay for (or get free) instead of a
single paid API plan. It adds a **CLI Matrix** dashboard tab plus runtime hooks.

## What it does

**Local CLI management**
- **Detect** every CLI on the host (`which` + version probe) — Online / Not Authenticated / Missing.
- **Auth status** for CLIs that expose it (e.g. `gh auth status`) or a credentials-file check.
- **Rate-limit guardrails** — hourly / daily / monthly caps per CLI.
- **Real usage tracking** — a `post_tool_call` hook tallies every time the agent drives a CLI through the `terminal` tool (gauges, "over cap" warnings).
- **One-click install** — runs a catalog install command (`npm i -g`, `brew install`, …), detached, with streamed logs.
- **17-CLI catalog** — Claude Code, Codex, Gemini, Qwen, Copilot, OpenCode, Cursor, Amp, Crush, Goose, Aider, mods, llm, gh, glab, Ollama, Hermes — each tagged with its Hermes **provider** mapping and **plan** (free / subscription / BYO / local).

**Intent routing**
- A `pre_llm_call` hook injects a routing policy so a cheap local orchestrator **delegates high-intensity work** (code gen, etc.) to capable CLIs via the orchestration matrix. It **auto-suppresses** when the primary model is already a capable provider — so it adds zero noise once you're on Codex/Copilot/etc.

**Media & Integrations panel**
- **34 media backends** across **Voice/TTS, Speech-to-Text, Image, Video, Music**, each labelled **native** (Hermes built-in) vs **plugin** (needs a backend), with **in-UI API-key entry** written to `~/.hermes/.env` (`chmod 600`, value never echoed).
- Ships two working backends and one tool (see below).

**`/cli` slash command** — quick CLI status + usage inside any Hermes session.

It integrates **only** through Hermes's documented plugin contracts, so nothing in the
Hermes codebase is modified and upstream upgrades stay clean.

---

## Prerequisites

- macOS or Linux
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) installed (`hermes` on your `PATH`)
- Python 3.11+ (Hermes provides this in its venv)

---

## Step-by-step install

### 1. Confirm Hermes is installed
```bash
hermes --version
```

### 2. Clone into your Hermes plugins directory
Hermes loads **user** plugins from `~/.hermes/plugins/<name>/`; the directory must be `cli-orchestrator`:
```bash
git clone https://github.com/srikanthmx/hermes-cli-orchestrator.git \
  ~/.hermes/plugins/cli-orchestrator
```
> Hermes only auto-imports a plugin's backend Python for `user`/`bundled` sources — installing under `~/.hermes/plugins/` is required for the dashboard API to mount.

### 3. Enable it
```bash
hermes plugins enable cli-orchestrator
```
This activates the usage hook, the intent-routing policy, the `/cli` command, and the `generate_music` tool on next start. (The dashboard tab works even without this.)

### 4. Launch the dashboard
```bash
hermes dashboard
```
First launch builds the web UI once, then opens `http://127.0.0.1:9119`.
> Open it from the URL Hermes launches — it carries the loopback token; a hand-typed tab bounces to login.

### 5. Open the **CLI Matrix** tab
Right after **Skills** in the left nav. Detection, limits, install, routing, and the Media panel all work with **no model configured**.

### 6. (Optional) install the bundled media backends
This repo ships extra Hermes provider-plugins under `backends/` to make media work:
```bash
# Free, keyless image generation:
cp -r backends/image_gen/pollinations ~/.hermes/plugins/image_gen/pollinations
hermes plugins enable pollinations
hermes config set image_gen.provider pollinations

# Text-to-video (needs a free-trial FAL_KEY — see Media panel):
cp -r backends/video_gen/fal ~/.hermes/plugins/video_gen/fal
hermes plugins enable video_gen/fal
```
Free local speech-to-text just needs the dependency: `uv pip install -p ~/.hermes/.../venv/bin/python faster-whisper` (Hermes's default `local` STT provider uses it).

---

## Using it

| Action | Where | What happens |
|--------|-------|--------------|
| **Re-scan** | top-right | Re-probes `which` + versions + auth |
| **Set caps** | per-CLI card | Saves hourly/daily/monthly to `state.json` |
| **Install** | per-CLI card (Missing) | Runs the catalog install command, streams logs |
| **Route intents** | Orchestration matrix | Saves intent → CLI rules (drives the routing policy) |
| **Add media key** | Media panel | Saves an API key to `~/.hermes/.env` (chmod 600) |
| **Quick status** | `/cli` in any session | Installed CLIs + today's usage |
| **Generate music** | `generate_music` tool | MusicGen via Replicate (needs `REPLICATE_API_TOKEN`) |

---

## Media backends — what works today

| Category | Backend | Status |
|---|---|---|
| **Voice / TTS** | Edge TTS (Hermes native) | ✅ **working, free, no key** (verified) |
| **Speech-to-Text** | faster-whisper (Hermes native `local`) | ✅ **working, free, no key** (verified) |
| **Image** | Pollinations (`backends/image_gen/pollinations`) | ✅ **working, free, no key** (verified) |
| **Video** | fal (`backends/video_gen/fal`) + 9 more key-configurable | ⚙️ wired + registers; `generate()` **unverified — needs a key** |
| **Music** | `generate_music` tool (Replicate MusicGen) + 7 more | ⚙️ wired; generation **unverified — needs a key** |

The panel manages keys for **all** providers (fal, Replicate, Runway, Luma, Kling,
MiniMax, Pika, Haiper, Veo, Sora; Suno, Udio, ElevenLabs Music, Stable Audio, Mubert,
Loudly, Beatoven…). Video/music backends activate once you paste a key — fal and
Replicate both offer free trial credits.

> **Honesty note:** backends labelled "unverified" were written against the providers'
> documented APIs and pass import/registration/error-path checks, but have **not** been
> run end-to-end (no key was available). They are not claimed to work until verified.

---

## State & customization

- Mutable state (limits, routing rules, usage events) lives in `~/.hermes/cli-orchestrator/state.json` — outside this repo.
- Media API keys live in `~/.hermes/.env` (mode 600).
- Extend the CLI catalog by dropping a `catalog.json` (same shape as `DEFAULT_CATALOG` in `dashboard/plugin_api.py`) into `~/.hermes/cli-orchestrator/`.

---

## Keeping it updated
```bash
cd ~/.hermes/plugins/cli-orchestrator && git pull
hermes dashboard --stop && hermes dashboard   # reload backend routes
```
The backend computes `HERMES_HOME` itself and avoids importing Hermes internals, so it's decoupled from Hermes refactors — if a contract changes, update **this** repo only, never the Hermes tree.

---

## Architecture

| File | Contract | Role |
|------|----------|------|
| `dashboard/manifest.json` | Dashboard plugin | Registers the **CLI Matrix** tab |
| `dashboard/plugin_api.py` | Dashboard plugin | FastAPI `router` at `/api/plugins/cli-orchestrator/` (CLI scan/limits/routing/install + Media catalog/scan/key) |
| `dashboard/dist/index.js` | Dashboard plugin | React UI (no build step; `window.__HERMES_PLUGIN_SDK__`) |
| `plugin.yaml` | General plugin | Declares hooks + the `generate_music` tool |
| `__init__.py` | General plugin | `post_tool_call` usage hook, `pre_llm_call` routing policy, `/cli` command, `generate_music` |
| `backends/` | Hermes provider-plugins | Pollinations image + fal video (install into Hermes's plugin tree) |

```
hermes-cli-orchestrator/
├── plugin.yaml
├── __init__.py                 # hooks + /cli + generate_music
├── dashboard/
│   ├── manifest.json
│   ├── plugin_api.py           # CLI + Media backend
│   └── dist/index.js           # dashboard UI
├── backends/                   # extra Hermes provider-plugins
│   ├── image_gen/pollinations/ # free keyless image (working)
│   └── video_gen/fal/          # text-to-video (needs FAL_KEY)
└── README.md
```

---

## Limitations (honest)

- **Rate limits are observability guardrails** — tracked + surfaced, not hard blocks (Hermes plugin `pre_tool_call` can't veto; enforced blocking needs a `config.yaml` shell hook).
- **Routing decides, the model executes** — the policy reliably picks the right CLI, but a weak local model may *narrate* a delegation instead of calling the tool. A capable primary (Codex/Copilot/Gemini, free via subscription) executes reliably; Ollama is the free floor.
- **Usage tracking** sees only CLIs the agent runs via `terminal` in plugin-enabled sessions, and counts *invocations*, not tokens.
- **Auth status** is exact only for CLIs with a status command (`gh`, `glab`); others use a credentials-file check.
- **Media: video & music are unverified** pending a provider key (see the table above). The Media panel manages the keys; generation is confirmed per-provider once a key exists.

---

## License

MIT
