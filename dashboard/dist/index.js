/**
 * CLI Orchestrator dashboard.
 *
 * Plain IIFE, no build step. Uses Hermes dashboard globals:
 *   window.__HERMES_PLUGIN_SDK__
 *   window.__HERMES_PLUGINS__
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var React = SDK.React;
  var h = React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useCallback = SDK.hooks.useCallback;
  var C = SDK.components;
  var cn = (SDK.utils && SDK.utils.cn) || function () {
    return Array.prototype.filter.call(arguments, Boolean).join(" ");
  };

  var BASE = "/api/plugins/cli-orchestrator";
  var USE_CASE_ORDER = ["coding", "chat", "image", "audio", "video", "research", "docs", "automation", "other"];
  var USE_CASE_NAMES = {
    coding: "Coding",
    chat: "Chat",
    image: "Image",
    audio: "Audio",
    video: "Video",
    research: "Research",
    docs: "Docs",
    automation: "Automation",
    other: "Other",
  };

  function getJSON(path) {
    return SDK.fetchJSON(BASE + path);
  }

  function postJSON(path, body) {
    return SDK.fetchJSON(BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }

  function pill(text, tone) {
    var tones = {
      ok: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
      warn: "border-amber-500/40 bg-amber-500/10 text-amber-300",
      bad: "border-rose-500/40 bg-rose-500/10 text-rose-300",
      info: "border-sky-500/40 bg-sky-500/10 text-sky-300",
      neutral: "border-border bg-background/40 text-muted-foreground",
    };
    return h("span", {
      className: cn("inline-flex items-center whitespace-nowrap border px-2 py-0.5 text-[11px] font-courier uppercase", tones[tone] || tones.neutral),
    }, text);
  }

  function metric(value, label, tone) {
    return h("div", { className: "min-w-0" },
      h("div", { className: cn("truncate font-courier text-xl leading-tight", tone || "") }, value),
      h("div", { className: "text-[11px] uppercase tracking-wider text-muted-foreground" }, label));
  }

  function inputClass(extra) {
    return cn("border border-border bg-background/50 px-2 py-1 text-xs outline-none focus:border-emerald-500/60", extra || "");
  }

  function selectClass(extra) {
    return cn("border border-border bg-background/50 px-2 py-1 text-xs outline-none focus:border-emerald-500/60", extra || "");
  }

  function targetId(row) {
    return row.type + ":" + row.id;
  }

  function targetName(row) {
    return row.name || row.id;
  }

  function statusFor(row) {
    if (row.type === "cli") {
      if (!row.installed) return pill("missing", "neutral");
      if (row.status === "unauthenticated") return pill("auth needed", "warn");
      if (row.provider_env && !row.key_count) return pill("key optional", "info");
      return pill("ready", "ok");
    }
    if (row.type === "provider") {
      if (row.cooling_down) return pill("cooldown", "warn");
      return row.authed ? pill("ready", "ok") : pill(row.auth === "oauth" ? "login" : "no key", "neutral");
    }
    return row.configured ? pill("ready", "ok") : pill(row.needs_key ? "no key" : "not installed", "neutral");
  }

  // Provenance label: verified (proven/user-marked) · custom (user-added) ·
  // catalog (known default). Verified is user-togglable unless built-in.
  function ProvenanceTag(props) {
    var row = props.row;
    var prov = row.provenance;
    var busySt = useState(false); var busy = busySt[0], setBusy = busySt[1];
    if (!prov) return null; // only CLI catalog rows carry provenance
    var isVerified = prov === "verified";
    var tone = isVerified ? "ok" : prov === "custom" ? "info" : "neutral";
    var canToggle = !row.verified_builtin && prov !== "custom";
    function toggle() {
      setBusy(true);
      postJSON("/verify-mark", { id: targetId(row), verified: !isVerified })
        .then(function () { if (props.onChanged) props.onChanged(); })
        .catch(function () {})
        .finally(function () { setBusy(false); });
    }
    var label = isVerified ? "verified" : prov;
    if (!canToggle) {
      return pill(label, tone);
    }
    return h("button", {
      onClick: toggle,
      disabled: busy,
      title: isVerified ? "Marked verified — click to unmark" : "Mark verified (tested & works on this machine)",
      className: cn("inline-flex items-center whitespace-nowrap border px-2 py-0.5 text-[11px] font-courier uppercase disabled:opacity-40",
        isVerified
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20"
          : "border-border bg-background/40 text-muted-foreground hover:bg-foreground/10"),
    }, busy ? "…" : (isVerified ? "verified ✓" : "mark verified"));
  }

  // A CLI or model can serve any text-oriented category, not just coding.
  var TEXT_CATS = ["coding", "chat", "research", "docs", "automation", "other"];

  function buildTargets(clis, providers, media) {
    var rows = [];
    (clis || []).forEach(function (c) {
      rows.push(Object.assign({}, c, {
        type: "cli",
        isLocal: c.id === "ollama" || c.category === "Local Models",
        isDeprecated: !!c.deprecated,
        useCases: TEXT_CATS.slice(),
        routeMode: "cli",
      }));
    });
    (providers || []).forEach(function (p) {
      rows.push(Object.assign({}, p, {
        type: "provider",
        isLocal: p.id === "custom" || p.tier === "local",
        isDeprecated: false,
        useCases: TEXT_CATS.slice(),
        routeMode: "model",
      }));
    });
    (media || []).forEach(function (m) {
      var uc = ["other"];
      if (m.category === "Image") uc = ["image", "other"];
      else if (m.category === "Video") uc = ["video", "other"];
      else if (m.category === "Voice / TTS" || m.category === "Speech-to-Text" || m.category === "Music") uc = ["audio", "other"];
      rows.push(Object.assign({}, m, {
        type: "media",
        isLocal: false,
        isDeprecated: false,
        useCases: uc,
        routeMode: "media",
      }));
    });
    rows.sort(function (a, b) {
      if (!!a.isLocal !== !!b.isLocal) return a.isLocal ? 1 : -1;
      if (!!a.isDeprecated !== !!b.isDeprecated) return a.isDeprecated ? 1 : -1;
      var ar = a.installed || a.authed || a.configured ? 0 : 1;
      var br = b.installed || b.authed || b.configured ? 0 : 1;
      if (ar !== br) return ar - br;
      return targetName(a).localeCompare(targetName(b));
    });
    return rows;
  }

  function KeyInput(props) {
    var item = props.item;
    var endpoint = props.endpoint;
    var envs = item.env ? (Array.isArray(item.env) ? item.env : [item.env]) : [];
    if (!envs.length && item.provider_env) envs = [item.provider_env];
    var env = envs[0] || "";
    var valueSt = useState(""); var value = valueSt[0], setValue = valueSt[1];
    var appendSt = useState(true); var append = appendSt[0], setAppend = appendSt[1];
    var busySt = useState(false); var busy = busySt[0], setBusy = busySt[1];
    var savedSt = useState(false); var saved = savedSt[0], setSaved = savedSt[1];
    if (!env) return h("span", { className: "text-xs text-muted-foreground" }, "No key required");
    function save() {
      if (!value.trim()) return;
      setBusy(true); setSaved(false);
      postJSON(endpoint, { env: env, value: value.trim(), append: append })
        .then(function () { setValue(""); setSaved(true); if (props.onChanged) props.onChanged(); })
        .catch(function () {})
        .finally(function () { setBusy(false); });
    }
    return h("div", { className: "flex min-w-[260px] flex-col gap-1" },
      h("div", { className: "flex items-center gap-2" },
        h("input", {
          type: "password",
          value: value,
          placeholder: env,
          onChange: function (e) { setValue(e.target.value); setSaved(false); },
          className: inputClass("min-w-0 flex-1 font-courier"),
        }),
        h("button", {
          onClick: save,
          disabled: busy || !value.trim(),
          className: "border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-xs font-courier text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-40",
        }, busy ? "..." : saved ? "saved" : "save")
      ),
      h("label", { className: "flex items-center gap-2 text-[11px] text-muted-foreground" },
        h("input", {
          type: "checkbox",
          checked: append,
          onChange: function (e) { setAppend(e.target.checked); },
        }),
        "append as another slot"));
  }

  function CopyCode(props) {
    var copiedSt = useState(false); var copied = copiedSt[0], setCopied = copiedSt[1];
    var text = props.text || "";
    function copy() {
      try {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(function () { setCopied(false); }, 1200);
      } catch (e) {}
    }
    return h("div", { className: "flex items-stretch gap-1" },
      h("code", {
        className: "min-w-0 flex-1 select-all overflow-x-auto whitespace-pre border border-border bg-background/40 px-2 py-1 font-courier text-[11px] text-muted-foreground",
      }, text),
      h("button", {
        onClick: copy,
        className: "shrink-0 border border-border px-2 text-[11px] font-courier hover:bg-foreground/10",
      }, copied ? "✓" : "copy"));
  }

  function stepRow(n, title, tone, body) {
    return h("div", { className: "flex gap-2" },
      h("div", {
        className: cn("flex h-5 w-5 shrink-0 items-center justify-center border font-courier text-[10px]",
          tone || "border-border text-muted-foreground"),
      }, n),
      h("div", { className: "flex min-w-0 flex-1 flex-col gap-1" },
        h("div", { className: "font-courier text-[11px] text-foreground" }, title),
        body));
  }

  // "Get help from AI" — routes the install question through a governed worker
  // CLI (codex/claude/qwen/opencode, or Ollama floor) via /install/assist.
  function AiHelp(props) {
    var openSt = useState(false); var open = openSt[0], setOpen = openSt[1];
    var busySt = useState(false); var busy = busySt[0], setBusy = busySt[1];
    var ansSt = useState(null); var ans = ansSt[0], setAns = ansSt[1];
    function ask() {
      setBusy(true); setOpen(true); setAns(null);
      postJSON("/install/assist", {
        id: props.row.id,
        manager: props.manager || "",
        log: props.log || "",
        question: props.question || "",
      })
        .then(function (res) { setAns(res || { ok: false, text: "No response" }); })
        .catch(function (e) { setAns({ ok: false, text: "Failed: " + e }); })
        .finally(function () { setBusy(false); });
    }
    return h("div", { className: "flex flex-col gap-1" },
      h("button", {
        onClick: ask,
        disabled: busy,
        className: "self-start border border-sky-500/40 bg-sky-500/10 px-2 py-1 text-[11px] font-courier text-sky-300 hover:bg-sky-500/20 disabled:opacity-40",
      }, busy ? "asking AI…" : (props.label || "Ask AI for help")),
      open && ans ? h("div", { className: "flex flex-col gap-1 border border-sky-500/30 bg-sky-500/5 p-2" },
        h("div", { className: "flex items-center justify-between" },
          h("span", { className: "text-[10px] uppercase tracking-wider text-sky-300" },
            "AI" + (ans.worker ? " · via " + ans.worker : "")),
          h("button", {
            onClick: function () { setOpen(false); },
            className: "text-[11px] text-muted-foreground hover:text-foreground",
          }, "hide")),
        h("pre", {
          className: "max-h-72 overflow-auto whitespace-pre-wrap font-courier text-[11px] text-muted-foreground",
        }, ans.text || "")) : null);
  }

  function InstallStepper(props) {
    var row = props.row;
    var onChanged = props.onChanged;
    var options = (row.install_options && row.install_options.length)
      ? row.install_options
      : (row.install_managers || []).map(function (m) {
          return { manager: m, label: m, command: "", executable: true, platforms: [] };
        });
    var runnable = options.filter(function (o) { return o.executable; });
    var initial = (runnable[0] || options[0] || {}).manager || "";
    var mgrSt = useState(initial); var mgr = mgrSt[0], setMgr = mgrSt[1];
    var installingSt = useState(false); var installing = installingSt[0], setInstalling = installingSt[1];
    var logSt = useState(""); var log = logSt[0], setLog = logSt[1];
    var doneSt = useState(false); var done = doneSt[0], setDone = doneSt[1];
    var opt = options.filter(function (o) { return o.manager === mgr; })[0] || options[0] || {};

    useEffect(function () {
      if (!installing) return undefined;
      var active = true;
      var timer = setInterval(function () {
        getJSON("/install/status?id=" + encodeURIComponent(row.id)).then(function (s) {
          if (!active) return;
          if (s.log) setLog(s.log);
          if (s.installed) setDone(true);
          if (!s.running) setInstalling(false);
        }).catch(function () {});
      }, 2000);
      return function () { active = false; clearInterval(timer); };
    }, [installing, row.id]);

    function startInstall() {
      if (!opt.executable) return;
      setDone(false);
      setLog("$ " + (opt.command || "") + "\n\nstarting…");
      setInstalling(true);
      postJSON("/install", { id: row.id, manager: mgr }).catch(function (e) {
        setLog("could not start install: " + e);
        setInstalling(false);
      });
    }

    if (!options.length) {
      return h("div", { className: "flex min-w-[240px] flex-col gap-2" },
        h("span", { className: "text-xs text-muted-foreground" }, "No installer registered."),
        row.docs ? h("a", { href: row.docs, target: "_blank", rel: "noreferrer",
          className: "text-[11px] text-muted-foreground underline hover:text-foreground" }, "setup docs") : null,
        h(AiHelp, { row: row }));
    }

    // Prerequisite step for the selected manager.
    var prereqBody;
    if (opt.prereq && opt.prereq_ok === false) {
      prereqBody = h("div", { className: "flex flex-col gap-1" },
        h("span", { className: "text-[11px] text-amber-300" }, "Needs " + opt.prereq + " — not found on PATH."),
        opt.prereq_check ? h(CopyCode, { text: opt.prereq_check }) : null,
        opt.prereq_get ? h("a", { href: opt.prereq_get, target: "_blank", rel: "noreferrer",
          className: "text-[11px] text-muted-foreground underline hover:text-foreground" }, "get " + opt.prereq) : null);
    } else if (opt.prereq && opt.prereq_ok === true) {
      prereqBody = h("span", { className: "text-[11px] text-emerald-300" }, opt.prereq + " detected ✓");
    } else if (opt.prereq) {
      prereqBody = h("span", { className: "text-[11px] text-muted-foreground" }, "Uses " + opt.prereq + ".");
    } else {
      prereqBody = h("span", { className: "text-[11px] text-muted-foreground" }, "No prerequisite.");
    }

    var prereqTone = opt.prereq_ok === false ? "border-amber-500/50 text-amber-300"
      : opt.prereq_ok === true ? "border-emerald-500/50 text-emerald-300" : null;

    return h("div", { className: "flex min-w-[300px] max-w-[380px] flex-col gap-3" },
      // Manager picker
      options.length > 1 ? h("div", { className: "flex flex-wrap gap-1" }, options.map(function (o) {
        return h("button", {
          key: o.manager,
          onClick: function () { setMgr(o.manager); },
          title: o.command || o.label,
          className: cn("border px-2 py-1 text-[11px] font-courier",
            o.manager === mgr ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-200" : "border-border text-muted-foreground hover:bg-foreground/10",
            o.executable ? "" : "opacity-60"),
        }, o.label + (o.executable ? "" : " (other OS)"));
      })) : null,

      stepRow("1", "Prerequisite", prereqTone, prereqBody),

      stepRow("2", "Install", done ? "border-emerald-500/50 text-emerald-300" : null,
        h("div", { className: "flex flex-col gap-1" },
          opt.command ? h(CopyCode, { text: opt.command }) : null,
          !opt.executable && opt.platforms && opt.platforms.length
            ? h("span", { className: "text-[11px] text-amber-300" }, "Command is for " + opt.platforms.join(", ") + " — copy & run it there.")
            : h("div", { className: "flex items-center gap-2" },
                h("button", {
                  onClick: startInstall,
                  disabled: installing || done,
                  className: "border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-[11px] font-courier text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-40",
                }, done ? "installed ✓" : installing ? "installing…" : "Install now"),
                installing ? h("span", { className: "text-[11px] text-muted-foreground" }, "running — logs below") : null),
          log ? h("pre", {
            className: "max-h-40 overflow-auto whitespace-pre-wrap border border-border bg-background/40 p-2 font-courier text-[10px] text-muted-foreground",
          }, log) : null)),

      (row.auth_command || row.auth_hint)
        ? stepRow("3", "Authenticate", null, h("div", { className: "flex flex-col gap-1" },
            row.auth_command ? h(CopyCode, { text: row.auth_command }) : null,
            row.auth_hint ? h("span", { className: "text-[11px] text-muted-foreground" }, row.auth_hint) : null))
        : null,

      stepRow(row.auth_command || row.auth_hint ? "4" : "3", "Verify", null,
        h("button", {
          onClick: onChanged,
          className: "self-start border border-border px-2 py-1 text-[11px] font-courier hover:bg-foreground/10",
        }, "Re-scan")),

      h("div", { className: "flex flex-wrap items-center gap-2 border-t border-border/60 pt-2" },
        h(AiHelp, { row: row, manager: mgr, log: log }),
        row.docs ? h("a", { href: row.docs, target: "_blank", rel: "noreferrer",
          className: "text-[11px] text-muted-foreground underline hover:text-foreground" }, "setup docs") : null));
  }

  function CliConfigure(props) {
    var row = props.row;
    var onChanged = props.onChanged;
    if (!row.installed) {
      return h(InstallStepper, { row: row, onChanged: onChanged });
    }
    return h("div", { className: "flex min-w-[260px] flex-col gap-2" },
      row.auth !== "authenticated" && (row.auth_command || row.auth_hint)
        ? h("div", { className: "flex flex-col gap-1 border border-amber-500/30 bg-amber-500/10 p-2" },
            h("div", { className: "text-[11px] uppercase tracking-wider text-amber-300" }, "Auth required"),
            row.auth_command ? h(CopyCode, { text: row.auth_command }) : null,
            row.auth_hint ? h("div", { className: "text-[11px] text-muted-foreground" }, row.auth_hint) : null)
        : null,
      row.provider_env
        ? h(KeyInput, { item: { env: [row.provider_env] }, endpoint: "/providers/key", onChanged: onChanged })
        : null,
      row.docs ? h("a", {
          href: row.docs,
          target: "_blank",
          rel: "noreferrer",
          className: "text-[11px] text-muted-foreground underline hover:text-foreground",
        }, "setup docs") : null,
      h("div", { className: "flex flex-wrap items-center gap-2" },
        h("button", {
          onClick: onChanged,
          className: "self-start border border-border px-2 py-1 text-xs font-courier hover:bg-foreground/10",
        }, "verify"),
        (row.auth !== "authenticated" && (row.auth_command || row.auth_hint))
          ? h(AiHelp, { row: row, question: "I ran the install but authentication isn't working. How do I complete auth and verify it?" })
          : null),
      !row.provider_env && row.auth === "authenticated"
        ? h("span", { className: "text-xs text-muted-foreground" }, "Ready")
        : null);
  }

  // ── Single, unified backend config (CLIs + models + media in ONE place) ──
  // Configure a backend once here; category routing below just references it.
  function BackendsConfig(props) {
    var targets = props.targets || [];
    var capSt = useState({}); var caps = capSt[0], setCaps = capSt[1];
    var msgSt = useState(""); var msg = msgSt[0], setMsg = msgSt[1];
    var filterSt = useState("all"); var filter = filterSt[0], setFilter = filterSt[1];

    function capValue(row, field) {
      var key = targetId(row) + ":" + field;
      if (caps[key] !== undefined) return caps[key];
      return ((row.limits || {})[field]) || "";
    }
    function setCap(row, field, value) {
      var key = targetId(row) + ":" + field;
      var next = {}; next[key] = value;
      setCaps(Object.assign({}, caps, next));
    }
    function saveCaps(row) {
      postJSON("/limits", {
        id: targetId(row),
        hourly: parseInt(capValue(row, "hourly"), 10) || 0,
        daily: parseInt(capValue(row, "daily"), 10) || 0,
        monthly: parseInt(capValue(row, "monthly"), 10) || 0,
      }).then(function () { setMsg("Limits saved for " + targetName(row)); if (props.onChanged) props.onChanged(); })
        .catch(function (e) { setMsg("Limit save failed: " + e); });
    }

    var kinds = [["all", "All"], ["cli", "CLIs"], ["provider", "Models"], ["media", "Media"]];
    var rows = targets.filter(function (t) { return filter === "all" || t.type === filter; });

    return h(C.Card, null,
      h(C.CardHeader, { className: "pb-2" },
        h("div", { className: "flex flex-wrap items-center justify-between gap-3" },
          h("div", { className: "min-w-0" },
            h(C.CardTitle, { className: "font-courier text-base" }, "Backends — configure once"),
            h("div", { className: "text-[11px] text-muted-foreground" }, "CLIs, models and media in one place. Caps, keys and install live here; routing is per category below.")),
          h("div", { className: "flex flex-wrap items-center gap-2" },
            msg ? h("span", { className: "text-xs text-muted-foreground" }, msg) : null,
            h("div", { className: "flex gap-1" }, kinds.map(function (k) {
              return h("button", {
                key: k[0], onClick: function () { setFilter(k[0]); },
                className: cn("border px-2 py-1 text-[11px] font-courier",
                  filter === k[0] ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-200" : "border-border text-muted-foreground hover:bg-foreground/10"),
              }, k[1]);
            })),
            h("button", {
              onClick: props.onChanged,
              className: "border border-border bg-background/40 px-3 py-1 text-xs font-courier hover:bg-foreground/10",
            }, "Re-scan")))),
      h(C.CardContent, { className: "overflow-x-auto" },
        h("table", { className: "w-full min-w-[1040px] border-collapse text-sm" },
          h("thead", null,
            h("tr", { className: "border-b border-border text-left text-[11px] uppercase tracking-wider text-muted-foreground" },
              h("th", { className: "py-2 pr-3" }, "Backend"),
              h("th", { className: "py-2 pr-3" }, "Status"),
              h("th", { className: "py-2 pr-3" }, "Serves"),
              h("th", { className: "py-2 pr-3" }, "Plan / capability"),
              h("th", { className: "py-2 pr-3" }, "Credential pool"),
              h("th", { className: "py-2 pr-3" }, "Usage"),
              h("th", { className: "py-2 pr-3" }, "Limits"),
              h("th", { className: "py-2" }, "Configure"))),
          h("tbody", null,
            rows.map(function (row) {
              var isCli = row.type === "cli";
              var isProvider = row.type === "provider";
              var isMedia = row.type === "media";
              var slots = row.key_count || 0;
              var usage = row.usage || {};
              var source = row.limits && row.limits.source === "custom" ? "custom" : "prefill";
              return h("tr", { key: targetId(row), className: "border-b border-border/60 align-top" },
                h("td", { className: "py-3 pr-3" },
                  h("div", { className: "flex flex-wrap items-center gap-2" },
                    pill(row.type, isCli ? "ok" : isProvider ? "info" : "warn"),
                    h(ProvenanceTag, { row: row, onChanged: props.onChanged }),
                    row.isDeprecated ? pill("legacy", "warn") : null,
                    h("div", { className: "min-w-0" },
                      h("div", { className: "font-courier text-sm" }, targetName(row)),
                      h("div", { className: "truncate text-[11px] text-muted-foreground" }, row.bin || row.model || row.category || "")))),
                h("td", { className: "py-3 pr-3" }, statusFor(row)),
                h("td", { className: "py-3 pr-3" },
                  isMedia
                    ? h("span", { className: "text-[11px] text-muted-foreground" }, row.category || "media")
                    : h("div", { className: "flex flex-wrap gap-1" }, (row.useCases || []).filter(function (u) { return u !== "other"; }).map(function (u) {
                        return h("span", { key: u, className: "border border-border bg-background/40 px-1.5 py-0.5 text-[10px] font-courier text-muted-foreground" }, USE_CASE_NAMES[u] || u);
                      }))),
                h("td", { className: "max-w-[240px] py-3 pr-3 text-xs text-muted-foreground" },
                  row.plan || row.limit || row.mechanism || row.category || ""),
                h("td", { className: "py-3 pr-3" },
                  row.env && (Array.isArray(row.env) ? row.env.length : row.env)
                    ? h("div", { className: "flex flex-col gap-1" },
                        pill(slots + " slot" + (slots === 1 ? "" : "s"), slots ? "ok" : "neutral"),
                        h("span", { className: "font-courier text-[11px] text-muted-foreground" },
                          Array.isArray(row.env) ? row.env.join(", ") : row.env))
                    : h("span", { className: "text-xs text-muted-foreground" }, "Keyless / local")),
                h("td", { className: "py-3 pr-3" },
                  h("div", { className: "flex flex-col gap-0.5 font-courier text-[11px] text-muted-foreground" },
                    h("span", null, "hour " + (usage.hour || 0) + " / " + capValue(row, "hourly")),
                    h("span", null, "day " + (usage.day || 0) + " / " + capValue(row, "daily")),
                    h("span", null, "month " + (usage.month || 0) + " / " + capValue(row, "monthly")))),
                h("td", { className: "py-3 pr-3" },
                  h("div", { className: "flex flex-col gap-1" },
                    h("div", { className: "flex items-center gap-1" },
                      ["hourly", "daily", "monthly"].map(function (f) {
                        return h("input", {
                          key: f, type: "number", min: 1, title: f,
                          value: capValue(row, f),
                          onChange: function (e) { setCap(row, f, e.target.value); },
                          className: inputClass("w-16 font-courier"),
                        });
                      }),
                      h("button", {
                        onClick: function () { saveCaps(row); },
                        className: "border border-border px-2 py-1 text-xs font-courier hover:bg-foreground/10",
                      }, "save")),
                    h("span", { className: "text-[11px] text-muted-foreground" }, source))),
                h("td", { className: "py-3" },
                  isCli
                    ? h(CliConfigure, { row: row, onChanged: props.onChanged })
                    : isProvider
                      ? h(KeyInput, { item: row, endpoint: "/providers/key", onChanged: props.onChanged })
                      : isMedia
                        ? h(KeyInput, { item: row, endpoint: "/media/key", onChanged: props.onChanged })
                        : h("span", { className: "text-xs text-muted-foreground" }, "Configured")));
            })))));
  }

  // ── Category-wise routing: pick primary + fallback among eligible backends ──
  function CategoryMatrix(props) {
    var useCase = props.useCase;
    var rows = (props.targets || []).filter(function (t) { return t.useCases.indexOf(useCase) >= 0; });
    var route = (props.routes || []).filter(function (r) { return r.use_case === useCase; })[0] || {
      use_case: useCase,
      mode: (props.useCaseDef && props.useCaseDef.default_mode) || "model",
      target: rows[0] ? targetId(rows[0]) : "",
      fallback: rows[1] ? targetId(rows[1]) : "",
      enabled: true,
    };
    var routeBusySt = useState(false); var routeBusy = routeBusySt[0], setRouteBusy = routeBusySt[1];
    var routeSavedSt = useState(false); var routeSaved = routeSavedSt[0], setRouteSaved = routeSavedSt[1];
    var msgSt = useState(""); var msg = msgSt[0], setMsg = msgSt[1];

    function saveRoute(patch) {
      var nextRoute = Object.assign({}, route, patch || {});
      var found = false;
      var next = (props.routes || []).map(function (r) {
        if (r.use_case === useCase) { found = true; return nextRoute; }
        return r;
      });
      if (!found) next.push(nextRoute);
      setRouteBusy(true); setRouteSaved(false);
      return postJSON("/use-cases", { routes: next })
        .then(function (res) {
          setRouteSaved(true); setMsg("Route saved");
          if (props.onRoutesChanged) props.onRoutesChanged((res && res.routes) || next);
        })
        .catch(function (e) { setMsg("Route failed: " + e); })
        .finally(function () { setRouteBusy(false); });
    }
    function optLabel(t) { return t.type + " / " + targetName(t); }

    return h(C.Card, null,
      h(C.CardHeader, { className: "pb-2" },
        h("div", { className: "flex flex-col gap-3" },
          h("div", { className: "flex flex-wrap items-center justify-between gap-3" },
            h(C.CardTitle, { className: "font-courier text-base" }, (USE_CASE_NAMES[useCase] || useCase) + " routing"),
            h("div", { className: "flex items-center gap-2" },
              pill(rows.filter(function (r) {
                if (r.type === "cli") return r.installed && r.status !== "unauthenticated";
                return r.authed || r.configured;
              }).length + "/" + rows.length + " ready", "info"),
              routeSaved ? pill("route saved", "ok") : null,
              msg ? h("span", { className: "text-xs text-muted-foreground" }, msg) : null)),
          h("div", { className: "grid grid-cols-1 gap-2 md:grid-cols-[120px_1fr_1fr_auto] md:items-center" },
            h("select", {
              value: route.mode || "model",
              onChange: function (e) { saveRoute({ mode: e.target.value }); },
              className: selectClass("font-courier"),
            }, ["cli", "model", "media"].map(function (m) { return h("option", { key: m, value: m }, m); })),
            h("select", {
              value: route.target || "",
              onChange: function (e) { saveRoute({ target: e.target.value }); },
              className: selectClass("w-full"),
            }, [h("option", { key: "", value: "" }, "Primary target")].concat(rows.map(function (t) {
              return h("option", { key: targetId(t), value: targetId(t) }, optLabel(t));
            }))),
            h("select", {
              value: route.fallback || "",
              onChange: function (e) { saveRoute({ fallback: e.target.value }); },
              className: selectClass("w-full"),
            }, [h("option", { key: "", value: "" }, "No fallback")].concat(rows.map(function (t) {
              return h("option", { key: targetId(t), value: targetId(t) }, optLabel(t));
            }))),
            h("label", { className: "flex items-center gap-2 text-xs text-muted-foreground" },
              h("input", {
                type: "checkbox",
                checked: route.enabled !== false,
                onChange: function (e) { saveRoute({ enabled: e.target.checked }); },
              }),
              routeBusy ? "Saving" : "Enabled")))),
      h(C.CardContent, { className: "overflow-x-auto" },
        rows.length === 0
          ? h("div", { className: "py-4 text-sm text-muted-foreground" }, "No backends serve this category yet — configure one in Backends above.")
          : h("table", { className: "w-full min-w-[720px] border-collapse text-sm" },
              h("thead", null,
                h("tr", { className: "border-b border-border text-left text-[11px] uppercase tracking-wider text-muted-foreground" },
                  h("th", { className: "py-2 pr-3" }, "Backend"),
                  h("th", { className: "py-2 pr-3" }, "Status"),
                  h("th", { className: "py-2 pr-3" }, "Usage (d / m)"),
                  h("th", { className: "py-2" }, "Route"))),
              h("tbody", null,
                rows.map(function (row) {
                  var tid = targetId(row);
                  var usage = row.usage || {};
                  return h("tr", { key: tid, className: "border-b border-border/60 align-top" },
                    h("td", { className: "py-3 pr-3" },
                      h("div", { className: "flex flex-wrap items-center gap-2" },
                        pill(row.type, row.type === "cli" ? "ok" : row.type === "provider" ? "info" : "warn"),
                        row.isDeprecated ? pill("legacy", "warn") : null,
                        h("span", { className: "font-courier text-sm" }, targetName(row)))),
                    h("td", { className: "py-3 pr-3" }, statusFor(row)),
                    h("td", { className: "py-3 pr-3 font-courier text-[11px] text-muted-foreground" },
                      (usage.day || 0) + " / " + (usage.month || 0)),
                    h("td", { className: "py-3" },
                      h("div", { className: "flex flex-wrap gap-1" },
                        route.target === tid ? pill("primary", "ok") : h("button", {
                          onClick: function () { saveRoute({ target: tid, mode: row.routeMode || route.mode || "model" }); },
                          disabled: routeBusy,
                          className: "border border-border px-2 py-1 text-xs font-courier hover:bg-foreground/10 disabled:opacity-40",
                        }, "primary"),
                        route.fallback === tid ? pill("fallback", "info") : h("button", {
                          onClick: function () { saveRoute({ fallback: tid }); },
                          disabled: routeBusy,
                          className: "border border-border px-2 py-1 text-xs font-courier hover:bg-foreground/10 disabled:opacity-40",
                        }, "fallback"))));
                })))));
  }

  function App() {
    var clisSt = useState([]); var clis = clisSt[0], setClis = clisSt[1];
    var mediaSt = useState([]); var media = mediaSt[0], setMedia = mediaSt[1];
    var providersSt = useState([]); var providers = providersSt[0], setProviders = providersSt[1];
    var healthSt = useState(null); var health = healthSt[0], setHealth = healthSt[1];
    var useCasesSt = useState([]); var useCases = useCasesSt[0], setUseCases = useCasesSt[1];
    var routesSt = useState([]); var routes = routesSt[0], setRoutes = routesSt[1];
    var customLocalSt = useState(false); var customLocal = customLocalSt[0], setCustomLocal = customLocalSt[1];
    var activeSt = useState("coding"); var active = activeSt[0], setActive = activeSt[1];
    var loadingSt = useState(true); var loading = loadingSt[0], setLoading = loadingSt[1];
    var errSt = useState(""); var err = errSt[0], setErr = errSt[1];

    var load = useCallback(function () {
      setLoading(true); setErr("");
      return Promise.all([
        getJSON("/scan"),
        getJSON("/media/scan"),
        getJSON("/providers/scan"),
        getJSON("/health"),
        getJSON("/use-cases"),
      ]).then(function (res) {
        setClis((res[0] && res[0].clis) || []);
        setMedia((res[1] && res[1].media) || []);
        setProviders((res[2] && res[2].providers) || []);
        setHealth(res[3] || null);
        setUseCases((res[4] && res[4].use_cases) || []);
        setRoutes((res[4] && res[4].routes) || []);
        setCustomLocal(!!(res[4] && res[4].show_custom_local));
      }).catch(function (e) {
        setErr(String(e));
      }).finally(function () {
        setLoading(false);
      });
    }, []);

    useEffect(function () { load(); }, [load]);

    var targets = buildTargets(clis, providers, media);
    var selected = (useCases.filter(function (u) { return u.id === active; })[0]) || {};
    var readyClis = clis.filter(function (c) { return c.installed; }).length;
    var readyProviders = providers.filter(function (p) { return p.authed; }).length;
    var readyMedia = media.filter(function (m) { return m.configured; }).length;
    var keySlots = targets.reduce(function (n, t) { return n + (t.key_count || 0); }, 0);
    var hasLocal = targets.some(function (t) { return t.isLocal; });
    function addCustomLocal() {
      postJSON("/custom-local", { enabled: true }).then(function () {
        setCustomLocal(true);
        load();
      });
    }

    return h("div", { className: "flex flex-col gap-5" },
      h(C.Card, { className: "border-emerald-500/20" },
        h(C.CardContent, { className: "flex flex-col gap-4 py-4" },
          h("div", { className: "flex flex-wrap items-center justify-between gap-3" },
            h("div", { className: "min-w-0" },
              h("div", { className: "font-courier text-lg text-emerald-300" }, "CLI Governor"),
              h("div", { className: "text-xs text-muted-foreground" }, "Configure every backend once, then route each category to the best one — with fallback.")),
            h("button", {
              onClick: load,
              className: "border border-border bg-background/40 px-3 py-1 text-xs font-courier hover:bg-foreground/10",
            }, loading ? "Scanning..." : "Re-scan"),
            !customLocal && !hasLocal ? h("button", {
              onClick: addCustomLocal,
              className: "border border-border bg-background/40 px-3 py-1 text-xs font-courier hover:bg-foreground/10",
            }, "Add custom/local") : null),
          h("div", { className: "grid grid-cols-2 gap-4 md:grid-cols-5" },
            metric(readyClis + "/" + clis.length, "CLI targets", "text-emerald-300"),
            metric(readyProviders + "/" + providers.length, "Model providers", "text-sky-300"),
            metric(readyMedia + "/" + media.length, "Media backends", "text-amber-300"),
            metric(String(keySlots), "Credential slots", "text-emerald-300"),
            metric((health && health.active_worker) || "-", "Active worker", "text-muted-foreground")))),

      err ? h(C.Card, { className: "border-rose-500/40" },
        h(C.CardContent, { className: "py-3 text-sm text-rose-300" }, "Backend error: " + err)) : null,

      // 1) Single, unified config for every backend.
      h(BackendsConfig, { targets: targets, onChanged: load }),

      // 2) Category-wise routing.
      h("div", { className: "flex flex-col gap-1" },
        h("div", { className: "text-[11px] uppercase tracking-wider text-muted-foreground" }, "Route by category"),
        h("div", { className: "flex flex-wrap gap-2" },
          USE_CASE_ORDER.map(function (id) {
            return h("button", {
              key: id,
              onClick: function () { setActive(id); },
              className: cn("border px-3 py-2 text-sm font-courier", active === id ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-200" : "border-border bg-background/40 text-muted-foreground hover:bg-foreground/10"),
            }, USE_CASE_NAMES[id]);
          }))),

      h(C.Card, null,
        h(C.CardContent, { className: "py-3" },
          h("div", { className: "font-courier text-sm" }, selected.name || USE_CASE_NAMES[active]),
          h("div", { className: "mt-1 max-w-4xl text-xs text-muted-foreground" }, selected.description || ""),
          h("div", { className: "mt-2 text-[11px] text-muted-foreground" }, selected.intent || ""))),

      h(CategoryMatrix, {
        useCase: active,
        useCaseDef: selected,
        targets: targets,
        routes: routes,
        onRoutesChanged: setRoutes,
        onChanged: load,
      })
    );
  }

  window.__HERMES_PLUGINS__.register("cli-orchestrator", App);
})();
