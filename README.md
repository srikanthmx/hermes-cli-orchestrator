# Hermes CLI Orchestrator

**The control plane that lets [Hermes Agent](https://github.com/NousResearch/hermes-agent) run on many brains — so it never hits a wall.**

![Hermes plugin](https://img.shields.io/badge/Hermes_Agent-plugin-8A2BE2) ![Platform](https://img.shields.io/badge/platform-macOS%20%C2%B7%20Linux-lightgrey) ![Cost](https://img.shields.io/badge/runs_at-~%240-brightgreen) ![License](https://img.shields.io/badge/license-MIT-blue)

Every AI plan has a wall. Codex can burn out in ~3 cron runs, then cool down for **28 days**. A free tier dies mid-month. A key rate-limits at the worst moment. If your agent leans on one "best model," it *stops* — and Hermes won't rotate off a hard-quota primary on its own (a hard 429 is treated as fatal, not failed-over).

This plugin is built on one idea: **there is no best brain — the *pool* is the brain.** Hermes keeps memory, skills, and sessions independent of the model, so the backend can change and it's still the same agent, with the same memory and habits. The orchestrator turns that into an operating model:

- **Stack every quota bucket you can get** — subscriptions (Codex, Copilot), free OAuth (Qwen, Nous), free API tiers (Gemini, OpenRouter `:free`, …), trial credits, local AI CLIs, multiple accounts per provider.
- **See the whole fleet in one dashboard** — what's installed, what's authed, what's verified, what's cooling down, what it costs you (nothing).
- **Switch brains in one command, from your phone** — even while the current model is dead.

## Highlights

⚡ **Switch brains from anywhere.** `/cli-brain copilot` in Telegram promotes a provider to primary, demotes the old one into the fallback chain, **re-pins your enabled crons** (so Hermes's drift-guard never silently skips them), and restarts the gateway. It dispatches with **no LLM in the loop**, so it works *while the brain is down* — the exact moment you need it. Deliberately manual: the plugin records and warns, but **never rewrites your primary on its own**.

🩺 **Probe before you trust.** `/cli-brain test <provider>` makes **one real call** through a provider and records/clears its cooldown — including quota deaths that happen in Hermes's auth layer, which no plugin hook can see. The dashboard's **Check sign-in** does the same for CLIs. Nothing gets a *verified* label without a live reply.

🎛️ **One dashboard for the whole fleet.** CLIs + model providers + media backends in three tabs — **Backends** (detect, guided install stepper, live sign-in checks, caps + usage bars, API keys with *get-key* links), **Brains** (primary switch, pooled accounts per provider, cooldowns), **Routing** (per-category primary/fallback: coding, chat, image, audio, video, research, docs, automation).

🤝 **Put your local AI CLIs to work — deterministically.** The `cli_delegate` tool and `/cli-delegate` command route heavy tasks to Codex / `agy` / OpenCode / …, **skip any CLI over its daily cap**, fall back on rate-limits, and record usage. This extends Hermes's provider governance to the CLIs on your PATH — and it's the reliable path when a weak orchestrating model would otherwise *narrate* "I'll run codex…" instead of running it.

🔑 **Credential pooling UI.** Add multiple keys/accounts per provider from the Brains tab (wraps Hermes-native `hermes auth`) — every bucket gets bigger.

🆓 **~$0 media stack included.** Free keyless image generation (Pollinations), free TTS (Edge) and STT (faster-whisper), a 34-backend media key manager, and a `generate_music` tool (Hermes has no music framework).

📟 **Everything is remote-controllable** from Telegram/Discord via `/cli-*` commands — caps, routing, installs, delegation, brain switching.

It integrates **only** through Hermes's documented plugin contracts — nothing in the Hermes tree is modified, upstream upgrades stay clean.

## How this differs from Hermes itself (honest)

Hermes **v0.18** natively does multi-model **fallback**, **Mixture-of-Agents**, **pooled credentials** (`hermes auth`), an OAuth **proxy**, **cron**, model-independent **memory/skills/sessions**, and it can **run CLIs** (the `terminal` tool + the desktop app's terminal). We don't reinvent — and don't resell — any of that. What this plugin adds on top:

| Hermes gives you | This plugin adds |
|---|---|
| Governs API/OAuth model providers | Extends the same governance to **local AI CLIs**: detection, caps, usage, delegation with fallback |
| Fallback chain for transient errors | **One-command manual brain switch** for the hard-quota deaths Hermes treats as fatal — with cron re-pin + gateway restart |
| `hermes auth` credential pool (CLI) | A **dashboard UI** for pooling accounts/keys per provider, with get-key links |
| Scattered config subcommands + a terminal | **One control-plane dashboard** for the whole fleet: CLIs + models + media |
| Runs a CLI when the model decides to | **Deterministic, cap-aware `cli_delegate`** that doesn't depend on the model choosing to emit a tool call |
| No music framework | **`generate_music`** tool |

---

## Prerequisites

- macOS or Linux
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) installed (`hermes` on your `PATH`)
- Python 3.11+ (Hermes provides this in its venv)

---

## Install in 60 seconds

```bash
# 1. Confirm Hermes
hermes --version

# 2. Clone into Hermes's user-plugin directory (the dir name must be cli-orchestrator)
git clone https://github.com/srikanthmx/hermes-cli-orchestrator.git \
  ~/.hermes/plugins/cli-orchestrator

# 3. Enable
hermes plugins enable cli-orchestrator

# 4. Open the dashboard → "CLI Governor" tab
hermes dashboard
```

> Hermes only auto-imports a plugin's backend Python for `user`/`bundled` sources — installing under `~/.hermes/plugins/` is required for the dashboard API to mount. Open the dashboard from the URL Hermes launches — it carries the loopback token; a hand-typed tab bounces to login.

Enabling activates the usage hook, the intent-routing policy, the `cli_delegate` tool, the `/cli-*` commands, and `generate_music` on next start. (The dashboard tab works even without step 3.)

### The CLI Governor tab

Right after **Skills** in the left nav. Three top-level tabs:

- **Backends** — staged so nothing is padding:
  - **Fleet — verified & ready**: installed + verified (passed a live test), authed models, configured media. Only these are routable.
  - **Set up — verify or add a key**: detected/known but not promoted yet — run **Check sign-in**, finish sign-in, or paste a key (with a *get key* link).
  - **Install from catalog**: known CLIs you haven't installed — a guided step-by-step install, then verify to promote.

  A backend **rises into the Fleet only once it passes the live test.**
- **Brains** — see the primary + fallback chain and cooldowns, switch the primary (auto re-pins enabled crons, prompts a gateway restart), and pool multiple accounts/keys per provider.
- **Routing** — per use case (**Coding, Chat, Image, Audio, Video, Research, Docs, Automation, Other**), pick a primary + fallback among the **Fleet** backends enabled for that category (untested/uninstalled ones can't be routed).

Detection, limits, install, routing, and media keys all work with **no model configured**. Caps are prefilled from the catalog/provider tier; saved values override. Gemini CLI is treated as legacy (its free tier ended); the forward path for free Google workflows is the **Antigravity CLI (`agy`)**:

```bash
# macOS and Linux, from Google's Antigravity CLI install docs
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

```powershell
# Windows PowerShell
irm https://antigravity.google/cli/install.ps1 | iex
```

```bash
# then run once and complete Google's sign-in; test headlessly with:
agy -p "reply PONG"
```

The IDE (a separate product) installs as `antigravity-ide`; use the **Open app** button on its card to sign in. Setup docs: https://antigravity.google/docs/cli

### (Optional) bundled free media backends

This repo ships extra Hermes provider-plugins under `backends/`:

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

## Run it from your phone

All settings are reachable from any Hermes gateway (Telegram, Discord, …) via `cli-` prefixed slash commands:

| Command | Does |
|---------|------|
| `/cli-brain` | **brain status**: primary, fallbacks, cooldowns, ranked switchable brains |
| `/cli-brain <provider> [model]` | **switch the primary brain** — re-pins enabled crons + restarts the gateway |
| `/cli-brain test <provider>` | live-probe a brain with one real call (records/clears its cooldown) |
| `/cli-brain restart` | restart the gateway |
| `/cli-delegate <task>` | **run a task on a local worker CLI** (caps + fallback + usage) |
| `/cli-status` | CLI status + usage today |
| `/cli-usage` | provider / model (brain) usage |
| `/cli-scan` | re-detect installed CLIs |
| `/cli-limit <cli> <daily> [hourly] [monthly]` | set usage caps |
| `/cli-route <cli> <intent…>` | map an intent to a CLI |
| `/cli-routes` | list routing rules |
| `/cli-install <cli> [manager]` | install a CLI |
| `/cli-media` | media backend status |
| `/cli-help` | list commands |

`/cli-brain` is the **manual brain switch** — by design the plugin never changes your primary model on its own; it records cooldowns and warns, and you flip the brain from Telegram/chat in one command (no LLM in the loop, so it works while the brain is dead).

`/cli-delegate` is the reliable way to put a **local CLI to work** without depending on a weak model to emit a tool call — it routes to the highest-priority available CLI, skips any over its cap, falls back on rate-limit, and records the usage the dashboard shows.

`/cli <subcommand>` works too (e.g. `/cli brain copilot`, `/cli limit codex 200`) — handy because Telegram's command **menu** only autocompletes `[a-z0-9_]` names, so the hyphenated forms work when typed but may not appear in the `/` menu.

Gate access with `hermes pairing` (only authorized DMs) and per-platform slash controls. **Media API keys are intentionally *not* settable over chat** (they'd land in chat history) — set those on the loopback dashboard.

---

## Using the dashboard

| Action | Where | What happens |
|--------|-------|--------------|
| **Re-scan** | top-right | Re-probes `which` + versions + auth |
| **Switch brain** | Brains tab | Promotes a provider to primary, re-pins enabled crons, offers gateway restart |
| **Pool accounts** | Brains tab | Add/reset/remove multiple keys/accounts per provider (wraps `hermes auth`) |
| **Set caps** | Matrix limits column | Saves hourly/daily/monthly caps for CLI, provider, or media targets |
| **Install** | Matrix configure column | Runs the catalog install command, then the same row shows auth/key setup and verify |
| **Route use cases** | Routing tab | Saves explicit primary + fallback targets per category |
| **Add provider/media key** | Matrix configure column | Saves an API key to `~/.hermes/.env` (chmod 600); extra slots stored as `KEY_2`, `KEY_3`, … |
| **Authenticate CLI** | Matrix configure column | Shows the CLI-specific login command and docs; **Verify** after completing auth |
| **Quick status** | `/cli` in any session | Installed CLIs + today's usage |
| **Generate music** | `generate_music` tool | MusicGen via Replicate (needs `REPLICATE_API_TOKEN`) |

The **18-CLI catalog** (Claude Code, Codex, Antigravity `agy` + IDE, Gemini, Qwen, Copilot, OpenCode, Cursor, Amp, Crush, Goose, mods, llm, gh, glab, Ollama, Hermes) is kept current by the `catalog-refresh` skill — new CLIs get added and configured, dead ones get pruned (`aider` was pruned as an interactive-only pair-programmer, not a delegation worker).

---

## What's verified today (real runs, not "it imports")

| Capability | Backend | Status |
|---|---|---|
| **Brain switch round-trip** | `/cli-brain` (copilot → ollama → copilot) | ✅ **ran** — config rewritten, crons re-pinned both ways, gateway relaunched |
| **Brain probe** | `/cli-brain test copilot` | ✅ **ran** — real one-shot reply through the provider |
| **Code delegation** | `agy` via `cli_delegate` | ✅ **ran** — returned a correct function |
| **Image** | Pollinations (`backends/image_gen/pollinations`) | ✅ **ran** — real 256×256 JPEG, free, keyless |
| **Voice / TTS** | Edge TTS (`edge_tts`) | ✅ **ran** — real MP3, free, no key |
| **Speech-to-Text** | faster-whisper (`local`) | ✅ **ran** — transcribed the TTS audio back accurately |
| **Video** | fal + 9 more key-configurable | ⚙️ wired + registers; **NOT run — needs a key** |
| **Music** | `generate_music` (Replicate MusicGen) + 7 more | ⚙️ wired; **NOT run — needs a key** |

The media panel manages keys for **all** providers (fal, Replicate, Runway, Luma, Kling, MiniMax, Pika, Haiper, Veo, Sora; Suno, Udio, ElevenLabs Music, Stable Audio, Mubert, Loudly, Beatoven…). Video/music backends activate once you paste a key — fal and Replicate both offer free trial credits.

> **Honesty note:** backends labelled "unverified" were written against the providers' documented APIs and pass import/registration/error-path checks, but have **not** been run end-to-end (no key was available). They are not claimed to work until verified.

---

## State & customization

- Mutable state (limits, routing rules, usage events, cooldowns) lives in `~/.hermes/cli-orchestrator/state.json` — outside this repo.
- Provider and media API keys live in `~/.hermes/.env` (mode 600). Extra keys for the same env var are stored as `KEY_2`, `KEY_3`, … so the primary env var stays compatible with clients that expect exactly one token.
- Extend the CLI catalog by dropping a `catalog.json` (same shape as `DEFAULT_CATALOG` in `dashboard/plugin_api.py`) into `~/.hermes/cli-orchestrator/`.

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
| `dashboard/plugin_api.py` | Dashboard plugin | FastAPI `router` at `/api/plugins/cli-orchestrator/` — catalog/scan/limits/routing/install, live CLI tests, brain + gateway endpoints, credential pool, media keys |
| `dashboard/dist/index.js` | Dashboard plugin | Plain-JS UI, no build step (`window.__HERMES_PLUGIN_SDK__`) |
| `plugin.yaml` | General plugin | Declares hooks + tools |
| `__init__.py` | General plugin | Usage hooks, routing policy, cooldown recording, `cli_delegate`, `/cli-*` commands incl. `/cli-brain`, `generate_music` |
| `backends/` | Hermes provider-plugins | Pollinations image + fal video (install into Hermes's plugin tree) |

```
hermes-cli-orchestrator/
├── plugin.yaml
├── __init__.py                 # hooks + /cli-* commands + cli_delegate + generate_music
├── dashboard/
│   ├── manifest.json
│   ├── plugin_api.py           # control-plane API
│   └── dist/index.js           # dashboard UI (Backends / Brains / Routing)
├── backends/                   # extra Hermes provider-plugins
│   ├── image_gen/pollinations/ # free keyless image (working)
│   └── video_gen/fal/          # text-to-video (needs FAL_KEY)
└── README.md
```

---

## Limitations (honest)

- **Rate limits are observability guardrails** — tracked + surfaced, and enforced inside `cli_delegate`; but a plugin can't hard-veto the agent's own `terminal` calls (Hermes plugin `pre_tool_call` can't block; enforced blocking needs a `config.yaml` shell hook).
- **Routing decides, the model executes** — the policy reliably picks the right CLI, but a weak local model may *narrate* a delegation instead of calling the tool. A capable primary (Codex/Copilot/Gemini) executes reliably; `/cli-delegate` is the deterministic bypass.
- **Usage tracking** sees only CLIs the agent runs via `terminal` in plugin-enabled sessions (plus everything `cli_delegate` runs), and counts *invocations*, not tokens.
- **Auth status** is exact only for CLIs with a status command (`gh`, `glab`); others use a credentials-file check.
- **Slash commands don't dispatch in `hermes -z` oneshot mode** — they're gateway-platform commands (Telegram/Discord) and the dashboard covers the rest.
- **Media: video & music are unverified** pending a provider key (see the table above).

## Gaps & FAQ (read this)

**Q: I routed a CLI (e.g. amp) — why doesn't my chat / cron use it?**
Because a CLI is a **worker**, not a **brain**. The **Routing** tab drives `cli_delegate` (delegating *tasks* to CLIs). Chat and crons reason with a **model provider** (the brain) from `config.yaml`. A CLI can never be the chat/cron brain — set the brain in the **Brains** tab or with `/cli-brain`. (This is the single most common confusion.)

**Q: I switched the brain / added a key — nothing changed. Why?**
Two caches:
- **The gateway loads config once, at start.** `/cli-brain <provider>` restarts it for you; after a Brains-tab switch, click **Restart gateway**. Crons + chat run through the gateway.
- **Backend Python loads once, at process start.** If you change `plugin_api.py` / `__init__.py`, **restart the dashboard/gateway** — a browser refresh only reloads the JavaScript.

**Q: My cron says "Skipped… config drifted… unpinned" — is it broken?**
No — that's Hermes's **drift-guard (#44585)**: it refuses to run an *unpinned* cron after the global brain changes, as a spend-safety. Fix: **pin the cron** to a model. Both switch paths (Brains tab and `/cli-brain`) **auto-repin enabled crons**, so this shouldn't recur.

**Q: Codex is capped for ~a month and nothing rotates — isn't this "never hits a wall"?**
Two facts: (1) **Hermes does not fail over on a hard-quota 429** (Codex's "retry after N days") — it treats it as fatal, so adding fallbacks doesn't help; a healthy brain must be **promoted to primary**. (2) **Switching is deliberately manual** — the plugin never rewrites your primary on its own. When a brain caps, switch it yourself in seconds from anywhere: **`/cli-brain <provider>`** (Telegram/chat — no LLM in the loop, works even while the brain is dead) or the **Brains tab**. Both re-pin enabled crons and restart the gateway. `/cli-brain` shows status + cooldowns; `/cli-brain test <provider>` live-probes a brain and records/clears its cooldown (this is also how Codex's auth-layer quota death — invisible to plugin hooks — gets recorded).

**Q: The CLI catalog lists things I haven't verified — is that padding?**
No. The **Backends** tab is staged: **Install → Set up (verify / add key) → Fleet**. A backend only enters the **Fleet** (and becomes routable) once it **passes a live sample reply** (Check sign-in) / auth. The catalog is the browse+install surface; the Fleet is what actually works.

**Q: Does this work in the Hermes desktop app?**
The **runtime** (delegation, `/cli-*` commands incl. `/cli-brain`, hooks) works there — same backend. But the **dashboard tab is web-only**: the desktop app renders its own native UI and does not host dashboard plugins. Use `hermes dashboard` (browser) for the CLI Governor UI.

**Q: The "Restart gateway" button — will it always work?**
It uses `launchctl kickstart` on the `ai.hermes.gateway` launchd service (falls back to `pkill`, which launchd relaunches). If your gateway isn't run as that service, restart it however you started it.

### Not built yet (roadmap — don't expect these to work)

- **Background auto-*recording* of Codex/auth-layer exhaustion** (a log-watcher that records — never switches). Today, `/cli-brain test codex` records it on demand.
- **One-click free-first chain builder** (guided wizard for Qwen OAuth + OpenRouter `:free` + Gemini API key → auto-ranked fallback chain). Multi-account pooling itself is built (Brains tab).

---

## License

MIT
