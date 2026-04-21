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

    .progress-info { font-size: 12px; color: var(--muted); margin-bottom: 4px; display: block; }

    /* Dual Panel additions */
    .reading-workspace { display: flex; flex: 1; overflow: hidden; }
    .left-panel { flex: 2; display: flex; flex-direction: column; overflow: hidden; }
    .right-panel { flex: 1; border-left: 1px solid var(--border); display: flex; flex-direction: column; background: #0b1117; }
    .page-nav { padding: 10px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: #0b1117; }
    .page-nav button { padding: 4px 10px; font-size: 12px; }
    .page-indicator { font-weight: bold; color: var(--accent); }
    .active-chunk { background: rgba(88, 166, 255, 0.15); padding: 2px 4px; border-radius: 4px; border-left: 3px solid var(--accent); }

    /* Scrollbars */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #8b9eb0; }
  </style>
</head>
<body>
  <div id="dropOverlay" class="drop-overlay">Soltá .txt, .md o .pdf acá</div>
  <aside class="sidebar">
    <div class="side-header">
      <h2>Biblioteca</h2>
      <button id="btnUpload" class="upload-btn">Subir</button>
      <input type="file" id="fileUpload" accept=".txt,.md,.pdf,application/pdf" style="display:none;" />
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

    <div class="reading-workspace">
      <!-- Left Panel: Document/Pages -->
      <div class="left-panel">
        <div class="page-nav">
          <button id="btnPrevPage">← Pág Anterior</button>
          <span class="page-indicator" id="pageIndicator">Página - de -</span>
          <button id="btnNextPage">Pág Siguiente →</button>
        </div>
        <div class="viewport" style="padding: 20px;">
          <div class="reader-content" id="textContent">
            <div class="empty-state">Seleccioná un libro en la biblioteca para empezar la lectura real.</div>
          </div>
        </div>

        <div class="console-section" style="border-top: 1px solid var(--border); border-bottom: none; height: auto;">
          <div class="controls-area">
            <span class="progress-info" id="progressInfo">Párrafo: -- / --</span>
            <div class="controls-row">
              <button id="cmdPrev" title="Anterior">← Párrafo Ant</button>
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
            <div class="msg assistant">Bienvenido al lector real. Las páginas se mostrarán arriba.</div>
          </div>
        </div>
      </div>

      <!-- Right Panel: Notes Mirror -->
      <div class="right-panel">
        <div style="padding: 15px; border-bottom: 1px solid var(--border); font-weight: bold; color: var(--accent);">Notas en Espejo</div>
        <div id="notesList" style="flex: 1; overflow-y: auto; padding: 15px;"></div>
        <div style="padding: 15px; border-top: 1px solid var(--border);">
            <textarea id="noteInput" rows="4" style="width: 100%; box-sizing: border-box; background: #161f27; border: 1px solid #30363d; color: #c9d1d9; padding: 8px; border-radius: 4px; font-family: inherit; resize: vertical;" placeholder="Escribir nota manual para esta página..."></textarea>
            <button id="btnSaveNote" class="upload-btn" style="width: 100%; margin-top: 8px;">Guardar Nota</button>
        </div>
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
      dropOverlay: document.getElementById("dropOverlay"),
      btnPrevPage: document.getElementById("btnPrevPage"),
      btnNextPage: document.getElementById("btnNextPage"),
      pageIndicator: document.getElementById("pageIndicator"),
      notesList: document.getElementById("notesList"),
      noteInput: document.getElementById("noteInput"),
      btnSaveNote: document.getElementById("btnSaveNote")
    };

    let state = {
      book_id: null,
      cursor: 0,
      total_chunks: 0,
      reader_state: "idle",
      last_text: "",
      current_page: 1,
      chunks_per_page: 5,
      page_pinned: false
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

    function escapeHtml(text) {
      return String(text || "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[ch]));
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
        const v = await fetch("/api/voice").then(r => r.json());

        const s = await api("/api/reader/session?include_chunks=1");
        if (s.ok && s.exists) {
          const isNewBook = state.book_id !== s.book_id;
          state.book_id = s.book_id;
          state.cursor = s.cursor || 0;
          state.total_chunks = s.total_chunks || 0;
          state.reader_state = s.reader_state || "idle";

          if (isNewBook) {
             state.last_text = "";
             el.textContent.innerHTML = "";
          }

          el.bookTitle.textContent = state.book_id;
          el.badge.textContent = state.reader_state;
          el.badge.className = `session-badge ${state.reader_state}`;
          const displayPara = Math.min(state.cursor + 1, state.total_chunks);
          el.progress.textContent = `Párrafo: ${displayPara} / ${state.total_chunks}`;

          const isIdle = state.reader_state === "idle";
          [el.btnPrev, el.btnNext, el.btnPause, el.btnResume, el.btnJump].forEach(b => b.disabled = isIdle);

          // Pagination Logic
          const totalPages = Math.ceil(state.total_chunks / state.chunks_per_page) || 1;
          const calculatedPage = Math.floor(state.cursor / state.chunks_per_page) + 1;

          // Only follow cursor page if page is not pinned by user navigation
          const effectivePage = (state.page_pinned && !isNewBook) ? state.current_page : calculatedPage;
          const pageChanged = state.current_page !== effectivePage || forceUpdate || isNewBook;
          state.current_page = effectivePage;
          el.pageIndicator.textContent = `Página ${state.current_page} de ${totalPages}`;

          el.btnPrevPage.disabled = state.current_page <= 1;
          el.btnNextPage.disabled = state.current_page >= totalPages;

          // Render Page Text
          if (s.chunks && s.chunks.length > 0) {
            const startIdx = (state.current_page - 1) * state.chunks_per_page;
            const endIdx = Math.min(startIdx + state.chunks_per_page, state.total_chunks);
            let html = "";
            let textRep = "";
            for (let i = startIdx; i < endIdx; i++) {
               textRep += s.chunks[i];
               if (i === state.cursor) {
                  html += `<div class="active-chunk" style="margin-bottom: 15px;">${escapeHtml(s.chunks[i])}</div>`;
               } else {
                  html += `<div style="margin-bottom: 15px;">${escapeHtml(s.chunks[i])}</div>`;
               }
            }
            if (state.last_text !== textRep || forceUpdate || el.textContent.innerText.trim().startsWith("Seleccioná")) {
               state.last_text = textRep;
               el.textContent.innerHTML = `<div class="reader-content">${html}</div>`;
            }
          } else if (isIdle) {
             el.textContent.innerHTML = `<div class="empty-state">Seleccioná un libro en la biblioteca para empezar la lectura real.</div>`;
          }

          // Fetch Notes if Page Changed
          if (pageChanged && state.book_id) {
             const notesRes = await fetch(`/api/notes?doc_id=${encodeURIComponent(state.book_id)}&page=${state.current_page}`).then(r => r.json());
             el.notesList.innerHTML = "";
             if (notesRes.ok && notesRes.notes.length > 0) {
                notesRes.notes.forEach(nn => {
                   const nd = document.createElement("div");
                   nd.style.cssText = "background: #161f27; padding: 10px; margin-bottom: 10px; border-radius: 4px; border-left: 3px solid var(--accent); white-space: pre-wrap;";
                   nd.innerHTML = `<div style="font-size: 10px; color: var(--muted); margin-bottom: 4px;">${nn.role.toUpperCase()}</div>${nn.text}`;
                   el.notesList.appendChild(nd);
                });
             } else {
                el.notesList.innerHTML = `<div class="empty-state" style="margin-top:20px;">No hay notas para esta página.</div>`;
             }
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

    // --- Pagination / Notes Binds ---
    async function refreshNotesOnly() {
        if (!state.book_id) return;
        try {
            const notesRes = await fetch(`/api/notes?doc_id=${encodeURIComponent(state.book_id)}&page=${state.current_page}`).then(r => r.json());
            el.notesList.innerHTML = "";
            if (notesRes.ok && notesRes.notes.length > 0) {
                notesRes.notes.forEach(nn => {
                    const nd = document.createElement("div");
                    nd.style.cssText = "background: #161f27; padding: 10px; margin-bottom: 10px; border-radius: 4px; border-left: 3px solid var(--accent); white-space: pre-wrap;";
                    nd.innerHTML = `<div style="font-size: 10px; color: var(--muted); margin-bottom: 4px;">${nn.role.toUpperCase()}</div>${nn.text}`;
                    el.notesList.appendChild(nd);
                });
            } else {
                el.notesList.innerHTML = `<div class="empty-state" style="margin-top:20px;">No hay notas para esta página.</div>`;
            }
        } catch (e) { console.error("Notes refresh failed", e); }
    }

    const navPage = async (pageTarget) => {
        if (!state.book_id) return;
        state.page_pinned = true;
        state.current_page = pageTarget;
        const targetChunk = (pageTarget - 1) * state.chunks_per_page + 1;
        await api("/api/reader/session/barge_in", "POST", {});
        await nav(`ir al párrafo ${targetChunk}`);
        // Unpin after navigation completes so future cursor syncs work
        setTimeout(() => { state.page_pinned = false; }, 3000);
    };

    el.btnPrevPage.onclick = () => navPage(state.current_page - 1);
    el.btnNextPage.onclick = () => navPage(state.current_page + 1);

    el.btnSaveNote.onclick = async () => {
        const txt = el.noteInput.value.trim();
        if (!txt || !state.book_id) return;
        el.btnSaveNote.disabled = true;
        try {
            const r = await fetch("/api/notes", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ doc_id: state.book_id, page: state.current_page, text: txt })
            }).then(res => res.json());
            if (r.ok) {
                el.noteInput.value = "";
                await refreshNotesOnly(); // Only refresh notes, don't re-sync cursor/page
            }
        } catch (e) {
            console.error(e);
        } finally {
            el.btnSaveNote.disabled = false;
        }
    };

    // --- Upload Logic ---
    async function handleFileRead(file, content, isBase64 = false) {
      pushMessage("system", `Subiendo ${file.name}...`);
      try {
        const payload = { filename: file.name };
        if (isBase64) payload.content_base64 = content;
        else payload.content = content;
        const r = await fetch("/api/documents/upload", {
           method: "POST",
           headers: { "Content-Type": "application/json" },
           body: JSON.stringify(payload)
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
      const name = file.name.toLowerCase();
      const isPdf = name.endsWith(".pdf") || file.type === "application/pdf";
      if (!name.endsWith(".txt") && !name.endsWith(".md") && !isPdf) {
         pushMessage("system", `Formato no soportado (${file.name}). Solo .txt, .md o .pdf.`);
         return;
      }
      const reader = new FileReader();
      reader.onload = (e) => handleFileRead(file, e.target.result, isPdf);
      reader.onerror = () => pushMessage("system", `Error al leer archivo local.`);
      if (isPdf) reader.readAsDataURL(file);
      else reader.readAsText(file);
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
