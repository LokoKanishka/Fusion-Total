N8N_PANEL_HTML = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DC n8n Panel</title>
  <style>
    :root {
      --bg: #06080b;
      --panel: #0e1218;
      --text: #c9f7ff;
      --muted: #8ad3df;
      --accent: #2fd1ff;
      --border: #274454;
      --user: #173949;
      --assistant: #1c2430;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      color: var(--text);
      background: radial-gradient(1000px 650px at 10% -15%, #12465c 0%, transparent 60%), var(--bg);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 16px;
    }
    .app {
      width: min(1080px, 100%);
      height: min(92vh, 940px);
      border: 1px solid var(--border);
      border-radius: 14px;
      background: var(--panel);
      display: grid;
      grid-template-rows: auto auto auto 1fr auto;
      overflow: hidden;
    }
    .top {
      padding: 12px 14px;
      border-bottom: 1px solid var(--border);
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }
    .title { font-weight: 700; }
    .meta { color: var(--muted); font-size: 13px; }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .tools {
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
    }
    .n8n-client {
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      display: grid;
      gap: 8px;
    }
    .wf-list {
      max-height: 140px;
      overflow: auto;
      display: grid;
      gap: 6px;
    }
    .wf-item {
      border: 1px solid #2a4152;
      background: #0c151f;
      border-radius: 8px;
      padding: 8px 10px;
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
    }
    .wf-name {
      font-size: 13px;
      color: #d7f7ff;
    }
    .wf-meta {
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }
    .chat {
      padding: 14px;
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .msg {
      padding: 10px 12px;
      border-radius: 10px;
      max-width: 90%;
      white-space: pre-wrap;
      border: 1px solid transparent;
    }
    .user {
      margin-left: auto;
      background: var(--user);
      border-color: #2e6680;
    }
    .assistant {
      margin-right: auto;
      background: var(--assistant);
      border-color: #304253;
    }
    .composer {
      border-top: 1px solid var(--border);
      padding: 12px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: end;
    }
    textarea, select {
      width: 100%;
      background: #0b1620;
      color: var(--text);
      border: 1px solid #2a4d5f;
      border-radius: 8px;
      padding: 10px;
    }
    textarea {
      min-height: 72px;
      resize: vertical;
    }
    button {
      background: linear-gradient(135deg, #146b88, #0e4f65);
      color: #e8faff;
      border: 1px solid #2bbbe4;
      border-radius: 8px;
      padding: 9px 12px;
      cursor: pointer;
      font-weight: 700;
    }
    button.alt {
      background: #101922;
      border-color: #2b4457;
      color: var(--muted);
    }
    button.alt:hover { border-color: #4f7fa1; color: #cff6ff; }
    .small { font-size: 12px; color: var(--muted); }
  </style>
</head>
<body>
  <div class="app">
    <div class="top">
      <div>
        <div class="title">Direct Chat - Panel n8n</div>
        <div class="meta">Panel aislado para interacciones de n8n. Puerto 5678 (uso exclusivo de Fusion).</div>
      </div>
      <div class="row">
        <select id="model" style="min-width:320px"></select>
        <button class="alt" id="openN8nUi">Abrir n8n UI (5678)</button>
        <button class="alt" id="newSession">Nueva sesion</button>
      </div>
    </div>

    <div class="tools">
      <span class="small" id="sessionInfo"></span>
      <span class="small">Aislado del chat principal y del modo lectura.</span>
    </div>

    <div class="n8n-client">
      <div class="row">
        <span class="small" id="n8nStatus">n8n API: verificando...</span>
        <button class="alt" id="refreshWorkflows">Actualizar workflows</button>
      </div>
      <div class="wf-list" id="workflowList"></div>
    </div>

    <div id="chat" class="chat"></div>

    <div class="composer">
      <textarea id="input" placeholder="Escribi una instruccion para n8n..."></textarea>
      <button id="send">Enviar</button>
    </div>
  </div>

  <script>
    const chatEl = document.getElementById("chat");
    const inputEl = document.getElementById("input");
    const modelEl = document.getElementById("model");
    const sendEl = document.getElementById("send");
    const newSessionEl = document.getElementById("newSession");
    const openN8nUiEl = document.getElementById("openN8nUi");
    const refreshWorkflowsEl = document.getElementById("refreshWorkflows");
    const sessionInfoEl = document.getElementById("sessionInfo");
    const n8nStatusEl = document.getElementById("n8nStatus");
    const workflowListEl = document.getElementById("workflowList");

    const N8N_SESSION_KEY = "molbot_n8n_panel_session_id";
    const N8N_MODEL_KEY = "molbot_n8n_panel_model_id";
    let sessionId = localStorage.getItem(N8N_SESSION_KEY) || ("n8n_" + crypto.randomUUID());
    localStorage.setItem(N8N_SESSION_KEY, sessionId);
    let history = [];

    function el(tag, cls, text) {
      const node = document.createElement(tag);
      if (cls) node.className = cls;
      if (text != null) node.textContent = text;
      return node;
    }

    function renderWorkflows(items) {
      workflowListEl.innerHTML = "";
      const arr = Array.isArray(items) ? items : [];
      if (!arr.length) {
        workflowListEl.appendChild(el("div", "small", "No hay workflows visibles."));
        return;
      }
      for (const wf of arr) {
        const row = el("div", "wf-item");
        const name = el("span", "wf-name", String(wf && wf.name || "workflow"));
        const active = !!(wf && wf.active);
        const wid = String(wf && wf.id || "").trim();
        const meta = el("span", "wf-meta", `${active ? "activo" : "inactivo"} · id:${wid || "-"}`);
        row.appendChild(name);
        row.appendChild(meta);
        workflowListEl.appendChild(row);
      }
    }

    async function refreshN8nStatus() {
      try {
        const r = await fetch("/api/n8n/status");
        const j = await r.json();
        if (!r.ok) {
          n8nStatusEl.textContent = `n8n API: error ${r.status}`;
          return;
        }
        if (!j || !j.token_configured) {
          n8nStatusEl.textContent = "n8n API: falta token (DIRECT_CHAT_N8N_API_TOKEN)";
          return;
        }
        const health = Number(j.health_http || 0);
        n8nStatusEl.textContent = `n8n API: ${j.api_auth_ok ? "auth OK" : "auth FAIL"} · health ${health || "-"}`;
      } catch {
        n8nStatusEl.textContent = "n8n API: sin conexión";
      }
    }

    async function refreshWorkflows() {
      refreshWorkflowsEl.disabled = true;
      try {
        const r = await fetch("/api/n8n/workflows?limit=40");
        const j = await r.json();
        if (!r.ok) {
          n8nStatusEl.textContent = `n8n API workflows error ${r.status}`;
          renderWorkflows([]);
          return;
        }
        const arr = Array.isArray(j && j.workflows) ? j.workflows : [];
        renderWorkflows(arr);
      } catch {
        renderWorkflows([]);
      } finally {
        refreshWorkflowsEl.disabled = false;
      }
    }

    function draw() {
      chatEl.innerHTML = "";
      for (const m of history) {
        const box = el("div", `msg ${m.role === "user" ? "user" : "assistant"}`, m.content);
        chatEl.appendChild(box);
      }
      chatEl.scrollTop = chatEl.scrollHeight;
    }

    function selectedModel() {
      const raw = String(modelEl.value || "").trim();
      const idx = raw.lastIndexOf("::");
      if (idx > 0) return { model: raw.slice(0, idx), model_backend: raw.slice(idx + 2) };
      return { model: raw || "openai-codex/gpt-5.1-codex-mini", model_backend: "cloud" };
    }

    function modelExists(rawValue) {
      const val = String(rawValue || "").trim();
      if (!val) return false;
      return Array.from(modelEl.options).some((o) => String(o.value || "") === val);
    }

    async function refreshModels(preferReset = false) {
      let payload = { default_model: "openai-codex/gpt-5.1-codex-mini", models: [] };
      try {
        const r = await fetch(`/api/models?session_id=${encodeURIComponent(sessionId)}`);
        if (r.ok) payload = await r.json();
      } catch {}

      const models = Array.isArray(payload.models) ? payload.models : [];
      const opts = [];
      for (const m of models) {
        const id = String(m && m.id || "").trim();
        const backend = String(m && m.backend || "").trim();
        const available = !!(m && m.available);
        if (!id || !backend) continue;
        opts.push({
          value: `${id}::${backend}`,
          text: `${id}${backend === "local" && !available ? " (no instalado)" : ""}`,
          available,
          backend,
        });
      }

      if (!opts.length) {
        opts.push({ value: "openai-codex/gpt-5.1-codex-mini::cloud", text: "openai-codex/gpt-5.1-codex-mini", available: true, backend: "cloud" });
      }

      modelEl.innerHTML = "";
      for (const o of opts) {
        const op = document.createElement("option");
        op.value = o.value;
        op.textContent = o.text;
        op.dataset.backend = o.backend;
        if (!o.available && o.backend === "local") op.disabled = true;
        modelEl.appendChild(op);
      }

      const persisted = (localStorage.getItem(N8N_MODEL_KEY) || "").trim();
      if (!preferReset && modelExists(persisted)) {
        modelEl.value = persisted;
      } else {
        const cloudDefault = `${String(payload.default_model || "openai-codex/gpt-5.1-codex-mini")}::cloud`;
        if (modelExists(cloudDefault)) modelEl.value = cloudDefault;
        else modelEl.selectedIndex = 0;
      }
      localStorage.setItem(N8N_MODEL_KEY, modelEl.value || "");
    }

    async function loadServerHistory() {
      try {
        const sel = selectedModel();
        const q = new URLSearchParams({
          session: sessionId,
          model: sel.model || "",
          model_backend: sel.model_backend || "",
        });
        const r = await fetch(`/api/history?${q.toString()}`);
        if (!r.ok) return;
        const j = await r.json();
        const h = Array.isArray(j.history) ? j.history : [];
        history = h.filter(x => x && (x.role === "user" || x.role === "assistant") && typeof x.content === "string");
        draw();
      } catch {}
    }

    async function sendMessage(forceText = null) {
      const msg = (forceText != null ? String(forceText) : inputEl.value).trim();
      if (!msg) return;
      const sel = selectedModel();
      const payload = {
        message: msg,
        source: "ui_n8n_panel",
        model: sel.model || "openai-codex/gpt-5.1-codex-mini",
        model_backend: sel.model_backend || "cloud",
        history,
        mode: "operativo",
        session_id: sessionId,
        allowed_tools: [],
        attachments: [],
      };
      history.push({ role: "user", content: msg });
      draw();
      inputEl.value = "";
      sendEl.disabled = true;
      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const j = await res.json();
        const reply = String(j && j.reply || j && j.error || "Sin respuesta");
        history.push({ role: "assistant", content: reply });
        draw();
      } catch (e) {
        history.push({ role: "assistant", content: `Error: ${e}` });
        draw();
      } finally {
        sendEl.disabled = false;
        inputEl.focus();
      }
    }

    sendEl.addEventListener("click", () => sendMessage());
    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
    modelEl.addEventListener("change", async () => {
      localStorage.setItem(N8N_MODEL_KEY, modelEl.value || "");
      await loadServerHistory();
      inputEl.focus();
    });

    newSessionEl.addEventListener("click", async () => {
      sessionId = "n8n_" + crypto.randomUUID();
      localStorage.setItem(N8N_SESSION_KEY, sessionId);
      history = [];
      draw();
      sessionInfoEl.textContent = `session_id: ${sessionId}`;
      await loadServerHistory();
      inputEl.focus();
    });

    openN8nUiEl.addEventListener("click", () => {
      window.open("http://127.0.0.1:5678", "_blank", "noopener,noreferrer");
    });
    refreshWorkflowsEl.addEventListener("click", async () => {
      await refreshN8nStatus();
      await refreshWorkflows();
    });

    sessionInfoEl.textContent = `session_id: ${sessionId}`;
    refreshModels()
      .then(() => loadServerHistory())
      .then(() => refreshN8nStatus())
      .then(() => refreshWorkflows())
      .then(() => inputEl.focus());
  </script>
</body>
</html>
"""
