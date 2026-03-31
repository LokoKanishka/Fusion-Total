# Dedicated Reader UI to keep voice/reading flow isolated from chat writing mode.
READER_HTML = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Lector Conversacional | Premium Reader</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0b1117;
      --card: #16202a;
      --sidebar: #0d1620;
      --text: #f0f6fc;
      --muted: #8b9eb0;
      --accent: #00d2ff;
      --accent-glow: rgba(0, 210, 255, 0.3);
      --border: #30363d;
      --success: #3fb950;
      --warning: #dbab09;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      color: var(--text);
      background: var(--bg);
      height: 100vh;
      display: flex;
      overflow: hidden;
    }
    
    /* Layout */
    .sidebar {
      width: 280px;
      background: var(--sidebar);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
    }
    .main {
      flex: 1;
      display: grid;
      grid-template-rows: auto 1fr auto;
      background: radial-gradient(circle at 50% 10%, #1e3348 0%, transparent 80%), var(--bg);
      overflow: hidden;
    }
    
    /* Sidebar Components */
    .side-header { padding: 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;}
    .side-header h2 { margin: 0; font-size: 18px; font-weight: 800; color: var(--accent); text-transform: uppercase; letter-spacing: 1px; }
    .upload-btn { background: var(--accent); color: #000; border: none; padding: 6px 12px; border-radius: 4px; font-weight: 700; cursor: pointer; font-size: 12px; transition: all 0.2s; }
    .upload-btn:hover { box-shadow: 0 0 10px var(--accent-glow); }
    .drop-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(11,17,23,0.9); z-index: 1000; display: none; justify-content: center; align-items: center; border: 4px dashed var(--accent); color: var(--accent); font-size: 24px; font-weight: bold; pointer-events: none; }
    .drop-overlay.active { display: flex; }
    .library-list { flex: 1; overflow-y: auto; padding: 10px; }
    .book-item {
      padding: 12px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: #0d1117;
      margin-bottom: 10px;
      cursor: pointer;
      transition: all 0.2s;
    }
    .book-item:hover { border-color: var(--accent); background: #1c2c3d; }
    .book-item.active { border-color: var(--accent); background: #1c2c3d; box-shadow: 0 0 10px var(--accent-glow); }
    .book-title { font-weight: 700; font-size: 14px; display: block; margin-bottom: 4px; }
    .book-meta { font-size: 11px; color: var(--muted); }

    /* Top Bar */
    .top-bar { padding: 16px 24px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: rgba(13, 17, 23, 0.8); backdrop-filter: blur(10px); }
    .current-book { display: flex; flex-direction: column; }
    .current-book-title { font-weight: 700; font-size: 16px; color: var(--text); }
    .session-badge {
      font-size: 10px;
      font-weight: 800;
      padding: 2px 8px;
      border-radius: 4px;
      background: var(--accent);
      color: #000;
      text-transform: uppercase;
      margin-top: 4px;
      display: inline-block;
      width: fit-content;
    }
    .session-badge.idle { background: var(--muted); }
    .session-badge.reading { background: var(--success); }
    .session-badge.paused { background: var(--warning); }
    .session-badge.commenting { background: #ab7df8; }

    /* Reading Canvas */
    .viewport { overflow-y: auto; padding: 40px; display: flex; justify-content: center; }
    .reader-content {
      width: min(800px, 100%);
      line-height: 1.6;
      font-size: 22px;
      font-weight: 400;
      color: #e6edf3;
      text-align: justify;
      animation: fadeIn 0.4s ease;
    }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    .empty-state { color: var(--muted); text-align: center; margin-top: 100px; font-style: italic; }

    /* Bottom Control / Console */
    .console-section { border-top: 1px solid var(--border); padding: 14px 24px; background: rgba(13, 22, 32, 0.9); display: grid; grid-template-columns: 1fr 340px; gap: 20px; }
    .controls-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .chat-mini {
       height: 140px; 
       overflow-y: auto; 
       background: #010409; 
       border: 1px solid var(--border); 
       border-radius: 8px; 
       padding: 10px; 
       font-size: 12px;
       display: flex;
       flex-direction: column;
       gap: 6px;
    }
    .msg { padding: 6px 10px; border-radius: 6px; border: 1px solid #1c2c3d; background: #0d1117; }
    .msg.user { align-self: flex-end; border-color: #2b566d; background: #112a35; }
    .msg.assistant { align-self: flex-start; }

    /* Inputs */
    .input-row { display: flex; gap: 8px; margin-top: 10px; }
    textarea {
      flex: 1;
      height: 44px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: #0d1117;
      color: var(--text);
      padding: 10px;
      font: inherit;
      resize: none;
    }
    button {
      padding: 8px 16px;
      border-radius: 8px;
      border: 1px solid #316078;
      background: #102734;
      color: var(--text);
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s;
    }
    button:hover:not(:disabled) { background: var(--accent); color: #000; }
    button:disabled { opacity: 0.4; cursor: not-allowed; }
    button.icon { padding: 8px; min-width: 44px; }
    
    .progress-info { font-size: 12px; color: var(--muted); margin-bottom: 4px; display: block; }

    /* Scrollbars */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #8b9eb0; }
  </style>
</head>
<body>
  <div id="dropOverlay" class="drop-overlay">Drop .txt or .md files here</div>
  <aside class="sidebar">
    <div class="side-header">
      <h2>Biblioteca</h2>
      <button id="btnUpload" class="upload-btn">Subir</button>
      <input type="file" id="fileUpload" accept=".txt,.md" style="display:none;" />
    </div>
    <div class="library-list" id="library">
      <div class="empty-state">Cargando libros...</div>
    </div>
  </aside>

  <main class="main">
    <div class="top-bar">
      <div class="current-book">
        <span class="current-book-title" id="activeBookTitle">Ningún documento activo</span>
        <span class="session-badge idle" id="activeSessionBadge">Idle</span>
      </div>
      <div class="row">
         <button id="backChat">Volver al Dashboard</button>
      </div>
    </div>

    <div class="viewport">
      <div class="reader-content" id="textContent">
        <div class="empty-state">Seleccioná un libro en la biblioteca para empezar la lectura real.</div>
      </div>
    </div>

    <div class="console-section">
      <div class="controls-area">
        <span class="progress-info" id="progressInfo">Párrafo: -- / --</span>
        <div class="controls-row">
          <button id="cmdPrev" title="Anterior">← Anterior</button>
          <button id="cmdNext" title="Siguiente">Siguiente →</button>
          <button id="cmdPause" title="Pausar">Pausar</button>
          <button id="cmdResume" title="Continuar">Continuar</button>
          <input id="jumpInput" style="width:50px; border-radius:4px; background:#0d1117; color:#fff; border:1px solid var(--border); padding:6px;" type="number" placeholder="#" />
          <button id="cmdJump">Ir</button>
        </div>
        <div class="input-row">
          <textarea id="input" placeholder="Preguntame sobre el bloque..."></textarea>
          <button id="send">Enviar</button>
        </div>
      </div>
      <div class="chat-mini" id="chat">
        <div class="msg assistant">Bienvenido al lector real. Los bloques que lea se mostrarán arriba.</div>
      </div>
    </div>
  </main>

  <script>
    const STORAGE_SID_KEY = "molbot_reader_session_id";
    const SESSION_SID = localStorage.getItem(STORAGE_SID_KEY) || crypto.randomUUID();
    const TAB_TOKEN = crypto.randomUUID();
    localStorage.setItem(STORAGE_SID_KEY, SESSION_SID);

    const el = {
      library: document.getElementById("library"),
      bookTitle: document.getElementById("activeBookTitle"),
      badge: document.getElementById("activeSessionBadge"),
      textContent: document.getElementById("textContent"),
      progress: document.getElementById("progressInfo"),
      chat: document.getElementById("chat"),
      input: document.getElementById("input"),
      send: document.getElementById("send"),
      jump: document.getElementById("jumpInput"),
      btnPrev: document.getElementById("cmdPrev"),
      btnNext: document.getElementById("cmdNext"),
      btnPause: document.getElementById("cmdPause"),
      btnResume: document.getElementById("cmdResume"),
      btnJump: document.getElementById("cmdJump"),
      btnUpload: document.getElementById("btnUpload"),
      fileUpload: document.getElementById("fileUpload"),
      dropOverlay: document.getElementById("dropOverlay")
    };

    let state = {
      book_id: null,
      cursor: 0,
      total_chunks: 0,
      reader_state: "idle",
      last_text: ""
    };

    // --- API Calls ---
    async function api(path, method = "GET", body = null) {
      const opts = {
        method,
        headers: { "Content-Type": "application/json" }
      };
      if (body) opts.body = JSON.stringify({ session_id: SESSION_SID, ...body });
      const r = await fetch(path + (method === "GET" && path.includes("?") ? `&session_id=${SESSION_SID}` : (method === "GET" ? `?session_id=${SESSION_SID}` : "")), opts);
      return await r.json();
    }

    // --- UI Logic ---
    function pushMessage(role, text) {
      const m = document.createElement("div");
      m.className = `msg ${role == "user" ? "user" : "assistant"}`;
      m.textContent = text;
      el.chat.appendChild(m);
      el.chat.scrollTop = el.chat.scrollHeight;
    }

    async function loadLibrary() {
      try {
        const j = await fetch("/api/reader/books").then(r => r.json());
        el.library.innerHTML = "";
        for (const b of j.books) {
          const div = document.createElement("div");
          div.className = "book-item";
          div.innerHTML = `<span class="book-title">${b.title}</span><span class="book-meta">Ref: ${b.id}</span>`;
          div.onclick = () => startBook(b.id);
          el.library.appendChild(div);
        }
      } catch (e) {
        el.library.innerHTML = `<div class="empty-state">Error cargando biblioteca</div>`;
      }
    }

    async function startBook(bookId) {
      pushMessage("system", `Iniciando lectura de ${bookId}...`);
      await api("/api/reader/session/start", "POST", { book_id: bookId });
      await syncAll();
    }

    async function syncAll(forceUpdate = false) {
      try {
        // Sync Voice State
        const v = await fetch("/api/voice").then(r => r.json());
        
        // Sync Session State with chunks for high-reactivity
        const s = await api("/api/reader/session?include_chunks=1");
        if (s.ok && s.exists) {
          // If book changed, clear last_text to force refresh
          if (state.book_id !== s.book_id) {
             state.last_text = "";
             el.textContent.innerHTML = "";
          }
          
          state.book_id = s.book_id;
          state.cursor = s.cursor;
          state.total_chunks = s.total_chunks;
          state.reader_state = s.reader_state || "idle";
          
          el.bookTitle.textContent = state.book_id;
          el.badge.textContent = state.reader_state;
          el.badge.className = `session-badge ${state.reader_state}`;
          const displayPara = Math.min(state.cursor + 1, state.total_chunks);
          el.progress.textContent = `Párrafo: ${displayPara} / ${state.total_chunks}`;
          
          // Disable controls if idle
          const isIdle = state.reader_state === "idle";
          [el.btnPrev, el.btnNext, el.btnPause, el.btnResume, el.btnJump].forEach(b => b.disabled = isIdle);
          
          // Show current chunk text derived from cursor
          const currentText = (s.chunks && s.chunks[s.cursor]) || (s.last_active_chunk ? s.last_active_chunk.text : "");
          if (currentText) {
            if (state.last_text !== currentText || forceUpdate || el.textContent.innerText.trim().startsWith("Seleccioná")) {
               state.last_text = currentText;
               el.textContent.innerHTML = `<div class="reader-content">${state.last_text}</div>`;
               el.textContent.scrollTop = 0;
            }
          } else if (state.reader_state === "idle") {
             el.textContent.innerHTML = `<div class="empty-state">Seleccioná un libro en la biblioteca para empezar la lectura real.</div>`;
          }
        }
      } catch (e) {
        console.error("Sync failed", e);
      }
    }

    async function poll() {
      try {
        const r = await fetch(`/api/reader/session/next?session_id=${SESSION_SID}&autocommit=1`);
        if (r.status === 200) {
          const j = await r.json();
          if (j.chunk && j.chunk.text && !j.replayed) {
            pushMessage("assistant", `[LECTURA]: ${j.chunk.text.substring(0, 40)}...`);
            await syncAll();
          }
        }
      } catch (e) {}
    }

    async function sendChat() {
      const msg = el.input.value.trim();
      if (!msg) return;
      pushMessage("user", msg);
      el.input.value = "";
      el.send.disabled = true;
      try {
        const r = await api("/api/chat", "POST", { message: msg });
        if (r.reply) {
            pushMessage("assistant", r.reply);
            await syncAll();
        }
      } finally {
        el.send.disabled = false;
      }
    }

    // --- Binds ---
    const nav = async (msg) => {
      await api("/api/chat", "POST", { message: msg });
      setTimeout(() => syncAll(true), 150); // Delay briefly for backend settling
    };

    el.btnPrev.onclick = () => nav("volver un párrafo");
    el.btnNext.onclick = () => nav("continuar");
    el.btnPause.onclick = () => api("/api/reader/session/barge_in", "POST", {}).then(() => syncAll(true));
    el.btnResume.onclick = () => nav("continuar");
    el.btnJump.onclick = () => {
      const n = el.jump.value;
      if (n) nav(`ir al párrafo ${n}`);
    };
    el.send.onclick = sendChat;
    el.input.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } };
    document.getElementById("backChat").onclick = () => window.location.href = "/";

    // --- Upload Logic ---
    async function handleFileRead(file, content) {
      pushMessage("system", `Subiendo ${file.name}...`);
      try {
        const r = await fetch("/api/documents/upload", {
           method: "POST",
           headers: { "Content-Type": "application/json" },
           body: JSON.stringify({ filename: file.name, content: content })
        }).then(res => res.json());
        
        if (r.ok) {
           pushMessage("system", `Archivo subido: ${r.title}.`);
           await loadLibrary();
           startBook(r.book_id);
        } else {
           pushMessage("system", `Error al subir: ${r.message || r.error}`);
        }
      } catch (e) {
        pushMessage("system", `Fallo la conexión al subir: ${e}`);
      }
    }

    function processFile(file) {
      if (!file) return;
      if (!file.name.toLowerCase().endsWith(".txt") && !file.name.toLowerCase().endsWith(".md")) {
         pushMessage("system", `Formato no soportado (${file.name}). Solo .txt o .md.`);
         return;
      }
      const reader = new FileReader();
      reader.onload = (e) => handleFileRead(file, e.target.result);
      reader.onerror = () => pushMessage("system", `Error al leer archivo local.`);
      reader.readAsText(file);
    }

    el.btnUpload.onclick = () => el.fileUpload.click();
    el.fileUpload.onchange = (e) => {
      processFile(e.target.files[0]);
      el.fileUpload.value = "";
    };

    window.addEventListener("dragover", (e) => {
      e.preventDefault();
      el.dropOverlay.classList.add("active");
    });
    window.addEventListener("dragleave", (e) => {
      e.preventDefault();
      if (e.clientX === 0 && e.clientY === 0) el.dropOverlay.classList.remove("active");
    });
    window.addEventListener("drop", (e) => {
      e.preventDefault();
      el.dropOverlay.classList.remove("active");
      if (e.dataTransfer.files.length) processFile(e.dataTransfer.files[0]);
    });

    // --- Init ---
    async function init() {
       // Ensure reader mode is active on start
       await api("/api/voice", "POST", { voice_owner: "reader", reader_mode_active: true, enabled: true });
       await loadLibrary();
       await syncAll();
       setInterval(poll, 2500);
       setInterval(syncAll, 5000);
    }

    init();
  </script>
</body>
</html>
"""
