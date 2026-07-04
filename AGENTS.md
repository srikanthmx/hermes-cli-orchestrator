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
- **Antigravity is TWO products:** the **CLI = `agy`** (headless via `agy -p "..."`, a real testable worker) and the **IDE = `antigravity-ide`** (a VS Code-style GUI launcher — "Open app", not headless).
- **Dashboard plugins render only in the web dashboard**, not the desktop app's native UI. The runtime plugin (hooks/tools/`/cli-*` commands) DOES work in the desktop (same backend). See `memory/desktop-vs-dashboard-plugins.md`.
- **Backend Python loads once, at process start.** A browser/app refresh loads new JS only. After ANY change to `dashboard/plugin_api.py` or `__init__.py`, the dashboard/gateway **must be RESTARTED** — say "restart the backend," never "reload." JS-only (`dashboard/dist/index.js`) changes just need a browser refresh.
- **Web dashboard token:** loopback API routes require `X-Hermes-Session-Token`. `hermes dashboard` needs a TTY (its TUI dies when backgrounded here). To run it headless: `uvicorn hermes_cli.web_server:app --port 9119` with `HERMES_DASHBOARD_SESSION_TOKEN=<tok>` set, then open `http://127.0.0.1:9119/?token=<tok>`.
- Verified worker CLIs on the dev machine: **codex, opencode, agy, gh** (live-tested). qwen CLI is **broken** (Node module error). gemini CLI dead.

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
| `__init__.py` | Runtime plugin: `post_tool_call` usage, `pre_llm_call` routing policy, `post_llm_call`, `cli_delegate` tool (+ `/cli-*` commands), `generate_music`. `DELEGATE_ARGV`/`CODING_PRIORITY` = worker fallback order. |
| `dashboard/plugin_api.py` | FastAPI backend at `/api/plugins/cli-orchestrator/`. Catalog (CLIs), providers catalog, media catalog, scan/limits/usage/install, `/install/assist` (AI help via a worker CLI, runs off the event loop), `/cli/test` + `/cli/open` (live sign-in check), `/capabilities`, `/verify-mark`, `/use-cases` (per-category routing), cooldown endpoints. |
| `dashboard/dist/index.js` | Dashboard UI (plain IIFE, no build). Top-level tabs: **Backends** (single unified config: CLIs+models+media, caps, keys, install stepper, per-backend category toggles, live Check sign-in, get-key links) and **Routing** (category-wise primary/fallback). |
| `dashboard/manifest.json` | Registers the CLI Governor tab (web dashboard only). |
| `backends/` | Bundled Hermes provider-plugins (pollinations image, fal video). |

---

## 5. Roadmap / open governance work (the actual value)

Priority order — these deliver the North Star:

1. **Auto-cooldown skip (the key gap).** Detect exhaustion signals ("retry in 28d", 429s) from a run, mark that bucket cooling, and drop it from the active chain until it's back — so switches are invisible and the user never waits on one dead bucket. (Cooldowns can be *recorded* today; auto-detection from run errors is NOT wired yet.)
2. **Free-first governed chain.** Build/rank `fallback_providers` across every addable bucket (free OAuth + free API + trials + subs as bonus), Ollama only if present. Native Hermes fallback + credential pooling does the rotation; the governor manages/ranks it.
3. **Multi-key / multi-account pooling** per provider (wraps `hermes auth add`) so each bucket is bigger.
4. **Crons ride the chain, not Codex.** Pin crons to the chain / a stable free provider; pinning also exempts them from the cron drift-guard (#44585) that silently skips unpinned jobs when config drifts.
5. `/cli-dashboard` command — one-click bridge from desktop/chat to the web UI.

---

## 6. Current live state (dev machine, keep updated)

- Authed model providers: **Codex, Copilot, Ollama** only. The ~10 free/trial providers need API keys (get-key links are in the UI).
- Verified worker CLIs: **codex, opencode, agy** (all delegation-capable); **gh** authed. agy is wired into `DELEGATE_ARGV`/`CODING_PRIORITY`.
- Crons: two jobs in `~/.hermes/cron/jobs.json` are **disabled** (`enabled:False`) and **unpinned** — they don't run and, when they did, burned Codex then died on the 28-day cap.
- The user has NOT set up any free provider keys yet — realizing the pool requires adding a few (Qwen login + OpenRouter `:free` + Gemini API is the fastest path to effectively-unlimited consistent crons without Ollama).
