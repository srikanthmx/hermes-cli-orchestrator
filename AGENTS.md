# AGENTS.md — Hermes CLI Governor (`cli-orchestrator`)

**Read this before doing anything. Do not re-derive the project's purpose — it's here.**

---

## 0. North Star (the whole point)

This plugin turns Hermes into a **governance layer over many cheap/free brains and CLIs** so the agent runs at ~$0 **and never hits a wall.**

The central idea: **there is no single "best brain." The *pool* is the brain.**

- Consistency and learning in Hermes are **model-independent** — memory, skills, session/journey persist no matter which backend answered a given call. So the brain can change under the hood on every call and it's still *the same agent* with the same memory and behavior.
- Therefore the goal is **not** "pick Codex or Ollama." It's: **stack as many independent quota buckets as possible and rotate across them, auto-skipping exhausted ones, so the user never stalls and never notices the switch.**
- Each of these is a **separate quota bucket**, and they add up:
  - Subscription providers: Codex (ChatGPT), Copilot — premium but **harshly capped**.
  - Free OAuth: Qwen-OAuth (~2000/day), Nous.
  - Free API: Gemini API, OpenRouter `:free`, HuggingFace, Z.ai — each its own cap.
  - Trial credits: NVIDIA, Novita, GMI.
  - **CLI workers via `cli_delegate`**: agy, opencode, codex-exec — *more* separate quota.
  - **Multiple keys/accounts per provider** — multiplies each bucket.
  - Ollama — **optional bonus for users who have it. NEVER the assumed floor** (most users won't set it up).

The plugin "boasts of a consistent brain and a governance layer that hides the difference between switched models." Every design decision must serve that. If a suggestion relies on one model, or assumes Ollama, it's wrong.

---

## 1. Core mental model — do NOT confuse these

| Concept | What it is | Where it lives |
|---|---|---|
| **Model / provider (the "brain")** | an LLM endpoint Hermes calls to reason | `config.yaml` `model` + `fallback_providers`; needs OAuth or an API key |
| **CLI (a "worker")** | a subprocess the governor **delegates tasks to** (`cli_delegate` / `/cli-delegate`) | detected on PATH; NOT an LLM endpoint |

- A CLI (agy, opencode, …) **can never appear in the models list** and **can't be a cron's brain** — it's a worker. It can do heavy work *inside* a run via `cli_delegate`, but the orchestrating brain must be a model/provider.
- "Consistent brain" = the model-independent memory/skills/session, **not** any single model.
- **Hermes can already RUN CLIs** (native `terminal` tool; the desktop app has an integrated xterm terminal + an "agent terminal"). So *executing* a CLI is not the differentiator. Our value is **governing** that execution — cap-aware fallback across CLIs, priority, usage tracking, and a deterministic path that doesn't rely on the model choosing to emit a `terminal` call.

## 1b. Positioning vs Hermes v0.18 (native vs ours — keep claims honest)

Installed/latest Hermes is **v0.18.0**. It NATIVELY has, so **do NOT sell these as the plugin's**:
- **`moa`** (Mixture of Agents — multi model/provider slots), **`fallback`** (fallback provider chain), **`auth`** (pooled provider credentials = multi-account), **`proxy`** (OpenAI-compatible proxy to OAuth providers), **`cron`**.
- Model-independent **memory / skills / sessions / learning**.
- **Running CLIs** (terminal tool + desktop terminal/agent-terminal).
- A media-provider framework (edge-tts, faster-whisper, image/video gen plugins).

**Genuine differentiators (what to actually sell):**
1. **Extends governance from model *providers* to your local *CLIs*.** Hermes governs API/OAuth model backends; this brings codex/agy/opencode/claude/cursor/etc. into the same regime — detect, cap, delegate with fallback, track usage.
2. **`cli_delegate` / `/cli-delegate`** — deterministic, cap-aware, auto-fallback delegation across CLIs. The reliable path when a weak brain would otherwise *narrate* a delegation instead of running it.
3. **A single control-plane dashboard** for the whole fleet — CLIs + models + media in one place: status, caps, keys (+ get-key links), guided install, per-category routing. Hermes has scattered config subcommands and a terminal, not this unified governance UI.
4. **Ops/health tooling per backend** — live "Check sign-in" (real sample call), install stepper + "Ask AI for help" (answered via a governed CLI), provenance labels (verified/catalog/custom), per-CLI caps + usage.
5. **`generate_music`** tool (Hermes has no music framework).

One-liner: **"The CLI & backend control plane for Hermes — extends Hermes's model governance to your local AI CLIs, with one dashboard to run the whole fleet."**

---

## 2. Hard-won facts (do not relearn these the hard way)

- **Codex limit is brutal:** ~3 cron iterations exhaust it, then a **~28-day cooldown**. Codex is NOT a workhorse for crons. Never design around Codex as the steady brain.
- **Ollama is optional**, never assumed. Not everyone installs it.
- **Gemini CLI free tier is DEAD** ("IneligibleTierError — migrate to Antigravity"). The **Gemini API key** path still works; use that, not the CLI.
- **`hermes-agent/` is no longer perfectly pristine:** it carries **one local commit** (88d1d620, cherry-picked upstream streaming fix for empty/None choices) on top of upstream f64e4f4f. The "never modify the Hermes tree" rule still stands for plugin work; expect `git pull` to need a rebase of that one commit until upstream ships it.
- **Antigravity is TWO products:** the **CLI = `agy`** (headless via `agy -p "..."`, a real testable worker) and the **IDE = `antigravity-ide`** (a VS Code-style GUI launcher — "Open app", not headless).
- **Dashboard plugins render only in the web dashboard**, not the desktop app's native UI. The runtime plugin (hooks/tools/`/cli-*` commands) DOES work in the desktop (same backend). See `memory/desktop-vs-dashboard-plugins.md`.
- **Backend Python loads once, at process start.** A browser/app refresh loads new JS only. After ANY change to `dashboard/plugin_api.py` or `__init__.py`, the dashboard/gateway **must be RESTARTED** — say "restart the backend," never "reload." JS-only (`dashboard/dist/index.js`) changes just need a browser refresh.
- **Web dashboard token:** loopback API routes require `X-Hermes-Session-Token`. `hermes dashboard` needs a TTY (its TUI dies when backgrounded here). To run it headless: `uvicorn hermes_cli.web_server:app --port 9119` with `HERMES_DASHBOARD_SESSION_TOKEN=<tok>` set, then open `http://127.0.0.1:9119/?token=<tok>`.
- Verified worker CLIs on the dev machine: **codex, opencode, agy, gh** (live-tested). qwen CLI is **still broken** (on PATH, but `qwen -p` crashes with a Node error under Node v24.13.1 — re-verified 2026-07-08). gemini CLI dead (binary present, free tier gone).

---

## 3. How to work in this repo (behaviors the user expects)

- **Verify against reality before claiming anything works.** Run the command, hit the endpoint, read the output. Never say "verified" for something untested. A 401 proves a route is *mounted*, not that a feature *works*.
- **Never modify the Hermes tree** (`hermes-agent/`). This plugin integrates only through documented plugin contracts so `git pull` upgrades stay clean. If a contract changes, fix THIS repo.
- **Commit + push progressively** after each verified feature (repo: `github.com/srikanthmx/hermes-cli-orchestrator`, branch `main`). End commit messages with the `Co-Authored-By` trailer.
- **Be accurate, not performative.** No "honestly/honest" filler. Don't over-apologize; state what happened and fix it. Don't narrate options you won't pursue.
- **`verified` label** in the UI = actually tested & working on this machine (auto-set by the live "Check sign-in", or built-in for proven integrations). `catalog` = known default, unverified. `custom` = user-added.
- Media API keys are set on the loopback dashboard only, never over chat.

---

## 4. Architecture map

| File | Role |
|---|---|
| `__init__.py` | Runtime plugin: `post_tool_call` usage, `pre_llm_call` routing policy (+ cooling-primary warning), `post_llm_call`, `api_request_error` (record-only cooldowns), `cli_delegate` tool (+ `/cli-*` commands incl. **`/cli-brain`** manual switch/probe/restart), `generate_music`. `DELEGATE_ARGV`/`CODING_PRIORITY` = worker fallback order. |
| `dashboard/plugin_api.py` | FastAPI backend at `/api/plugins/cli-orchestrator/`. Catalog (CLIs), providers catalog, media catalog, scan/limits/usage/install, `/install/assist` (AI help via a worker CLI, runs off the event loop), `/cli/test` + `/cli/open` (live sign-in check), `/capabilities`, `/verify-mark`, `/use-cases` (per-category routing), cooldown endpoints, **brain endpoints** (`/brain`, `/brain/primary` — promote provider to primary + re-pin enabled crons via `_repin_enabled_crons`, `/gateway/restart` via launchctl kickstart), **credential pool** (`/auth/pool`, `/auth/add-key`, `/auth/add-oauth`(+status), `/auth/reset`, `/auth/remove` — wraps `hermes auth`). |
| `dashboard/dist/index.js` | Dashboard UI (plain IIFE, no build). THREE top-level tabs: **Backends** (staged: catalog → install → verify → promote to Fleet; CLIs+models+media, caps, keys, install stepper, per-backend category toggles, live Check sign-in, get-key links, usage bars, search), **Brains** (switch primary model + restart gateway + auto-repin crons; pooled accounts add/reset/remove per brain), **Routing** (category-wise primary/fallback). |
| `dashboard/manifest.json` | Registers the CLI Governor tab (web dashboard only). |
| `backends/` | Bundled Hermes provider-plugins (pollinations image, fal video). |

---

## 5. Roadmap / open governance work (the actual value)

Priority order — these deliver the North Star (status audited 2026-07-08):

1. **Manual brain switch — BUILT 2026-07-08 (replaces the auto-heal plan).** `/cli-brain` status/switch/test/restart from any gateway platform; record-only cooldown hook; pre-turn warning. NO autonomous switching — user decision, see §5c. Remaining nice-to-have: a log-watcher that auto-*records* (never switches) auth-layer quota deaths so status is accurate without a manual `/cli-brain test`.
2. **Free-first governed chain.** Build/rank `fallback_providers` across every addable bucket (free OAuth + free API + trials + subs as bonus), Ollama only if present. Native Hermes fallback + credential pooling does the rotation; the governor manages/ranks it. Blocked in practice by zero free keys on the machine → needs the **Pool Builder wizard** (guided Qwen OAuth + OpenRouter `:free` + Gemini API key, each live-verified, then auto-rank the chain).
3. ~~Multi-key / multi-account pooling~~ **BUILT** — Brains tab + `/auth/*` endpoints wrap `hermes auth` (add key / add OAuth / reset / remove per provider). Pool currently holds only codex(1) + copilot(1); value realizes once free buckets are added.
4. **Crons ride the chain — BUILT.** Every switch path now re-pins all ENABLED crons to the new primary (drift-guard #44585 exemption) AND restarts the gateway: dashboard `/brain/primary` + `/gateway/restart`, and `/cli-brain <provider>` (verified round-trip 2026-07-08).
5. `/cli-dashboard` command — one-click bridge from desktop/chat to the web UI.

---

## 5b. Keeping the catalog current — the `catalog-refresh` skill

The catalog is meant to be **living**. `skills/catalog-refresh/SKILL.md` is the
procedure: research current AI CLIs + free/cheap providers, add/configure the ones
with a **headless mode** (so they can be delegate workers), **verify with a real
one-shot call before marking verified**, and **prune the dead weight**. Prune
criteria: no headless mode (interactive-only), dead free tier, superseded, or
heavy config with no governance benefit. Already actioned: **`aider` removed**
(interactive git-centric pair-programmer, not a stateless delegate worker).

Verified end-to-end this build (real runs, not endpoint checks): **code** (`agy`
wrote a correct function), **image** (Pollinations JPEG), **TTS** (edge_tts MP3),
**STT** (faster_whisper transcribed it back). NOT verified: video/music (need
keys), and see the chat finding below.

## 5c. Chat resilience — the key finding (2026-07)

Debugged end-to-end. Facts, in order:
1. `hermes -z "..."` died: **"Codex provider quota exhausted (429); retry after
   ~2.1M s (~25 days)"** and did NOT fail over.
2. The `fallback_providers` chain **is** correct/registered (Codex → Copilot →
   Ollama, per `hermes fallback list`). `fallback_providers` is the right key.
3. **Copilot works** as a brain when forced (`--provider copilot -m gpt-5.4` → replies).
4. But adding Copilot as a *fallback* did NOT resurrect chat — **Hermes does not
   fail over on Codex's hard "quota exhausted, retry in N days" 429 in oneshot
   mode.** It treats a hard quota as fatal, unlike a transient 429.
5. **Fix that worked: promote Copilot to PRIMARY** (`model.provider: copilot`).
   `hermes -z` → "Paris". Chat is working again.

**Design consequence for roadmap #1 (auto-cooldown): it must PROMOTE a working
provider to primary (swap `model.provider`/`model.default`), NOT just reorder the
fallback chain** — because Hermes-native fallback won't rotate off a hard-quota
primary.

### Brain switching is MANUAL — auto-heal was REMOVED (user decision 2026-07-08)
The user explicitly rejected autonomous switching: **the plugin must never
rewrite `model.provider` on its own.** The auto-heal engine (`_promote_primary`
/ `_maybe_restore_primary` / `on_session_start` trigger) was removed and
replaced with a manual-first design, all in `__init__.py`:

- **`/cli-brain` command family** (works from Telegram/chat; also `/cli brain …`).
  Dispatches directly with NO LLM, so it works even when the current brain is dead:
  - `/cli-brain` — status: primary, fallback chain, cooldowns w/ ETA, ranked
    switchable brains (`_BRAIN_CANDIDATES`: copilot → codex → gemini →
    openrouter → nous → ollama) with auth state.
  - `/cli-brain <provider> [model]` — `_switch_primary()`: promote to primary,
    demote old primary to front of fallbacks, **re-pin enabled crons** (reuses
    backend `_repin_enabled_crons`), clear the legacy `auto_heal` state key, and
    **restart the gateway ~5s after the reply flushes** (launchctl kickstart,
    pkill fallback; launchd relaunches). Warns if the target has a recorded cooldown.
  - `/cli-brain test <provider> [model]` — `_probe_provider()`: ONE real
    one-shot call (`hermes -z … --provider X -m Y`); records a cooldown on
    quota/rate-limit (this is how Codex's AUTH-layer death gets recorded — the
    probe catches it in its own subprocess), clears it on success. Spends 1 request.
  - `/cli-brain restart` — restart the gateway only.
- **`api_request_error` hook is RECORD-ONLY** now: on 429/quota it records the
  cooldown (accurate status + warnings), never switches.
- **`pre_llm_call` warning**: if the current primary has a recorded cooldown, a
  context line tells the model to suggest `/cli-brain <provider>` to the user.

Verified live 2026-07-08: status vs real config; probe copilot = real call, ✓
healthy 56s; full switch round-trip copilot → custom/ollama → copilot (config
rewritten with base_url, 1 enabled cron re-pinned each way, gateway restarted
by launchd both times). **Known limitation: slash commands do NOT dispatch in
`hermes -z` oneshot mode** (they go to the LLM) — they are gateway-platform
commands (Telegram etc.), which is the intended surface.

## 6. Current live state (dev machine, audited 2026-07-08)

- Primary = **copilot/gpt-5.4**; fallbacks = custom/ollama (qwen3.5) → openai-codex (cooldown recorded, **~20.8d left**). The legacy `auto_heal` state key was cleared — nothing restores Codex automatically; when its cooldown ends, `/cli-brain test openai-codex` then `/cli-brain openai-codex` if wanted.
- Authed model providers: **Codex, Copilot, Ollama** only (`auth.json` credential_pool: openai-codex×1, copilot×1). The ~10 free/trial providers still need API keys (get-key links in the UI).
- Worker CLIs on PATH: **codex, opencode, agy, gh, copilot, ollama** (verified earlier); **gemini** present but free tier dead; **qwen** present but headless crashes (Node v24.13.1); **claude** not installed.
- Crons in `~/.hermes/cron/jobs.json`: **"small-cap-stock-research-market-hours" is ENABLED and pinned to copilot/gpt-5.4** (re-pins automatically on every brain switch); "AI trending news + research briefing" disabled/unpinned.
- The user has NOT set up any free provider keys yet (`.env` has only Telegram vars) — realizing the pool requires adding a few (Qwen login + OpenRouter `:free` + Gemini API is the fastest path to effectively-unlimited consistent crons without Ollama).
