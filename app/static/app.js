// Small, dependency-free client: start generation, stream progress, share buttons, checklist state.
(function () {
  "use strict";

  function $(sel) { return document.querySelector(sel); }

  function setMsg(el, text, isError) {
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("error", !!isError);
  }

  async function startGeneration(query, force, msgEl) {
    const codeEl = $("#code");
    setMsg(msgEl, "Starting the research agent...");
    let resp, body;
    try {
      resp = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query, force: !!force, access_code: codeEl ? codeEl.value : "" }),
      });
      body = await resp.json().catch(function () { return {}; });
    } catch (e) {
      setMsg(msgEl, "Network error: " + e, true);
      return;
    }
    if (!resp.ok) {
      let detail = body && body.detail;
      if (Array.isArray(detail)) detail = detail.map(function (d) { return d.msg; }).join("; ");
      setMsg(msgEl, detail || ("Request failed (" + resp.status + ")"), true);
      return;
    }
    if (body.redirect) {
      if (body.cached === "generated") {
        setMsg(msgEl, "A guide generated on " + body.generated_at + " already exists. Opening it...");
      } else {
        setMsg(msgEl, "A curated guide exists. Opening it...");
      }
      window.location.href = body.redirect;
      return;
    }
    window.location.href = body.url;
  }

  // ---- home page form ----
  const form = $("#gen-form");
  if (form) {
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      const q = $("#q").value.trim();
      if (q) startGeneration(q, false, $("#gen-msg"));
    });
  }

  // ---- regenerate button on agent guides ----
  const regen = $("#regen");
  if (regen) {
    regen.addEventListener("click", function () {
      startGeneration(regen.dataset.query, true, $("#gen-msg"));
    });
  }

  // ---- job progress page ----
  const jobEl = $("#job");
  if (jobEl) {
    const logEl = $("#log");
    const resultEl = $("#job-result");
    const stageOrder = ["research", "sources", "synthesize", "probe", "composio", "verify", "done"];
    const stageMap = { sources: "research", composio: "probe" };

    function markStage(stage) {
      const target = stageMap[stage] || stage;
      const idx = stageOrder.indexOf(target);
      document.querySelectorAll(".stages li").forEach(function (li) {
        const s = li.dataset.stage;
        const i = stageOrder.indexOf(s);
        li.classList.toggle("done", i < idx || (target === "done" && s === "done"));
        li.classList.toggle("active", s === target && target !== "done");
      });
    }

    function addLine(ev) {
      const line = document.createElement("div");
      line.className = "line" + (ev.ok === false ? " bad" : "") + (ev.outcome ? " outcome-" + ev.outcome : "");
      const t = document.createElement("span");
      t.className = "t";
      t.textContent = (ev.t != null ? ev.t.toFixed(1) : "") + "s";
      const s = document.createElement("span");
      s.className = "stage";
      s.textContent = ev.stage || ev.type;
      const m = document.createElement("span");
      m.textContent = ev.message || "";
      line.appendChild(t); line.appendChild(s); line.appendChild(m);
      if (ev.queries && ev.queries.length) {
        const q = document.createElement("div");
        q.className = "queries";
        q.textContent = "searched: " + ev.queries.join(" | ");
        line.appendChild(q);
      }
      logEl.appendChild(line);
      logEl.scrollTop = logEl.scrollHeight;
    }

    const es = new EventSource("/api/jobs/" + encodeURIComponent(jobEl.dataset.job) + "/events");
    es.onmessage = function (msg) {
      let ev;
      try { ev = JSON.parse(msg.data); } catch (e) { return; }
      if (ev.type === "step") {
        addLine(ev);
        markStage(ev.stage);
      } else if (ev.type === "done") {
        es.close();
        markStage("done");
        resultEl.innerHTML = "";
        const a = document.createElement("a");
        a.href = ev.url;
        a.textContent = "Open the guide";
        resultEl.appendChild(document.createTextNode("Guide ready. "));
        resultEl.appendChild(a);
        setTimeout(function () { window.location.href = ev.url; }, 1500);
      } else if (ev.type === "error") {
        es.close();
        addLine({ stage: "error", message: ev.message, ok: false, t: ev.t });
        setMsg(resultEl, "Generation failed: " + ev.message, true);
      }
    };
    es.onerror = function () {
      // The browser reconnects automatically and resumes from Last-Event-ID.
      if (es.readyState === EventSource.CLOSED) setMsg(resultEl, "Lost connection to the server.", true);
    };
  }

  // ---- share buttons ----
  function flash(btn, text) {
    const old = btn.textContent;
    btn.textContent = text;
    setTimeout(function () { btn.textContent = old; }, 1500);
  }
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); document.body.removeChild(ta);
  }
  const copyLink = $("#copy-link");
  if (copyLink) {
    copyLink.addEventListener("click", function () {
      copyText(copyLink.dataset.url).then(function () { flash(copyLink, "Copied"); },
        function () { flash(copyLink, "Copy failed"); });
    });
  }
  const copyMd = $("#copy-md");
  if (copyMd) {
    copyMd.addEventListener("click", function () {
      fetch(copyMd.dataset.src).then(function (r) { return r.text(); }).then(copyText)
        .then(function () { flash(copyMd, "Copied"); }, function () { flash(copyMd, "Copy failed"); });
    });
  }

  // ---- checklist persistence ----
  const guide = document.querySelector(".guide");
  if (guide) {
    const key = "oauth-guide-steps:" + guide.dataset.slug;
    let state = {};
    try { state = JSON.parse(localStorage.getItem(key) || "{}"); } catch (e) { state = {}; }
    document.querySelectorAll(".checklist input[type=checkbox]").forEach(function (cb) {
      cb.checked = !!state[cb.dataset.step];
      cb.addEventListener("change", function () {
        state[cb.dataset.step] = cb.checked;
        try { localStorage.setItem(key, JSON.stringify(state)); } catch (e) { /* storage full or blocked */ }
      });
    });
  }
})();
