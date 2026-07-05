# Hermes CLI Orchestrator

**The CLI & backend control plane for [Hermes Agent](https://github.com/NousResearch/hermes-agent).**

Hermes already governs your *model providers* (fallback chains, Mixture-of-Agents,
pooled credentials, an OAuth→OpenAI proxy). This plugin **extends that governance to your
local AI CLIs** — Codex, Antigravity (`agy`), OpenCode, Claude Code, Cursor, and friends —
and gives you **one dashboard to run the whole fleet**: detect, install, sign-in-check,
cap, route, and delegate. The goal: run Hermes at ~$0 by putting every CLI, subscription,
free-tier key, and local model to work, instead of leaning on a single paid plan.

## How this differs from Hermes itself (honest)

Hermes **v0.18** natively does multi-model **fallback**, **Mixture-of-Agents**, **pooled
credentials** (`hermes auth`), an OAuth **proxy**, **cron**, model-independent
**memory/skills/sessions**, and it can **run CLIs** (the `terminal` tool + the desktop
app's integrated terminal). We don't reinvent those. What this plugin adds on top:

- **Governs *CLIs*, not just model providers** — brings local AI CLIs into the same regime Hermes applies to API/OAuth backends: detection, per-CLI caps, usage tracking, delegation with fallback.
- **`cli_delegate` / `/cli-delegate`** — a **deterministic**, cap-aware delegation across CLIs (Codex → Qwen → OpenCode → `agy` …) that skips exhausted ones and falls through on rate-limits. The reliable path for when a weak orchestrating model would otherwise *narrate* "I'll run codex…" instead of actually running it.
- **A single control-plane dashboard** — CLIs + model providers + media backends in one **Backends** tab (status, provenance, per-backend category toggles, caps, keys **with "get key" links**, guided install), plus a **Routing** tab for per-category primary/fallback. Hermes has config subcommands and a terminal; this is the unified UI.
- **Per-backend ops tooling** — **live "Check sign-in"** (runs a real sample call through the CLI and reports the true status, auto-marking it *verified*), a **guided install stepper** with prereq probing + streaming logs, and an **"Ask AI for help"** button that answers setup questions *through one of your own governed CLIs* (free, no extra key).
- **`generate_music`** tool — Hermes has no music framework.

## What it does

**Local CLI governance**
- **Detect** every CLI on the host (`which` + version + auth probe) — Ready / Auth needed / Missing, with **provenance** (verified · catalog · custom).
- **Live "Check sign-in"** — a real one-shot call through the CLI to confirm it's authenticated and working; passing auto-marks it **verified**.
- **Rate-limit guardrails** — hourly / daily / monthly caps per backend, with usage gauges.
- **Guided install** — a numbered stepper (prereq check → command with copy → live log → auth → verify) and an AI-assisted help button.
- **18-CLI catalog** — Claude Code, Codex, **Antigravity CLI (`agy`)** + **Antigravity IDE**, Gemini, Qwen, Copilot, OpenCode, Cursor, Amp, Crush, Goose, mods, llm, gh, glab, Ollama, Hermes. The catalog is **kept current** by the `catalog-refresh` skill (researches new CLIs/models, adds/configures, prunes dead ones — `aider` was pruned as an interactive-only pair-programmer, not a delegation worker).

**Delegation & routing**
- **`cli_delegate` tool + `/cli-delegate` command** — put a local CLI to work with cap-skip, cross-CLI fallback, and usage recording.
- **Category routing** — set a primary + fallback per use case (coding, chat, image, audio, video, research, docs, automation) among the backends you've enabled for it.

**Model & media governance (a management layer over Hermes-native mechanisms)**
- **Model registry** — free/cheap/subscription providers with tier labels, **"get key" links**, and cooldown *visibility* (e.g. "Codex → retry in 28d"). *The fallback/pooling underneath is Hermes-native; the registry + links + surfacing are ours.*
- **34 media backends** across Voice/TTS, Speech-to-Text, Image, Video, Music with in-UI key entry (written to `~/.hermes/.env`, `chmod 600`, value never echoed) — each with a **"get key" link**.

**Remote control** — `/cli-*` slash commands manage the governor from any gateway (Telegram, …).

It integrates **only** through Hermes's documented plugin contracts, so nothing in the
Hermes codebase is modified and upstream upgrades stay clean.

> **Roadmap (not yet built — don't expect these to work):** auto-cooldown detection
> (auto-route around an exhausted bucket like Codex's 28-day cap), a one-click free-first
> chain builder, and multi-key/multi-account pooling wrappers. Today cooldowns are
> *surfaced*, and the rotation itself is Hermes-native `fallback`/`auth`.

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
This activates the usage hook, the intent-routing policy, the `cli_delegate` tool + `/cli-*` commands, and the `generate_music` tool on next start. (The dashboard tab works even without this.)

### 4. Launch the dashboard
```bash
hermes dashboard
```
First launch builds the web UI once, then opens `http://127.0.0.1:9119`.
> Open it from the URL Hermes launches — it carries the loopback token; a hand-typed tab bounces to login.

### 5. Open the **CLI Governor** tab
Right after **Skills** in the left nav. Two top-level tabs:

- **Backends** — grouped by a promotion flow so nothing is padded:
  - **Fleet — verified & ready**: installed + verified (passed a live test),
    authed models, configured media. Only these are routable.
  - **Set up — verify or add a key**: detected/known but not promoted yet — run
    **Check sign-in**, finish sign-in, or paste a key (**with a "get key" link**).
  - **Install from catalog**: known CLIs you haven't installed — a **guided
    step-by-step install**, then verify to promote.
  A backend **rises into the Fleet only once it passes the test flow.**
- **Routing** — per use case (**Coding, Chat, Image, Audio, Video, Research,
  Docs, Automation, Other**), pick a **primary + fallback** among the
  **Fleet** backends enabled for that category (untested/uninstalled ones can't be routed).

Detection, limits, install, routing, and media keys all work with **no model
configured**. Caps are prefilled from the catalog/provider tier; saved values
override. Local/custom targets only appear when detected or when you choose
**Add custom/local**. Gemini CLI is treated as legacy (its free tier ended); the
forward path for free Google workflows is the **Antigravity CLI (`agy`)**.

Antigravity CLI (`agy`) install/auth flow:
```bash
# macOS and Linux, from Google's Antigravity CLI install docs
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

```powershell
# Windows PowerShell
irm https://antigravity.google/cli/install.ps1 | iex
```

```bat
# Windows CMD
curl -fsSL https://antigravity.google/cli/install.cmd -o install.cmd && install.cmd && del install.cmd
```

```bash
# then run once and complete Google's sign-in; test headlessly with:
agy -p "reply PONG"
```
The IDE (a separate product) installs as `antigravity-ide`; use the **Open app**
button on its card to sign in. Setup docs: https://antigravity.google/docs/cli

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
| **Set caps** | Matrix limits column | Saves hourly/daily/monthly caps for CLI, provider, or media targets to `state.json` |
| **Install** | Matrix configure column | Runs the catalog install command, then the same row shows auth/key setup and verify |
| **Route use cases** | Matrix route controls | Saves explicit primary + fallback targets for Coding, Chat, Image, Audio, Video, Research, Docs, Automation, and Other |
| **Route legacy intents** | `/cli-route` or API | Saves intent → CLI rules (still honored by the runtime policy) |
| **Add provider/media key** | Matrix configure column | Saves an API key to `~/.hermes/.env` (chmod 600); extra slots are stored as `KEY_2`, `KEY_3`, ... |
| **Authenticate CLI** | Matrix configure column | Shows the CLI-specific login command and docs; use **Verify** after completing auth |
| **Quick status** | `/cli` in any session | Installed CLIs + today's usage |
| **Generate music** | `generate_music` tool | MusicGen via Replicate (needs `REPLICATE_API_TOKEN`) |

---

## Remote control (Telegram / gateway)

All settings are reachable from any Hermes gateway (Telegram, Discord, …) via
`cli-` prefixed slash commands — so you can manage the governor from your phone:

| Command | Does |
|---------|------|
| `/cli-status` | CLI status + usage today |
| `/cli-scan` | re-detect installed CLIs |
| `/cli-limit <cli> <daily> [hourly] [monthly]` | set usage caps |
| `/cli-route <cli> <intent…>` | map an intent to a CLI |
| `/cli-routes` | list routing rules |
| `/cli-install <cli> [manager]` | install a CLI |
| `/cli-media` | media backend status |
| `/cli-delegate <task>` | **run a task on a local worker CLI** (caps + fallback + usage) |
| `/cli-usage` | provider / model (brain) usage |
| `/cli-help` | list commands |

`/cli-delegate` is the reliable way to put a **local CLI to work** without depending
on a weak model to emit a tool call — it routes to the highest-priority available
CLI, skips any over its cap, falls back on rate-limit, and records the usage the
dashboard shows.

`/cli <subcommand>` works too (e.g. `/cli limit codex 200`) — handy because
Telegram's command **menu** only autocompletes `[a-z0-9_]` names, so the
hyphenated forms work when typed but may not appear in the `/` menu.

Gate access with `hermes pairing` (only authorized DMs) and per-platform slash
controls. **Media API keys are intentionally *not* settable over chat** (they'd
land in chat history) — set those on the loopback dashboard.

---

## Media backends — what works today

Verified end-to-end on this build (real round-trip, not just "it imports"):

| Category | Backend | Status |
|---|---|---|
| **Image** | Pollinations (`backends/image_gen/pollinations`) | ✅ **ran** — generated a real 256×256 JPEG, free, keyless |
| **Voice / TTS** | Edge TTS (`edge_tts` module) | ✅ **ran** — produced a real MP3, free, no key (needs the `edge_tts` dep installed) |
| **Speech-to-Text** | faster-whisper (`local`) | ✅ **ran** — transcribed the TTS audio back accurately, free (needs `faster_whisper` installed) |
| **Video** | fal (`backends/video_gen/fal`) + 9 more key-configurable | ⚙️ wired + registers; `generate()` **NOT run — needs a key** |
| **Music** | `generate_music` tool (Replicate MusicGen) + 7 more | ⚙️ wired; generation **NOT run — needs a key** |

Also verified end-to-end this build: **code delegation** (a real coding task via `agy`
returned a correct function). **Not** exercised: a full chat turn through the model chain
(would burn the scarce Codex quota), and cross-model fallback (only Codex/Copilot/Ollama
are currently authed).

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
- Provider and media API keys live in `~/.hermes/.env` (mode 600). When adding
  another key for the same env var, the dashboard stores it as `KEY_2`, `KEY_3`,
  etc. so the primary provider env var stays compatible with clients that expect
  exactly one token.
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
| `dashboard/manifest.json` | Dashboard plugin | Registers the **CLI Governor** tab |
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
