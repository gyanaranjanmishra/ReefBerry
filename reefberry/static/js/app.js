/* ReefBerry front-end helpers. No build step, no dependencies. */
(function () {
  "use strict";

  // Disable a form's buttons and show a message while a slow action (like a network scan) runs.
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    var busy = form.getAttribute("data-busy");
    if (!busy) return;
    form.querySelectorAll("button").forEach(function (b) {
      b.disabled = true;
      if (b.type === "submit") b.textContent = busy;
    });
  });

  // Fill mail server fields from a preset.
  var preset = document.getElementById("smtp_preset");
  if (preset) {
    var presets = JSON.parse(preset.getAttribute("data-presets") || "{}");
    preset.addEventListener("change", function () {
      var p = presets[preset.value] || {};
      if (p.smtp_host) document.getElementById("smtp_host").value = p.smtp_host;
      if (p.smtp_port) document.getElementById("smtp_port").value = p.smtp_port;
      if (p.smtp_security) document.getElementById("smtp_security").value = p.smtp_security;
    });
  }

  function hhmm(epochS) {
    return new Date(epochS * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  function mmss(s) {
    s = Math.max(0, Math.round(s));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function setOutlet(el, o) {
    var pill = el.querySelector("[data-pill]");
    var note = el.querySelector("[data-note]");
    el.classList.remove("state-loading", "state-on", "state-off", "state-offline");
    var text = o.role_label;
    if (!o.online || o.is_on === null || o.is_on === undefined) {
      el.classList.add("state-offline");
      pill.textContent = "Offline";
      text = o.error === "needs TP-Link login" ? "Needs TP-Link login" : "Can't reach strip";
      el.disabled = true;
    } else {
      el.classList.add(o.is_on ? "state-on" : "state-off");
      pill.textContent = o.is_on ? "On" : "Off";
      el.disabled = false;
      if (!o.is_on && o.next_on) text = "Back on at " + hhmm(o.next_on);
      else if (!o.is_on && o.hold_reason) text = o.hold_reason;
    }
    note.textContent = text;
    el.dataset.on = o.is_on ? "1" : "0";
    el.setAttribute("aria-label", o.name + ", " + pill.textContent + ". " + (o.is_on ? "Tap to turn off" : "Tap to turn on"));
  }

  function dashboard(intervalMs, opts) {
    opts = opts || {};
    var list = document.getElementById("outlets");
    var stamp = document.getElementById("updated");
    var modes = document.getElementById("modes");
    var activity = document.getElementById("activity");
    var toast = document.getElementById("toast");
    var dialog = document.getElementById("confirm");
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content;
    var ruleMin = modes.dataset.ruleMin;
    var clock = document.getElementById("clock");
    var feedEnds = null, clockSkew = 0, running = false, toastTimer = null;

    function showToast(msg) {
      toast.textContent = msg;
      toast.hidden = false;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(function () { toast.hidden = true; }, 8000);
    }

    function post(url, body) {
      return fetch(url, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json" },
        body: JSON.stringify(body || {})
      }).then(function (r) {
        if (r.status === 401) { window.location = "/login?next=/"; throw new Error("Signed out."); }
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (!r.ok) throw new Error(data.detail || "Something went wrong (" + r.status + ").");
          return data;
        });
      });
    }

    function confirmAsk(message, okLabel) {
      return new Promise(function (resolve) {
        if (!dialog || !dialog.showModal) { resolve(window.confirm(message)); return; }
        dialog.querySelector("[data-msg]").textContent = message;
        dialog.querySelector("[data-ok]").textContent = okLabel;
        dialog.returnValue = "";
        dialog.addEventListener("close", function onClose() {
          dialog.removeEventListener("close", onClose);
          resolve(dialog.returnValue === "ok");
        });
        dialog.showModal();
      });
    }

    function updateCountdown() {
      var el = modes.querySelector("[data-countdown]");
      if (el && feedEnds) el.textContent = mmss(feedEnds - (Date.now() / 1000 + clockSkew));
    }

    function render(data) {
      document.getElementById("sim-note").hidden = !data.simulated;
      clockSkew = data.now - Date.now() / 1000;
      // The Pi screen runs for weeks: pick up new versions after an update.
      if (opts.version && data.version && data.version !== opts.version) { window.location.reload(); return; }
      if (list && data.ready) {
        data.outlets.forEach(function (o) {
          var el = list.querySelector('[data-id="' + CSS.escape(o.id) + '"]');
          if (el && !el.classList.contains("busy")) setOutlet(el, o);
        });
      }
      var mode = data.mode.name;
      modes.querySelectorAll("[data-show]").forEach(function (box) { box.hidden = box.dataset.show !== mode; });
      feedEnds = mode === "feed" ? data.mode.ends : null;
      updateCountdown();

      activity.textContent = "";
      if (!data.events.length) {
        var none = document.createElement("li");
        none.className = "muted";
        none.textContent = "Nothing yet.";
        activity.appendChild(none);
      }
      data.events.forEach(function (ev) {
        var li = document.createElement("li");
        li.className = "ev-" + ev.kind;
        var t = document.createElement("time");
        t.textContent = new Date(ev.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        li.appendChild(t);
        li.appendChild(document.createTextNode(ev.text));
        activity.appendChild(li);
      });
      stamp.textContent = data.ready
        ? "Updated " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
        : "Reading outlets…";
    }

    function tick() {
      if (running || document.hidden) return;
      running = true;
      fetch("/api/outlets", { headers: { Accept: "application/json" }, credentials: "same-origin" })
        .then(function (r) {
          if (r.status === 401) {
            if (opts.readOnly) throw new Error("The Pi screen view is turned off (sudo reefberry screen on).");
            window.location = "/login?next=/"; throw new Error("Signed out.");
          }
          return r.json();
        })
        .then(render)
        .catch(function (e) {
          stamp.textContent = opts.readOnly && e.message.indexOf("screen") > -1 ? e.message : "Can't reach the Pi right now. Retrying…";
        })
        .finally(function () { running = false; });
    }

    function refreshNow() { running = false; tick(); }

    if (list && !opts.readOnly) {
      list.addEventListener("click", function (ev) {
        var el = ev.target.closest(".outlet");
        if (!el || el.disabled) return;
        var turnOn = el.dataset.on !== "1";
        var ask = Promise.resolve(true);
        if (!turnOn) {
          var msg = "Turn off " + el.dataset.name + "?";
          if (el.dataset.role === "return") {
            msg += " The skimmer and ATO turn off too, and come back " + ruleMin +
              " minutes after the return pump is on again.";
          } else {
            msg += " It stays off until you turn it back on.";
          }
          ask = confirmAsk(msg, "Turn off");
        }
        ask.then(function (ok) {
          if (!ok) return;
          el.classList.add("busy");
          el.disabled = true;
          return post("/api/outlets/" + encodeURIComponent(el.dataset.id) + "/switch", { on: turnOn })
            .catch(function (e) { showToast(e.message); })
            .finally(function () { el.classList.remove("busy"); refreshNow(); });
        });
      });
    }

    if (!opts.readOnly) modes.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-action]");
      if (!btn) return;
      var action = btn.dataset.action;
      var ask = Promise.resolve(true);
      if (action === "water-change-start") {
        ask = confirmAsk("Start a water change? The return pump, wavemakers, skimmer and ATO turn off, " +
          "and off-alerts pause until you press Done.", "Start");
      }
      ask.then(function (ok) {
        if (!ok) return;
        btn.disabled = true;
        return post("/api/mode/" + action)
          .catch(function (e) { showToast(e.message); })
          .finally(function () { btn.disabled = false; refreshNow(); });
      });
    });

    function updateClock() {
      if (clock) clock.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    tick();
    updateClock();
    setInterval(tick, intervalMs);
    setInterval(function () { updateCountdown(); updateClock(); }, 1000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) tick(); });
  }

  window.ReefBerry = { dashboard: dashboard };
})();
