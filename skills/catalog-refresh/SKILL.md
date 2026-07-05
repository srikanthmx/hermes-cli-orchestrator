---
name: catalog-refresh
description: Research the current market of AI CLIs and free/cheap LLM providers, add/configure the good ones in the CLI Governor catalog, and prune ones that are useless for governed delegation. Use when the user wants to refresh, update, or evolve the plugin's catalog of CLIs/models.
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [catalog, cli, models, providers, research, governance, refresh, evolve]
    related_skills: []
---

# Catalog Refresh — keep the CLI Governor current with the market

The plugin's value is a **living** catalog. Models and CLIs change monthly (new
free tiers, dead ones, new agents). This skill researches what's out there,
adds/configures the ones that fit, verifies them, and removes dead weight.

## Where the catalog lives
- CLIs → `DEFAULT_CATALOG` in `dashboard/plugin_api.py`.
- Model providers → `PROVIDERS_CATALOG` in the same file.
- Media backends → `MEDIA_CATALOG` in the same file.
- Get-key links → each provider/media `signup` field + the `ENV_KEY_URL` map in `dashboard/dist/index.js`.
- Delegation workers → `DELEGATE_ARGV` + `CODING_PRIORITY` in `__init__.py`, and `_DELEGATE_PRIORITY` + `_CLI_TEST_ARGV` in `plugin_api.py`.
- Users can also override/extend via `~/.hermes/cli-orchestrator/catalog.json` (merged by id; entries there show as `custom`).

## Step 1 — Research (web)
Search for the *current* state (use the real month/year):
- AI coding/agent CLIs with a **headless / non-interactive** mode (e.g. `... -p`, `... run`, `... exec`).
- Free / free-tier / trial-credit / cheap LLM providers usable as a Hermes model provider (API key or OAuth).
Capture per candidate: exact **binary name**, **install command(s)** per OS, **headless one-shot invocation**, **auth method** (OAuth login vs API-key env var), **get-key URL**, and **free-tier limits / cooldown**.

## Step 2 — Decide: ADD, KEEP, or REMOVE
**Add a CLI** only if it has a *clean non-interactive headless mode* (so it can be a `cli_delegate` worker). If it's interactive-only, it can still be catalogued for detection but NOT added to `DELEGATE_ARGV`.
**Add a provider** if it's free/cheap/trial and the user can obtain a key/OAuth. Include `tier`, `signup`, `limit`.

**Remove / demote (prune criteria) — this is deliberate, keep the catalog lean:**
- **No headless mode** → useless as a governed delegate worker (interactive-only pair-programmers).
- **Dead free tier** (e.g. Gemini CLI's individual tier ended) → mark legacy or remove.
- **Superseded** by a better tool for the same job.
- **Heavy per-tool provider config with no governance benefit** over what's already covered.
- Example already actioned: **`aider`** removed — it's an interactive, git-repo-centric pair-programmer, not a clean stateless delegation worker for this plugin's use case.

## Step 3 — Verify before claiming (NEVER mark verified untested)
For any CLI you add or keep as a worker, run a **real** one-shot: `<bin> -p "Reply with the word PONG"` (or its headless flag). Only if it returns sensibly do you treat it as working. The dashboard's **Check sign-in** does exactly this and auto-marks `verified`. Do not write `"verified": True` into the catalog for something you did not run.

## Step 4 — Wire it in
- CLI: add to `DEFAULT_CATALOG` (id, name, bin, `install`, `auth`/`auth_command`, `provider`, `plan`, `docs`). If it has a headless mode, add it to `DELEGATE_ARGV` + `CODING_PRIORITY` (`__init__.py`), `_DELEGATE_PRIORITY` + `_CLI_TEST_ARGV` (`plugin_api.py`).
- Provider: add to `PROVIDERS_CATALOG` with `tier`, `env`, `signup`, `limit`; add the env→URL to `ENV_KEY_URL` if new.
- Update the CLI count in `README.md` and note changes in `AGENTS.md`.

## Step 5 — Report
Give the user a diff summary: **added** (with why + verified status), **kept**, **removed** (with the prune reason). Never silently drop something the user configured.

## Honesty rules (non-negotiable)
- Backend `.py` changes need a **dashboard/gateway restart** to take effect (not just a browser refresh).
- Don't claim a provider is "free" or a CLI "works" without checking the current source / running it.
- Keep get-key links accurate — a key field with no working link is a dead end.
