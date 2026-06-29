# Hermes CLI Orchestrator

A drop-in [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that adds a
**CLI Matrix** dashboard tab for managing your local terminal worker CLIs
(Claude Code, OpenAI Codex, Gemini, `gh`, Copilot, Aider, Ollama, …):

- **Detect** every CLI on the host (`which` + version probe) with live status — Online / Not Authenticated / Missing.
- **Auth status** for CLIs that expose it (e.g. `gh auth status`) or a credentials-file heuristic.
- **Rate-limit guardrails** — set hourly / daily / monthly caps per CLI.
- **Real usage tracking** — a `post_tool_call` hook tallies every time the agent drives a CLI through the `terminal` tool (gauges, "over cap" warnings).
- **One-click install** — runs a catalog install command (`npm i -g`, `brew install`, …), detached, with streamed logs.
- **Orchestration matrix** — map agent intents (Frontend, Version Control, …) to local CLIs.
- **`/cli` slash command** — a quick status read inside any Hermes session.

It integrates **only** through Hermes's two documented, stable plugin contracts, so
nothing in the Hermes codebase is modified and upstream upgrades stay clean.

---

## Prerequisites

- macOS or Linux
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) installed (the `hermes` command on your `PATH`)
- Python 3.11+ (Hermes already provides this in its venv)

---

## Step-by-step install

### 1. Confirm Hermes is installed

```bash
hermes --version
```

If you don't have it yet, follow the
[Hermes setup guide](https://github.com/NousResearch/hermes-agent#installation) first.

### 2. Clone this repo into your Hermes plugins directory

Hermes loads **user** plugins from `~/.hermes/plugins/<name>/`. The directory name
must be `cli-orchestrator`:

```bash
git clone https://github.com/srikanthmx/hermes-cli-orchestrator.git \
  ~/.hermes/plugins/cli-orchestrator
```

> **Why here and not a project folder?** Hermes only auto-imports a plugin's backend
> Python for `user`/`bundled` sources — not project (`./.hermes/plugins/`) sources.
> Installing under `~/.hermes/plugins/` is required for the dashboard API to mount.

*(Alternative: keep the repo anywhere you like and symlink it in —
`ln -s /path/to/hermes-cli-orchestrator ~/.hermes/plugins/cli-orchestrator`.)*

### 3. Enable the plugin

```bash
hermes plugins enable cli-orchestrator
hermes plugins list | grep cli-orchestrator    # should show: enabled
```

This activates the usage-tracking hook and the `/cli` slash command on the next
Hermes start. (The dashboard tab works even without this step.)

### 4. Launch the dashboard

```bash
hermes dashboard
```

The first launch builds the web UI once (cached afterward), then opens your
browser at `http://127.0.0.1:9119`.

> Open it from the URL Hermes prints/launches — it carries the loopback session
> token. A fresh tab typed by hand will bounce to a login screen.

### 5. Open the **CLI Matrix** tab

It appears in the left nav, right after **Skills**. You'll immediately see your
detected CLIs, statuses, and the orchestration matrix. **No model needs to be
configured** for detection, limits, install, or routing.

---

## Using it

| Action | Where | What happens |
|--------|-------|--------------|
| **Re-scan** | top-right button | Re-probes `which` + versions + auth |
| **Set caps** | per-CLI card | Saves hourly/daily/monthly to `state.json` |
| **Install** | per-CLI card (Missing) | Runs the catalog install command, streams logs |
| **Route intents** | Orchestration matrix | Saves intent → CLI rules |
| **Quick status** | `/cli` in any session | Prints installed CLIs + today's usage |

**Usage gauges** populate once you've configured a model (`hermes setup`) and the
agent actually runs a tracked CLI via the `terminal` tool during a session.

---

## State & customization

- Mutable state (limits, routing, rolling usage events) lives in
  **`~/.hermes/cli-orchestrator/state.json`** — outside this repo, so the plugin
  directory stays a clean git checkout.
- Extend the detected-CLI catalog by dropping a `catalog.json` (same shape as
  `DEFAULT_CATALOG` in `dashboard/plugin_api.py`) into
  `~/.hermes/cli-orchestrator/`.

---

## Keeping it updated

```bash
cd ~/.hermes/plugins/cli-orchestrator
git pull
hermes dashboard --stop && hermes dashboard   # reload backend routes
```

Because the backend computes `HERMES_HOME` itself and avoids importing Hermes
internals, it's decoupled from Hermes refactors. If a future Hermes release
changes a plugin contract, update **this** repo only — never the Hermes tree.

---

## How it works (architecture)

Two Hermes plugin contracts, one directory:

| File | Contract | Role |
|------|----------|------|
| `dashboard/manifest.json` | Dashboard plugin | Registers the **CLI Matrix** tab |
| `dashboard/plugin_api.py` | Dashboard plugin | FastAPI `router`, auto-mounted at `/api/plugins/cli-orchestrator/` |
| `dashboard/dist/index.js` | Dashboard plugin | React UI (no build step; uses `window.__HERMES_PLUGIN_SDK__`) |
| `plugin.yaml` | General plugin | Declares the runtime hook |
| `__init__.py` | General plugin | `post_tool_call` usage hook + `/cli` command |

```
hermes-cli-orchestrator/
├── plugin.yaml              # general-plugin manifest
├── __init__.py             # usage hook + /cli command
├── dashboard/
│   ├── manifest.json        # dashboard tab + UI/API wiring
│   ├── plugin_api.py        # FastAPI backend (self-contained)
│   └── dist/
│       └── index.js        # dashboard UI bundle
└── README.md
```

---

## Limitations (v0.1.0, honest)

- **Rate limits are observability guardrails** — tracked + surfaced (incl. an
  "over cap" warning), but not hard blocks. Hermes plugin `pre_tool_call` hooks
  can't veto a tool call; enforced blocking would need a Hermes-side shell hook
  in `config.yaml` (planned).
- **Usage tracking** sees only CLIs the agent runs via the `terminal` tool, in
  sessions where the plugin is enabled. CLIs you run by hand outside Hermes
  aren't counted.
- **Token/cost caps** count *invocations*, not tokens — external CLIs don't
  expose token usage to outside processes.
- **Auth status** is exact only for CLIs with a status command (`gh`, `glab`);
  others use a credentials-file presence heuristic.
- **`/install`** runs only commands from the built-in catalog (never arbitrary
  client input) and works best for non-interactive installers.

---

## License

MIT
