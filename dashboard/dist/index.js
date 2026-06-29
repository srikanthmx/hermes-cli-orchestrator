/**
 * CLI Orchestrator — "CLI Matrix" dashboard tab.
 *
 * Plain IIFE (no build step). Uses the globals the Hermes dashboard exposes:
 *   window.__HERMES_PLUGIN_SDK__  — React, hooks, components, fetchJSON, utils
 *   window.__HERMES_PLUGINS__     — register(name, Component)
 *
 * Backend lives in ../plugin_api.py, mounted at
 *   /api/plugins/cli-orchestrator/
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

  // ── tiny presentational helpers ──────────────────────────────────────────
  function StatusBadge(status, auth) {
    var map = {
      online: ["Online", "text-emerald-400 border-emerald-500/40 bg-emerald-500/10"],
      unauthenticated: ["Not Authenticated", "text-amber-400 border-amber-500/40 bg-amber-500/10"],
      missing: ["Missing", "text-zinc-400 border-zinc-500/40 bg-zinc-500/10"],
    };
    var m = map[status] || map.missing;
    return h("span", {
      className: cn(
        "inline-flex items-center gap-1.5 border px-2 py-0.5 text-[11px] font-courier uppercase tracking-wider",
        m[1]
      ),
    },
      h("span", { className: "inline-block h-1.5 w-1.5 rounded-full bg-current" }),
      m[0]
    );
  }

  function Gauge(pct, label, sub) {
    pct = Math.max(0, Math.min(100, pct || 0));
    var danger = pct >= 90, warn = pct >= 70;
    var color = danger ? "bg-rose-500" : warn ? "bg-amber-400" : "bg-emerald-400";
    return h("div", { className: "flex flex-col gap-1" },
      h("div", { className: "flex items-baseline justify-between" },
        h("span", { className: "text-xs text-muted-foreground uppercase tracking-wider" }, label),
        h("span", { className: "font-courier text-sm" }, pct + "%")
      ),
      h("div", { className: "h-1.5 w-full overflow-hidden rounded-full bg-foreground/10" },
        h("div", { className: cn("h-full rounded-full transition-all", color), style: { width: pct + "%" } })
      ),
      sub ? h("span", { className: "text-[11px] text-muted-foreground" }, sub) : null
    );
  }

  function Metric(value, label, accent) {
    return h("div", { className: "flex flex-col gap-0.5" },
      h("span", { className: cn("font-courier text-2xl leading-none", accent || "") }, value),
      h("span", { className: "text-[11px] uppercase tracking-wider text-muted-foreground" }, label)
    );
  }

  // ── per-CLI card ─────────────────────────────────────────────────────────
  function CliCard(props) {
    var c = props.cli;
    var lim = c.limits || { hourly: 0, daily: 0, monthly: 0 };
    var st = useState({ hourly: lim.hourly || 0, daily: lim.daily || 0, monthly: lim.monthly || 0 });
    var form = st[0], setForm = st[1];
    var savingSt = useState(false); var saving = savingSt[0], setSaving = savingSt[1];
    var installSt = useState(false); var installing = installSt[0], setInstalling = installSt[1];
    var logSt = useState(""); var log = logSt[0], setLog = logSt[1];

    function num(field) {
      return h("div", { className: "flex flex-col gap-1" },
        h("label", { className: "text-[11px] uppercase tracking-wider text-muted-foreground" }, field),
        h("input", {
          type: "number", min: 0, value: form[field],
          onChange: function (e) {
            var v = parseInt(e.target.value, 10);
            var next = {}; next[field] = isNaN(v) ? 0 : v;
            setForm(Object.assign({}, form, next));
          },
          className: "w-full border border-border bg-background/40 px-2 py-1 font-courier text-sm outline-none focus:border-emerald-500/60",
        })
      );
    }

    function saveLimits() {
      setSaving(true);
      postJSON("/limits", { id: c.id, hourly: form.hourly, daily: form.daily, monthly: form.monthly })
        .then(function () { if (props.onChanged) props.onChanged(); })
        .catch(function () {})
        .finally(function () { setSaving(false); });
    }

    function doInstall(manager) {
      setInstalling(true); setLog("starting…");
      postJSON("/install", { id: c.id, manager: manager })
        .then(function () {
          var poll = setInterval(function () {
            getJSON("/install/status?id=" + encodeURIComponent(c.id))
              .then(function (s) {
                setLog(s.log || "");
                if (!s.running) {
                  clearInterval(poll);
                  setInstalling(false);
                  if (props.onChanged) props.onChanged();
                }
              })
              .catch(function () { clearInterval(poll); setInstalling(false); });
          }, 1500);
        })
        .catch(function (e) { setInstalling(false); setLog("install failed: " + e); });
    }

    var dayCap = lim.daily || 0;
    var dayUse = (c.usage && c.usage.day) || 0;
    var dayPct = dayCap > 0 ? Math.round((100 * dayUse) / dayCap) : 0;

    return h(C.Card, { className: "flex flex-col" },
      h(C.CardHeader, { className: "pb-2" },
        h("div", { className: "flex items-start justify-between gap-2" },
          h("div", { className: "flex flex-col gap-1" },
            h(C.CardTitle, { className: "text-base font-courier" }, c.name),
            h("span", { className: "text-[11px] uppercase tracking-wider text-muted-foreground" }, c.category)
          ),
          StatusBadge(c.status, c.auth)
        )
      ),
      h(C.CardContent, { className: "flex flex-col gap-3 text-sm" },
        // identity line
        h("div", { className: "flex flex-col gap-0.5 font-courier text-xs text-muted-foreground" },
          h("span", null, "$ " + c.bin + (c.version ? "  ·  " + c.version : "")),
          c.path ? h("span", { className: "truncate" }, c.path) : h("span", null, "not on PATH")
        ),
        // auth line
        c.auth_supported
          ? h("div", { className: "text-xs" },
              "Auth: ",
              h("span", {
                className: c.auth === "authenticated" ? "text-emerald-400" : "text-amber-400",
              }, c.auth)
            )
          : null,

        // usage vs daily cap
        c.installed
          ? Gauge(dayPct, "Daily usage", dayUse + (dayCap ? " / " + dayCap : " (no cap)") + " calls today" +
              (dayCap && dayPct >= 100 ? "  ⚠ OVER CAP" : ""))
          : null,

        // limit inputs
        c.installed
          ? h("div", { className: "flex flex-col gap-2 border-t border-border pt-3" },
              h("div", { className: "grid grid-cols-3 gap-2" }, num("hourly"), num("daily"), num("monthly")),
              h("button", {
                onClick: saveLimits, disabled: saving,
                className: "self-start border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs font-courier text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50 cursor-pointer",
              }, saving ? "Saving…" : "Save limits")
            )
          : // install actions
            h("div", { className: "flex flex-col gap-2 border-t border-border pt-3" },
              (c.install_managers && c.install_managers.length)
                ? h("div", { className: "flex flex-wrap gap-2" },
                    c.install_managers.map(function (mgr) {
                      return h("button", {
                        key: mgr, onClick: function () { doInstall(mgr); }, disabled: installing,
                        className: "border border-border bg-background/40 px-3 py-1 text-xs font-courier hover:bg-foreground/10 disabled:opacity-50 cursor-pointer",
                      }, installing ? "Installing…" : "Install via " + mgr);
                    })
                  )
                : h("span", { className: "text-xs text-muted-foreground" }, "No installer registered"),
              log ? h("pre", {
                className: "max-h-32 overflow-auto whitespace-pre-wrap border border-border bg-black/40 p-2 font-courier text-[10px] text-emerald-300",
              }, log) : null
            ),

        c.docs ? h("a", {
          href: c.docs, target: "_blank", rel: "noreferrer",
          className: "text-[11px] text-muted-foreground underline hover:text-foreground",
        }, "docs ↗") : null
      )
    );
  }

  // ── orchestration routing manager ────────────────────────────────────────
  var DEFAULT_INTENTS = [
    "Frontend / UI", "Backend / API", "Version Control", "Security Review",
    "Testing", "Docs", "Refactor", "Research",
  ];

  function RoutingManager(props) {
    var rulesSt = useState(props.rules || []);
    var rules = rulesSt[0], setRules = rulesSt[1];
    var savingSt = useState(false); var saving = savingSt[0], setSaving = savingSt[1];
    var savedSt = useState(false); var saved = savedSt[0], setSaved = savedSt[1];

    useEffect(function () { setRules(props.rules || []); }, [props.rules]);

    var cliOptions = (props.clis || []).map(function (c) { return c.id; });

    function update(i, key, val) {
      var next = rules.slice();
      next[i] = Object.assign({}, next[i], (function () { var o = {}; o[key] = val; return o; })());
      setRules(next); setSaved(false);
    }
    function addRow() { setRules(rules.concat([{ intent: "", cli: cliOptions[0] || "" }])); setSaved(false); }
    function removeRow(i) { var n = rules.slice(); n.splice(i, 1); setRules(n); setSaved(false); }
    function save() {
      setSaving(true);
      postJSON("/routing", { rules: rules.filter(function (r) { return r.intent && r.cli; }) })
        .then(function () { setSaved(true); })
        .catch(function () {})
        .finally(function () { setSaving(false); });
    }

    return h(C.Card, null,
      h(C.CardHeader, { className: "pb-2" },
        h("div", { className: "flex items-center justify-between" },
          h(C.CardTitle, { className: "text-base font-courier" }, "Orchestration Matrix"),
          h("span", { className: "text-[11px] text-muted-foreground" }, "intent → local CLI")
        )
      ),
      h(C.CardContent, { className: "flex flex-col gap-2" },
        rules.length === 0
          ? h("p", { className: "text-xs text-muted-foreground" }, "No routing rules yet. Map an agent intent to a worker CLI.")
          : rules.map(function (r, i) {
              return h("div", { key: i, className: "flex items-center gap-2" },
                h("input", {
                  list: "cli-orch-intents", value: r.intent, placeholder: "intent",
                  onChange: function (e) { update(i, "intent", e.target.value); },
                  className: "flex-1 border border-border bg-background/40 px-2 py-1 font-courier text-xs outline-none focus:border-emerald-500/60",
                }),
                h("span", { className: "text-muted-foreground" }, "→"),
                h("select", {
                  value: r.cli,
                  onChange: function (e) { update(i, "cli", e.target.value); },
                  className: "border border-border bg-background/40 px-2 py-1 font-courier text-xs outline-none focus:border-emerald-500/60",
                }, cliOptions.map(function (id) { return h("option", { key: id, value: id }, id); })),
                h("button", {
                  onClick: function () { removeRow(i); },
                  className: "border border-border px-2 py-1 text-xs text-muted-foreground hover:text-rose-400 cursor-pointer",
                }, "✕")
              );
            }),
        h("datalist", { id: "cli-orch-intents" },
          DEFAULT_INTENTS.map(function (it) { return h("option", { key: it, value: it }); })),
        h("div", { className: "flex items-center gap-2 pt-1" },
          h("button", {
            onClick: addRow,
            className: "border border-border bg-background/40 px-3 py-1 text-xs font-courier hover:bg-foreground/10 cursor-pointer",
          }, "+ Add rule"),
          h("button", {
            onClick: save, disabled: saving,
            className: "border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs font-courier text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50 cursor-pointer",
          }, saving ? "Saving…" : "Save matrix"),
          saved ? h("span", { className: "text-xs text-emerald-400" }, "saved ✓") : null
        )
      )
    );
  }

  // ── top-level page ───────────────────────────────────────────────────────
  function CliMatrixPage() {
    var clisSt = useState([]); var clis = clisSt[0], setClis = clisSt[1];
    var healthSt = useState(null); var health = healthSt[0], setHealth = healthSt[1];
    var routingSt = useState([]); var routing = routingSt[0], setRouting = routingSt[1];
    var loadingSt = useState(true); var loading = loadingSt[0], setLoading = loadingSt[1];
    var errSt = useState(null); var err = errSt[0], setErr = errSt[1];

    var load = useCallback(function () {
      setLoading(true); setErr(null);
      return Promise.all([getJSON("/scan"), getJSON("/health"), getJSON("/routing")])
        .then(function (res) {
          setClis((res[0] && res[0].clis) || []);
          setHealth(res[1] || null);
          setRouting((res[2] && res[2].routing) || []);
        })
        .catch(function (e) { setErr(String(e)); })
        .finally(function () { setLoading(false); });
    }, []);

    useEffect(function () { load(); }, [load]);

    // group by category
    var byCat = {};
    clis.forEach(function (c) { (byCat[c.category] = byCat[c.category] || []).push(c); });
    var cats = Object.keys(byCat).sort();

    return h("div", { className: "flex flex-col gap-6" },
      // ── header / metrics bar ──
      h(C.Card, { className: "border-emerald-500/20" },
        h(C.CardContent, { className: "flex flex-col gap-4 py-4" },
          h("div", { className: "flex flex-wrap items-center justify-between gap-4" },
            h("div", { className: "flex items-center gap-3" },
              h("span", { className: "font-courier text-lg text-emerald-400" }, "▚ CLI MATRIX"),
              h(C.Badge, { variant: "outline" }, "v0.1.0"),
              loading ? h("span", { className: "text-xs text-muted-foreground" }, "scanning…") : null
            ),
            h("button", {
              onClick: load,
              className: "border border-border bg-background/40 px-3 py-1 text-xs font-courier hover:bg-foreground/10 cursor-pointer",
            }, "⟳ Re-scan")
          ),
          health ? h("div", { className: "grid grid-cols-2 gap-6 md:grid-cols-5" },
            Metric(health.installed + "/" + health.total, "Installed", "text-emerald-400"),
            Metric(health.install_pct + "%", "Coverage"),
            Metric(health.authenticated + "/" + (health.auth_supported || 0), "Authenticated"),
            Metric(health.usage_today, "Calls today"),
            h("div", { className: "col-span-2 md:col-span-1" },
              Gauge(health.daily_budget_used_pct, "Daily cap margin",
                health.daily_budget_used_pct >= 90 ? "near subscription limit" : "within safe margin"))
          ) : null
        )
      ),

      err ? h(C.Card, { className: "border-rose-500/40" },
        h(C.CardContent, { className: "py-3 text-sm text-rose-400" },
          "Backend error: " + err + " (is the dashboard running with the plugin loaded?)")) : null,

      // ── CLI cards by category ──
      cats.map(function (cat) {
        return h("div", { key: cat, className: "flex flex-col gap-3" },
          h("h3", { className: "font-courier text-sm uppercase tracking-wider text-muted-foreground" }, cat),
          h("div", { className: "grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3" },
            byCat[cat].map(function (c) {
              return h(CliCard, { key: c.id, cli: c, onChanged: load });
            })
          )
        );
      }),

      // ── routing ──
      h(RoutingManager, { rules: routing, clis: clis })
    );
  }

  window.__HERMES_PLUGINS__.register("cli-orchestrator", CliMatrixPage);
})();
