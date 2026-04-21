#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import base64
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fusion_reader_v2 import AudioCache, FusionReaderV2, VoiceMetricsStore, import_document_bytes, import_document_path


PORT = int(os.environ.get("FUSION_READER_V2_PORT", "8010"))
ROOT = Path(__file__).resolve().parent.parent
LIBRARY_ROOT = ROOT / "library"
CONVERTED_ROOT = ROOT / "runtime" / "fusion_reader_v2" / "imported_texts"
UPLOAD_ROOT = ROOT / "runtime" / "fusion_reader_v2" / "upload_jobs"
ALLOWED_LIBRARY_SUFFIXES = {".txt", ".md"}
IMPORT_JOBS: dict[str, dict] = {}
IMPORT_JOBS_LOCK = threading.Lock()
APP = FusionReaderV2(
    cache=AudioCache(ROOT / "runtime" / "fusion_reader_v2" / "audio_cache"),
    metrics=VoiceMetricsStore(ROOT / "runtime" / "fusion_reader_v2" / "voice_metrics.jsonl"),
)

INDEX_HTML = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Fusion Reader v2</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #090b0b;
      --panel: #111514;
      --panel-2: #171c1a;
      --line: #31403a;
      --text: #f1f5ef;
      --muted: #98a59f;
      --accent: #21d07a;
      --accent-2: #38c6d8;
      --danger: #ff7474;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    button, input, textarea {
      font: inherit;
      letter-spacing: 0;
    }
    .app {
      height: 100vh;
      display: grid;
      grid-template-columns: minmax(220px, 270px) minmax(0, 1fr) minmax(220px, 270px);
      grid-template-rows: minmax(0, 52vh) minmax(320px, 48vh);
    }
    aside, main, .lab {
      min-width: 0;
      border-color: var(--line);
    }
    aside {
      grid-row: 1 / span 2;
      background: var(--panel);
      padding: 16px;
      overflow: auto;
    }
    .left-sidebar {
      grid-column: 1;
      border-right: 1px solid var(--line);
    }
    .right-sidebar {
      grid-column: 3;
      border-left: 1px solid var(--line);
    }
    main {
      grid-column: 2;
      grid-row: 1;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-height: 0;
      height: 100%;
      border-bottom: 1px solid var(--line);
    }
    .lab {
      grid-column: 2;
      grid-row: 2;
      background: #0c0f0e;
      padding: 18px;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto auto;
      gap: 10px;
      min-height: 0;
    }
    h1, h2 {
      margin: 0;
      line-height: 1.1;
    }
    h1 {
      font-size: 20px;
      color: var(--accent);
    }
    h2 {
      font-size: 15px;
      color: var(--accent-2);
    }
    .sub {
      color: var(--muted);
      font-size: 13px;
      margin: 5px 0 12px;
      overflow-wrap: anywhere;
    }
    .upload-zone {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--text);
      background: #0b0e0d;
      padding: 12px;
      cursor: pointer;
      min-height: 150px;
      display: grid;
      align-content: center;
      justify-items: center;
      gap: 8px;
      text-align: center;
      outline: none;
    }
    .upload-zone:hover,
    .upload-zone.dragover,
    .upload-zone:focus-visible {
      border-color: var(--accent);
      background: #101814;
    }
    .upload-icon {
      width: 38px;
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 8px;
      display: block;
      padding-top: 5px;
      color: var(--accent);
      font-size: 20px;
      line-height: 1;
    }
    .upload-zone strong {
      display: block;
      overflow-wrap: anywhere;
      font-size: 14px;
    }
    .upload-zone button {
      min-height: 36px;
      padding: 7px 10px;
    }
    .upload-zone span,
    .upload-info {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
      overflow-wrap: anywhere;
    }
    .progress-wrap {
      width: 100%;
      height: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #070909;
      overflow: hidden;
      margin-top: 10px;
    }
    .progress-bar {
      width: 0%;
      height: 100%;
      background: var(--accent);
      transition: width .25s ease;
    }
    .notes-panel {
      margin-top: 18px;
      border-top: 1px solid var(--line);
      padding-top: 14px;
    }
    .notes-panel summary {
      cursor: pointer;
      color: var(--accent-2);
      font-weight: 700;
      min-height: 34px;
      line-height: 34px;
      overflow-wrap: anywhere;
    }
    .note-input {
      min-height: 74px;
      margin-top: 8px;
    }
    .note-list {
      display: grid;
      gap: 6px;
      margin-top: 10px;
    }
    .note-row {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #080a0a;
      padding: 0;
    }
    .note-row.current {
      border-color: var(--accent);
    }
    .note-row summary {
      align-items: center;
      color: var(--text);
      display: flex;
      gap: 6px;
      justify-content: space-between;
      min-height: 22px;
      line-height: 1.2;
      font-weight: 600;
      padding: 5px 7px;
    }
    .note-label {
      flex: 1;
      font-size: 13px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .note-rename {
      align-items: center;
      border-radius: 6px;
      display: inline-flex;
      flex: 0 0 auto;
      font-size: 12px;
      justify-content: center;
      min-height: 22px;
      padding: 2px 6px;
    }
    .note-text,
    .note-quote {
      margin: 7px 7px 0;
      font-size: 13px;
      line-height: 1.4;
      overflow-wrap: anywhere;
      white-space: pre-wrap;
    }
    .note-quote {
      color: var(--muted);
      max-height: 78px;
      overflow: auto;
    }
    .note-actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin: 8px 7px 7px;
    }
    .note-actions button {
      min-height: 32px;
      padding: 6px 9px;
      font-size: 13px;
    }
    .file-input {
      position: absolute;
      width: 1px;
      height: 1px;
      opacity: 0;
      pointer-events: none;
    }
    .topbar {
      min-height: 60px;
      border-bottom: 1px solid var(--line);
      padding: 10px 22px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      background: var(--panel-2);
    }
    .title {
      min-width: 0;
    }
    .title strong {
      display: block;
      font-size: 17px;
      overflow-wrap: anywhere;
    }
    .title span {
      display: block;
      color: var(--muted);
      font-size: 13px;
      margin-top: 4px;
    }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 7px 10px;
      color: var(--muted);
      white-space: nowrap;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--danger);
      flex: 0 0 auto;
    }
    .dot.ok { background: var(--accent); }
    .reader {
      overflow: auto;
      padding: 26px clamp(18px, 4vw, 56px) 18px;
      display: flex;
      align-items: center;
    }
    .chunk {
      width: 100%;
      max-width: 980px;
      margin: 0 auto;
      font-size: 22px;
      line-height: 1.45;
      color: var(--text);
      overflow-wrap: anywhere;
      transform: translateY(-8%);
    }
    .chunk.empty {
      color: var(--muted);
      font-style: italic;
      text-align: center;
    }
    .controls {
      border-top: 1px solid var(--line);
      padding: 10px 22px;
      display: grid;
      gap: 8px;
      background: var(--panel);
    }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }
    .toggle {
      min-height: 40px;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      color: var(--muted);
      user-select: none;
    }
    .upload-toggle {
      min-height: 34px;
      margin-top: 8px;
      padding: 6px 8px;
      font-size: 13px;
    }
    .toggle input {
      width: 17px;
      height: 17px;
      accent-color: var(--accent);
    }
    button {
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #101513;
      color: var(--text);
      padding: 9px 13px;
      cursor: pointer;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #06100b;
      font-weight: 700;
    }
    button.wide {
      width: 100%;
    }
    button:disabled {
      opacity: .55;
      cursor: not-allowed;
    }
    input[type="number"] {
      width: 84px;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #080a0a;
      color: var(--text);
      padding: 8px;
    }
    audio {
      width: 100%;
      min-height: 34px;
    }
    textarea {
      width: 100%;
      min-height: 116px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #080a0a;
      color: var(--text);
      padding: 10px;
      line-height: 1.4;
    }
    .chatbox {
      display: grid;
      gap: 10px;
      min-height: 0;
    }
    .chat-log {
      min-height: 0;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #080a0a;
      padding: 10px;
      display: grid;
      align-content: start;
      gap: 10px;
      font-size: 14px;
      line-height: 1.45;
    }
    .chat-msg {
      overflow-wrap: anywhere;
      white-space: pre-wrap;
    }
    .chat-msg.user {
      color: var(--accent);
    }
    .chat-msg.assistant {
      color: var(--text);
    }
    .chat-msg.system {
      color: var(--muted);
      font-style: italic;
    }
    .chat-input {
      min-height: 82px;
    }
    .chat-actions {
      display: grid;
      grid-template-columns: minmax(120px, 1fr) minmax(120px, 1fr);
      gap: 10px;
    }
    .dialogue-row {
      display: grid;
      grid-template-columns: minmax(140px, 220px) minmax(0, 1fr);
      gap: 10px;
      align-items: center;
    }
    .dialogue-info {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .log {
      min-height: 18px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
      overflow-wrap: anywhere;
    }
    @media (max-width: 980px) {
      .app {
        grid-template-columns: 1fr;
        grid-template-rows: auto;
        height: auto;
        min-height: 100vh;
      }
      aside {
        grid-row: auto;
      }
      .right-sidebar {
        display: none;
      }
      aside, .lab {
        border: 0;
        border-bottom: 1px solid var(--line);
      }
      main {
        grid-column: auto;
        grid-row: auto;
        min-height: 55vh;
      }
      .lab {
        grid-column: auto;
        grid-row: auto;
        min-height: 45vh;
      }
      .chunk {
        font-size: 20px;
      }
      .topbar {
        align-items: flex-start;
        flex-direction: column;
      }
      .status {
        white-space: normal;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="left-sidebar">
      <h1>Cargar Texto</h1>
      <p class="sub">Arrastrá un archivo o buscalo en la PC.</p>
      <div id="dropzone" class="upload-zone" tabindex="0" role="button" aria-label="Cargar archivo de texto">
        <div class="upload-icon">+</div>
        <strong>Soltá tu documento acá</strong>
        <span>TXT, MD, PDF, DOCX, ODT, RTF y más.</span>
        <button id="chooseFileBtn" class="primary wide" type="button">Buscar Archivo</button>
        <input id="fileInput" class="file-input" type="file" accept=".txt,.md,.markdown,.pdf,.doc,.docx,.odt,.ott,.rtf,.html,.htm,.csv,.log,text/plain,text/markdown,application/pdf,application/vnd.oasis.opendocument.text,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword">
      </div>
      <label class="toggle upload-toggle"><input id="autoReadToggle" type="checkbox" checked> Leer al cargar</label>
      <p id="uploadInfo" class="upload-info">Todavía no cargaste ningún texto.</p>
      <div class="progress-wrap" aria-hidden="true"><div id="importProgress" class="progress-bar"></div></div>
      <button id="prepareBtn" class="wide" type="button">Preparar documento</button>
      <button id="cancelPrepareBtn" class="wide" type="button">Cancelar preparación</button>
      <p id="prepareInfo" class="upload-info">Audio sin preparar.</p>
      <div class="progress-wrap" aria-hidden="true"><div id="prepareProgress" class="progress-bar"></div></div>
      <details class="notes-panel" open>
        <summary id="notesSummary">Notas del documento</summary>
        <textarea id="noteInput" class="note-input" placeholder="Escribí una nota para el bloque actual..."></textarea>
        <button id="saveNoteBtn" class="primary wide" type="button">Guardar nota</button>
        <p id="notesInfo" class="upload-info">Sin notas todavía.</p>
        <div id="notesList" class="note-list"></div>
      </details>
    </aside>

    <main>
      <header class="topbar">
        <div class="title">
          <strong id="docTitle">Ningún documento activo</strong>
          <span id="docMeta">Bloque 0 de 0</span>
        </div>
        <div class="status"><span id="ttsDot" class="dot"></span><span id="ttsStatus">TTS sin comprobar</span></div>
      </header>

      <section class="reader">
        <div id="chunk" class="chunk empty">Subí un TXT o MD para empezar.</div>
      </section>

      <section class="controls">
        <div class="row">
          <button id="prevBtn">Anterior</button>
          <button id="readBtn" class="primary">Leer</button>
          <button id="repeatBtn">Repetir</button>
          <button id="nextBtn">Siguiente</button>
          <input id="jumpInput" type="number" min="1" value="1" aria-label="Bloque">
          <button id="jumpBtn">Ir</button>
          <label class="toggle"><input id="continuousToggle" type="checkbox"> Continuo</label>
        </div>
        <audio id="player" controls></audio>
        <div id="log" class="log">Lista para cargar.</div>
      </section>
    </main>

    <section class="lab">
      <h2>Laboratorio</h2>
      <div id="chatLog" class="chat-log" aria-live="polite">
        <div class="chat-msg system">Cargá un documento y preguntame por lo que está en pantalla.</div>
      </div>
      <textarea id="chatInput" class="chat-input" placeholder="Escribí sobre el texto actual..."></textarea>
      <div class="chat-actions">
        <button id="sendChatBtn" class="primary">Enviar</button>
        <button id="clearLabHistoryBtn" type="button">Borrar historial</button>
      </div>
      <div class="dialogue-row">
        <button id="dialogueBtn" class="primary">Dialogar</button>
        <div id="dialogueInfo" class="dialogue-info">Diálogo apagado.</div>
      </div>
      <audio id="dialoguePlayer"></audio>
    </section>
    <aside class="right-sidebar" aria-label="Panel lateral derecho"></aside>
  </div>

  <script>
    const els = {
      dropzone: document.getElementById('dropzone'),
      chooseFileBtn: document.getElementById('chooseFileBtn'),
      fileInput: document.getElementById('fileInput'),
      uploadInfo: document.getElementById('uploadInfo'),
      importProgress: document.getElementById('importProgress'),
      autoReadToggle: document.getElementById('autoReadToggle'),
      prepareBtn: document.getElementById('prepareBtn'),
      cancelPrepareBtn: document.getElementById('cancelPrepareBtn'),
      prepareInfo: document.getElementById('prepareInfo'),
      prepareProgress: document.getElementById('prepareProgress'),
      notesSummary: document.getElementById('notesSummary'),
      noteInput: document.getElementById('noteInput'),
      saveNoteBtn: document.getElementById('saveNoteBtn'),
      notesInfo: document.getElementById('notesInfo'),
      notesList: document.getElementById('notesList'),
      docTitle: document.getElementById('docTitle'),
      docMeta: document.getElementById('docMeta'),
      chunk: document.getElementById('chunk'),
      ttsDot: document.getElementById('ttsDot'),
      ttsStatus: document.getElementById('ttsStatus'),
      log: document.getElementById('log'),
      player: document.getElementById('player'),
      prevBtn: document.getElementById('prevBtn'),
      readBtn: document.getElementById('readBtn'),
      repeatBtn: document.getElementById('repeatBtn'),
      nextBtn: document.getElementById('nextBtn'),
      jumpInput: document.getElementById('jumpInput'),
      jumpBtn: document.getElementById('jumpBtn'),
      continuousToggle: document.getElementById('continuousToggle'),
      chatLog: document.getElementById('chatLog'),
      chatInput: document.getElementById('chatInput'),
      sendChatBtn: document.getElementById('sendChatBtn'),
      clearLabHistoryBtn: document.getElementById('clearLabHistoryBtn'),
      dialogueBtn: document.getElementById('dialogueBtn'),
      dialogueInfo: document.getElementById('dialogueInfo'),
      dialoguePlayer: document.getElementById('dialoguePlayer')
    };
    const LAB_NOTES_DOC_ID = '__laboratory__';
    let status = null;
    let notesState = { docId: '', current: 0, items: [] };
    const dialogue = {
      active: false,
      stream: null,
      audioContext: null,
      analyser: null,
      monitorId: 0,
      recorder: null,
      pcmChunks: [],
      pcmPreRoll: [],
      pcmPreRollSamples: 0,
      recording: false,
      finalizing: false,
      processing: false,
      speaking: false,
      chunkIndex: null,
      turnId: 0,
      trace: null,
      suppressUntil: 0,
      localSpeechStartedAt: 0,
      bargeInMs: 240,
      bargeInSpeechMs: 0,
      localSelfMuteMs: 700,
      speechMs: 0,
      silenceMs: 0,
      startedAt: 0,
      lastTick: 0,
      noiseFloor: 0.012,
      minThreshold: 0.018,
      thresholdMultiplier: 2.15,
      speechStartMs: 35,
      silenceStopMs: 1250,
      minRecordMs: 650,
      maxRecordMs: 18000,
      preRollMs: 900,
      finalFlushMs: 180,
      turnStartedAt: 0,
      finalizeTimeoutId: 0,
      captureStopAt: 0,
      sampleRate: 48000
    };

    async function api(path, body) {
      const options = body === undefined ? {} : {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      };
      const res = await fetch(path, options);
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || 'request_failed');
      }
      return data;
    }

    function setBusy(isBusy) {
      [els.prevBtn, els.readBtn, els.repeatBtn, els.nextBtn, els.jumpBtn, els.sendChatBtn, els.saveNoteBtn].forEach(btn => {
        btn.disabled = isBusy;
      });
    }

    function log(text) {
      els.log.textContent = text;
    }

    function wait(ms) {
      return new Promise(resolve => setTimeout(resolve, ms));
    }

    function dialogueFlushWaitMs() {
      return dialogue.finalFlushMs;
    }

    function visibleChunkIndex() {
      const current = status && Number(status.current || 0);
      return current > 0 ? current - 1 : null;
    }

    function fmtMs(ms) {
      const value = Math.max(0, Number(ms || 0));
      if (value >= 1000) {
        return `${(value / 1000).toFixed(2)}s`;
      }
      return `${Math.round(value)}ms`;
    }

    function formatDialogueTrace(data, trace, responseWallMs) {
      const server = data && data.trace && typeof data.trace === 'object' ? data.trace : {};
      const sttTimings = server.stt_timings && typeof server.stt_timings === 'object' ? server.stt_timings : {};
      const recordedMs = trace && Number(trace.recordedMs || 0) > 0
        ? Number(trace.recordedMs || 0)
        : (trace && trace.speechStopAt && trace.speechStartAt ? trace.speechStopAt - trace.speechStartAt : 0);
      const fromStopMs = trace && trace.speechStopAt && trace.responseAt ? trace.responseAt - trace.speechStopAt : responseWallMs;
      const uploadAndServerMs = trace && trace.sendStartedAt && trace.responseAt ? trace.responseAt - trace.sendStartedAt : responseWallMs;
      const parts = [
        `Traza turno ${trace && trace.turnId || '?'}`,
        `audio ${fmtMs(recordedMs)}`,
        `silencio corte ${fmtMs(dialogue.silenceStopMs)}`,
        `flush ${fmtMs(trace && trace.flushWaitMs || 0)}`,
        `subida+servidor ${fmtMs(uploadAndServerMs)}`,
        `STT ${fmtMs(data && data.stt_ms)}`
      ];
      if (sttTimings.convert_ms !== undefined || sttTimings.decode_ms !== undefined) {
        parts.push(`ffmpeg ${fmtMs(sttTimings.convert_ms || 0)}`);
        parts.push(`whisper ${fmtMs(sttTimings.decode_ms || 0)}`);
      }
      parts.push(`intención ${fmtMs(server.intent_ms || 0)}`);
      parts.push(`nota ${fmtMs(server.note_ms || 0)}`);
      parts.push(`chat ${fmtMs(data && data.chat_ms)}`);
      parts.push(`voz ${fmtMs(data && data.tts_ms)}`);
      parts.push(`desde fin de habla ${fmtMs(fromStopMs)}`);
      parts.push(`total servidor ${fmtMs(server.server_total_ms || data && data.duration_ms || 0)}`);
      return parts.join(' | ');
    }

    function selectLocalFemaleSpanishVoice() {
      if (!('speechSynthesis' in window) || typeof window.speechSynthesis.getVoices !== 'function') {
        return null;
      }
      const voices = window.speechSynthesis.getVoices() || [];
      if (!voices.length) {
        return null;
      }
      const spanish = voices.filter(voice => String(voice.lang || '').toLowerCase().startsWith('es'));
      const pool = spanish.length ? spanish : voices;
      const femaleHints = /(female|mujer|femenina|m[oó]nica|monica|paulina|helena|elena|sabina|soledad|laura|lucia|luc[ií]a|maria|mar[ií]a|carmen|isabel|paloma|google espa[ñn]ol)/i;
      const maleHints = /(male|hombre|masculina|pablo|jorge|juan|carlos|diego|miguel|antonio|enrique|ricardo)/i;
      return (
        pool.find(voice => femaleHints.test(`${voice.name} ${voice.voiceURI}`) && !maleHints.test(`${voice.name} ${voice.voiceURI}`)) ||
        spanish.find(voice => !maleHints.test(`${voice.name} ${voice.voiceURI}`)) ||
        spanish[0] ||
        null
      );
    }

    if ('speechSynthesis' in window) {
      window.speechSynthesis.onvoiceschanged = () => {
        selectLocalFemaleSpanishVoice();
      };
    }

    function speakLocal(text, onDone) {
      if (!('speechSynthesis' in window)) {
        if (typeof onDone === 'function') {
          onDone();
        }
        return;
      }
      const clean = String(text || '').trim();
      if (!clean) {
        if (typeof onDone === 'function') {
          onDone();
        }
        return;
      }
      try {
        window.speechSynthesis.cancel();
        dialogue.localSpeechStartedAt = performance.now();
        dialogue.suppressUntil = Math.max(dialogue.suppressUntil || 0, dialogue.localSpeechStartedAt + dialogue.localSelfMuteMs);
        const utterance = new SpeechSynthesisUtterance(clean);
        utterance.lang = 'es-ES';
        const selectedVoice = selectLocalFemaleSpanishVoice();
        if (selectedVoice) {
          utterance.voice = selectedVoice;
          utterance.lang = selectedVoice.lang || 'es-ES';
        }
        utterance.rate = 1.08;
        utterance.onend = () => {
          if (typeof onDone === 'function') {
            onDone();
          }
        };
        utterance.onerror = () => {
          if (typeof onDone === 'function') {
            onDone();
          }
        };
        window.speechSynthesis.speak(utterance);
      } catch (_) {
        if (typeof onDone === 'function') {
          onDone();
        }
      }
    }

    function setImportProgress(percent) {
      const value = Math.max(0, Math.min(100, Number(percent || 0)));
      els.importProgress.style.width = `${value}%`;
    }

    function setPrepareProgress(percent) {
      const value = Math.max(0, Math.min(100, Number(percent || 0)));
      els.prepareProgress.style.width = `${value}%`;
    }

    function renderPrepareStatus(prepare) {
      if (!prepare) {
        return;
      }
      setPrepareProgress(prepare.percent || 0);
      const total = prepare.total || 0;
      const done = (prepare.cached || 0) + (prepare.generated || 0) + (prepare.failed || 0);
      if (prepare.status === 'running' || prepare.status === 'canceling') {
        els.prepareInfo.textContent = `Preparando audio ${done}/${total}. Cache ${prepare.cached || 0}, nuevos ${prepare.generated || 0}.`;
      } else if (prepare.status === 'done') {
        els.prepareInfo.textContent = `Documento preparado. Cache ${prepare.cached || 0}, nuevos ${prepare.generated || 0}.`;
      } else if (prepare.status === 'canceled') {
        els.prepareInfo.textContent = 'Preparación cancelada.';
      } else if (prepare.status === 'error') {
        els.prepareInfo.textContent = prepare.message || 'No pude preparar el documento.';
      } else {
        els.prepareInfo.textContent = total ? 'Audio pendiente de preparar.' : 'Audio sin preparar.';
      }
    }

    function renderStatus(data) {
      const selectedNotesDocId = data.doc_id || LAB_NOTES_DOC_ID;
      const shouldRefreshNotes = selectedNotesDocId !== notesState.docId || data.current !== notesState.current || Boolean(data.notes && data.notes.count !== notesState.items.length);
      status = data;
      els.docTitle.textContent = data.title || 'Ningún documento activo';
      els.docMeta.textContent = `Bloque ${data.current || 0} de ${data.total || 0}`;
      els.jumpInput.max = data.total || 1;
      els.jumpInput.value = data.current || 1;
      els.chunk.textContent = data.text || 'Subí un TXT o MD para empezar.';
      els.chunk.classList.toggle('empty', !data.text);
      const ttsOk = Boolean(data.tts && data.tts.ok);
      els.ttsDot.classList.toggle('ok', ttsOk);
      els.ttsStatus.textContent = ttsOk ? 'TTS listo' : 'TTS no disponible';
      renderPrepareStatus(data.prepare);
      if (shouldRefreshNotes) {
        refreshNotes().catch(() => {});
      }
    }

    function noteReference(note) {
      if (String(note && note.source_kind || '').toLowerCase() === 'laboratory') {
        return `L${Number(note && note.anchor_number || 1)}`;
      }
      return `B${Number(note && note.chunk_number || note && note.anchor_number || 1)}`;
    }

    function renderNotes(items, activeDocId = '') {
      const notes = Array.isArray(items) ? items : [];
      const selectedDocId = activeDocId || status && status.doc_id || '';
      const laboratoryMode = selectedDocId === LAB_NOTES_DOC_ID;
      const hasLabNotes = notes.some(note => String(note && note.source_kind || '').toLowerCase() === 'laboratory');
      const hasDocumentNotes = notes.some(note => String(note && note.source_kind || '').toLowerCase() !== 'laboratory');
      notesState = {
        docId: selectedDocId,
        current: status && status.current || 0,
        items: notes
      };
      const currentCount = notes.filter(note => String(note && note.source_kind || '').toLowerCase() !== 'laboratory' && Number(note.chunk_number || 0) === Number(notesState.current || 0)).length;
      els.notesSummary.textContent = `${laboratoryMode ? 'Notas del laboratorio' : (hasLabNotes && hasDocumentNotes ? 'Notas del documento y laboratorio' : 'Notas del documento')} (${notes.length})`;
      if (!notes.length) {
        els.notesInfo.textContent = laboratoryMode ? 'Sin notas del laboratorio todavía.' : 'Sin notas todavía.';
      } else if (laboratoryMode) {
        els.notesInfo.textContent = `${notes.length} nota${notes.length === 1 ? '' : 's'} en el laboratorio.`;
      } else if (hasLabNotes && hasDocumentNotes) {
        const labCount = notes.filter(note => String(note && note.source_kind || '').toLowerCase() === 'laboratory').length;
        els.notesInfo.textContent = `${currentCount} nota${currentCount === 1 ? '' : 's'} en este bloque y ${labCount} de laboratorio.`;
      } else {
        els.notesInfo.textContent = `${currentCount} nota${currentCount === 1 ? '' : 's'} en este bloque.`;
      }
      els.notesList.replaceChildren();
      if (!notes.length) {
        return;
      }
      for (const note of notes) {
        const row = document.createElement('details');
        row.className = 'note-row';
        if (String(note && note.source_kind || '').toLowerCase() !== 'laboratory' && Number(note.chunk_number || 0) === Number(notesState.current || 0)) {
          row.classList.add('current');
        }
        const summary = document.createElement('summary');
        const label = document.createElement('span');
        label.className = 'note-label';
        label.textContent = `${noteReference(note)} ${compactNoteLabel(note)}`.trim();
        label.title = note.text || '';
        const renameBtn = document.createElement('button');
        renameBtn.type = 'button';
        renameBtn.className = 'note-rename';
        renameBtn.textContent = '+';
        renameBtn.title = 'Editar nombre';
        renameBtn.setAttribute('aria-label', 'Editar nombre de la nota');
        renameBtn.addEventListener('click', event => {
          event.preventDefault();
          event.stopPropagation();
          renameNote(note);
        });
        summary.append(label, renameBtn);
        const text = document.createElement('p');
        text.className = 'note-text';
        text.textContent = note.text || '';
        const quote = document.createElement('p');
        quote.className = 'note-quote';
        quote.textContent = note.quote ? `Texto: ${note.quote}` : '';
        const actions = document.createElement('div');
        actions.className = 'note-actions';
        const goBtn = document.createElement('button');
        goBtn.type = 'button';
        if (String(note && note.source_kind || '').toLowerCase() === 'laboratory') {
          goBtn.textContent = 'Sin bloque';
          goBtn.disabled = true;
        } else {
          goBtn.textContent = 'Ir al bloque';
          goBtn.addEventListener('click', event => {
            event.preventDefault();
            goToNote(note);
          });
        }
        const editBtn = document.createElement('button');
        editBtn.type = 'button';
        editBtn.textContent = 'Editar';
        editBtn.addEventListener('click', event => {
          event.preventDefault();
          editNote(note);
        });
        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.textContent = 'Borrar';
        deleteBtn.addEventListener('click', event => {
          event.preventDefault();
          deleteNote(note);
        });
        actions.append(goBtn, editBtn, deleteBtn);
        row.append(summary, text);
        if (note.quote) {
          row.append(quote);
        }
        row.append(actions);
        els.notesList.appendChild(row);
      }
    }

    function compactNoteLabel(note) {
      const saved = String(note && note.label || '').trim();
      if (saved) {
        return saved;
      }
      const raw = String(note && note.text || '').trim();
      const words = raw.match(/[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+/g) || [];
      const stop = new Set(['a', 'al', 'bloque', 'como', 'con', 'de', 'del', 'el', 'en', 'es', 'esa', 'ese', 'esta', 'este', 'la', 'las', 'lo', 'los', 'nota', 'notas', 'para', 'por', 'que', 'se', 'sobre', 'toma', 'tomar', 'tomá', 'tome', 'un', 'una', 'y']);
      const selected = [];
      for (const word of words) {
        if (/^\\d+$/.test(word)) {
          continue;
        }
        if (stop.has(word.toLowerCase())) {
          continue;
        }
        selected.push(word);
        if (selected.length >= 3) {
          break;
        }
      }
      return (selected.length ? selected : words.slice(0, 3)).join(' ');
    }

    async function refreshNotes() {
      if (!status) {
        notesState = { docId: '', current: 0, items: [] };
        els.notesSummary.textContent = 'Notas del documento';
        els.notesInfo.textContent = 'Cargá un documento para tomar notas.';
        els.notesList.replaceChildren();
        return;
      }
      if (!status.doc_id) {
        const data = await api(`/api/notes?doc_id=${encodeURIComponent(LAB_NOTES_DOC_ID)}`);
        renderNotes(data.items || [], data.doc_id || LAB_NOTES_DOC_ID);
        return;
      }
      const [docData, labData] = await Promise.all([
        api(`/api/notes?doc_id=${encodeURIComponent(status.doc_id)}`),
        api(`/api/notes?doc_id=${encodeURIComponent(LAB_NOTES_DOC_ID)}`).catch(() => ({ items: [] }))
      ]);
      const merged = [...(docData.items || []), ...(labData.items || [])];
      renderNotes(merged, status.doc_id);
    }

    async function saveCurrentNote() {
      const text = els.noteInput.value.trim();
      if (!text) {
        log('Escribí una nota antes de guardarla.');
        return;
      }
      if (!status || !status.doc_id) {
        log('Cargá un documento antes de guardar notas.');
        return;
      }
      setBusy(true);
      try {
        const data = await api('/api/notes/create', { text });
        els.noteInput.value = '';
        renderNotes(data.items || [], data.note && data.note.doc_id || status.doc_id);
        log(`Nota guardada como ${noteReference(data.note || {})}.`);
      } catch (err) {
        log(`No pude guardar la nota: ${err.message}`);
      } finally {
        setBusy(false);
      }
    }

    async function goToNote(note) {
      if (String(note && note.source_kind || '').toLowerCase() === 'laboratory') {
        log(`La nota ${noteReference(note)} pertenece al laboratorio y no tiene bloque.`);
        return;
      }
      try {
        const data = await api('/api/jump', { index: Number(note.chunk_number || 1) });
        renderStatus(data);
        log(`Salté al bloque ${note.chunk_number || 1}.`);
      } catch (err) {
        log(`No pude ir a la nota: ${err.message}`);
      }
    }

    async function renameNote(note) {
      const currentLabel = compactNoteLabel(note);
      const nextLabel = window.prompt('Nombre corto de la nota', currentLabel);
      if (nextLabel === null) {
        return;
      }
      const label = nextLabel.trim();
      if (!label) {
        log('El nombre de la nota no puede quedar vacío.');
        return;
      }
      try {
        const data = await api('/api/notes/rename', { note_id: note.note_id, doc_id: note.doc_id, label });
        renderNotes(data.items || []);
        log('Nombre de nota actualizado.');
      } catch (err) {
        log(`No pude renombrar la nota: ${err.message}`);
      }
    }

    async function editNote(note) {
      const nextText = window.prompt('Editar nota', note.text || '');
      if (nextText === null) {
        return;
      }
      const text = nextText.trim();
      if (!text) {
        log('La nota no puede quedar vacía.');
        return;
      }
      try {
        const data = await api('/api/notes/update', { note_id: note.note_id, doc_id: note.doc_id, text });
        renderNotes(data.items || []);
        log('Nota actualizada.');
      } catch (err) {
        log(`No pude editar la nota: ${err.message}`);
      }
    }

    async function deleteNote(note) {
      if (!window.confirm('Borrar esta nota?')) {
        return;
      }
      try {
        const data = await api('/api/notes/delete', { note_id: note.note_id, doc_id: note.doc_id });
        renderNotes(data.items || []);
        log('Nota borrada.');
      } catch (err) {
        log(`No pude borrar la nota: ${err.message}`);
      }
    }

    function playAudio(data) {
      if (!data.audio_url) {
        return;
      }
      els.player.src = data.audio_url;
      els.player.play().catch(() => {
        log('Audio generado. Tocá play si el navegador bloqueó la reproducción automática.');
      });
    }

    function addChatMessage(kind, text) {
      const node = document.createElement('div');
      node.className = `chat-msg ${kind}`;
      const label = kind === 'user' ? 'Vos' : kind === 'assistant' ? 'Laboratorio' : 'Sistema';
      node.textContent = `${label}: ${text}`;
      els.chatLog.appendChild(node);
      els.chatLog.scrollTop = els.chatLog.scrollHeight;
      return node;
    }

    function setDialogueInfo(text) {
      els.dialogueInfo.textContent = text;
    }

    async function refresh() {
      const data = await api('/api/status');
      renderStatus(data);
    }

    function canReadFile(file) {
      const name = file.name.toLowerCase();
      const accepted = ['.txt', '.md', '.markdown', '.pdf', '.doc', '.docx', '.odt', '.ott', '.rtf', '.html', '.htm', '.csv', '.log'];
      return accepted.some(ext => name.endsWith(ext)) || file.type.startsWith('text/');
    }

    async function pollImportJob(jobId) {
      while (true) {
        await wait(700);
        const data = await api(`/api/import-status?id=${encodeURIComponent(jobId)}`);
        setImportProgress(data.percent || 0);
        const total = data.total ? ` ${data.current || 0}/${data.total}` : '';
        els.uploadInfo.textContent = `${data.filename}: ${data.message || data.stage || 'convirtiendo...'}${total}`;
        log(data.message || 'Convirtiendo documento...');
        if (data.status === 'done') {
          setImportProgress(100);
          return data.result;
        }
        if (data.status === 'error') {
          throw new Error(data.error || data.message || 'import_failed');
        }
      }
    }

    async function loadFile(file) {
      if (!file) {
        return;
      }
      if (!canReadFile(file)) {
        log('Ese formato todavía no lo reconozco. Probá PDF, DOCX, ODT, RTF, TXT o MD.');
        els.uploadInfo.textContent = `${file.name}: formato no soportado todavía.`;
        return;
      }
      setBusy(true);
      try {
        log('Preparando documento...');
        setImportProgress(0);
        els.uploadInfo.textContent = `${file.name}: convirtiendo para lectura...`;
        const url = `/api/import-file/start?filename=${encodeURIComponent(file.name)}&mime=${encodeURIComponent(file.type || '')}`;
        const res = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': file.type || 'application/octet-stream' },
          body: file
        });
        const started = await res.json();
        if (!res.ok || started.ok === false) {
          throw new Error(started.error || 'import_start_failed');
        }
        setImportProgress(started.percent || 1);
        els.uploadInfo.textContent = `${file.name}: documento recibido. Convirtiendo...`;
        const data = await pollImportJob(started.job_id);
        renderStatus(data);
        const convertedKb = data.converted_bytes ? ` Texto convertido: ${Math.max(1, Math.round(data.converted_bytes / 1024))} KB.` : '';
        els.uploadInfo.textContent = `${file.name} cargado. ${data.total || 0} bloques listos. ${data.import_detail || ''}.${convertedKb}`;
        els.player.removeAttribute('src');
        if (els.autoReadToggle.checked) {
          log('Texto cargado. Generando voz del primer bloque...');
          await readCurrent();
        } else {
          log('Texto cargado. La voz ya puede leer el bloque actual.');
        }
      } catch (err) {
        log(`No pude cargar el archivo: ${err.message}`);
      } finally {
        setBusy(false);
      }
    }

    async function navigate(path, body = {}) {
      setBusy(true);
      try {
        const data = await api(path, body);
        renderStatus(data);
        log('Ubicación actualizada.');
      } catch (err) {
        log(`No pude navegar: ${err.message}`);
      } finally {
        setBusy(false);
      }
    }

    async function readCurrent() {
      setBusy(true);
      try {
        log('Generando voz neural...');
        const data = await api('/api/read', { play: false });
        renderStatus(data);
        playAudio(data);
        log(`${data.cached ? 'Audio listo desde cache.' : 'Audio neural generado.'} Listo en ${data.ready_ms} ms; sintesis ${data.synthesis_ms || 0} ms.`);
      } catch (err) {
        log(`Falló la voz: ${err.message}`);
      } finally {
        setBusy(false);
      }
    }

    async function pollPrepare() {
      while (true) {
        await wait(1000);
        const data = await api('/api/prepare/status');
        renderPrepareStatus(data);
        if (!['running', 'canceling'].includes(data.status)) {
          return data;
        }
      }
    }

    async function prepareDocument() {
      setBusy(true);
      try {
        const data = await api('/api/prepare/start', { start: 'cursor' });
        renderPrepareStatus(data);
        log('Preparando audio del documento en segundo plano...');
        setBusy(false);
        await pollPrepare();
      } catch (err) {
        log(`No pude preparar el documento: ${err.message}`);
      } finally {
        setBusy(false);
      }
    }

    async function cancelPrepare() {
      try {
        const data = await api('/api/prepare/cancel', {});
        renderPrepareStatus(data);
        log('Cancelando preparación de audio...');
      } catch (err) {
        log(`No pude cancelar: ${err.message}`);
      }
    }

    async function readNextWhenAudioEnds() {
      if (!els.continuousToggle.checked || !status || !status.total || status.current >= status.total) {
        return;
      }
      setBusy(true);
      try {
        log('Avanzando al siguiente bloque...');
        const nextData = await api('/api/next', {});
        renderStatus(nextData);
      } catch (err) {
        log(`No pude avanzar: ${err.message}`);
        setBusy(false);
        return;
      }
      setBusy(false);
      await readCurrent();
    }

    async function sendChat() {
      const message = els.chatInput.value.trim();
      if (!message) {
        return;
      }
      els.chatInput.value = '';
      addChatMessage('user', message);
      if (dialogue.active) {
        setBusy(true);
        try {
          await sendTypedDialogue(message);
        } finally {
          setBusy(false);
        }
        return;
      }
      setBusy(true);
      try {
        addChatMessage('system', 'Pensando con el documento abierto...');
        const data = await api('/api/chat', { message, chunk_index: visibleChunkIndex() });
        const pending = els.chatLog.querySelector('.chat-msg.system:last-child');
        if (pending && pending.textContent.includes('Pensando')) {
          pending.remove();
        }
        addChatMessage('assistant', data.answer || '(sin respuesta)');
        if (data.note) {
          await refreshNotes();
        }
        log(`Chat listo con ${data.model || 'modelo local'} en ${data.duration_ms || 0} ms.`);
      } catch (err) {
        addChatMessage('system', `Falló el chat: ${err.message}`);
        log(`Falló el chat: ${err.message}`);
      } finally {
        setBusy(false);
      }
    }

    function stopDialoguePlaybackForTypedTurn() {
      if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
      els.dialoguePlayer.pause();
      els.dialoguePlayer.currentTime = 0;
      dialogue.speaking = false;
      dialogue.bargeInSpeechMs = 0;
      dialogue.speechMs = 0;
      dialogue.silenceMs = 0;
      dialogue.suppressUntil = performance.now() + 180;
    }

    async function playDialogueAnswer(data) {
      if (data.audio_url) {
        dialogue.speaking = true;
        els.dialoguePlayer.src = data.audio_url;
        try {
          await els.dialoguePlayer.play();
        } catch (_) {
          dialogue.speaking = false;
          log('Voz generada. Tocá play si el navegador bloqueó la reproducción automática.');
        }
      } else if (data.answer && data.provider === 'text_ack') {
        dialogue.speaking = true;
        speakLocal(data.answer, () => {
          dialogue.speaking = false;
          if (dialogue.active) {
            setDialogueInfo('Escuchando...');
          }
        });
      }
    }

    async function sendTypedDialogue(message) {
      if (dialogue.speaking) {
        stopDialoguePlaybackForTypedTurn();
      }
      dialogue.processing = true;
      dialogue.recording = false;
      dialogue.finalizing = false;
      dialogue.pcmChunks = [];
      dialogue.pcmPreRoll = [];
      dialogue.pcmPreRollSamples = 0;
      const pending = addChatMessage('system', 'Dialogando por voz...');
      const startedAt = performance.now();
      try {
        const data = await api('/api/dialogue/turn', { text: message, chunk_index: visibleChunkIndex() });
        if (pending && pending.isConnected) {
          pending.remove();
        }
        if (data.model === 'reader_control') {
          addChatMessage('system', 'Respuesta detenida.');
        } else {
          addChatMessage('assistant', data.answer || '(sin respuesta)');
        }
        if (data.note) {
          await refreshNotes();
        }
        const wallMs = Math.round(performance.now() - startedAt);
        const info = `Texto respondido con voz | chat ${fmtMs(data.chat_ms)} | voz ${fmtMs(data.tts_ms)} | total ${fmtMs(data.duration_ms || wallMs)}`;
        setDialogueInfo(info);
        log(`Diálogo escrito listo con ${data.model || 'modelo local'} en ${wallMs} ms.`);
        await playDialogueAnswer(data);
      } catch (err) {
        if (pending && pending.isConnected) {
          pending.remove();
        }
        addChatMessage('system', `Falló el diálogo: ${err.message}`);
        setDialogueInfo(`Falló el diálogo: ${err.message}`);
        log(`Falló el diálogo: ${err.message}`);
      } finally {
        dialogue.processing = false;
        if (!dialogue.speaking && dialogue.active) {
          setDialogueInfo('Escuchando...');
        }
      }
    }

    async function clearLaboratoryHistory() {
      setBusy(true);
      try {
        const data = await api('/api/laboratory/reset', {});
        els.chatLog.innerHTML = '';
        addChatMessage('system', 'Historial de laboratorio borrado.');
        log(`Historial de laboratorio borrado (${data.chat_items || 0} chat, ${data.dialogue_items || 0} diálogo).`);
      } catch (err) {
        addChatMessage('system', `No pude borrar el historial: ${err.message}`);
        log(`No pude borrar el historial: ${err.message}`);
      } finally {
        setBusy(false);
      }
    }

    function dialogueMimeType() {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
        return 'audio/webm;codecs=opus';
      }
      if (window.MediaRecorder && MediaRecorder.isTypeSupported('audio/webm')) {
        return 'audio/webm';
      }
      return '';
    }

    async function toggleDialogue() {
      if (dialogue.active) {
        stopDialogue();
        return;
      }
      await startDialogue();
    }

    async function startDialogue() {
      if (!navigator.mediaDevices || !(window.AudioContext || window.webkitAudioContext)) {
        setDialogueInfo('Tu navegador no permite grabar audio desde esta página.');
        return;
      }
      try {
        api('/api/prepare/cancel', {}).catch(() => {});
        dialogue.stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            channelCount: 1,
            sampleRate: 48000,
            sampleSize: 16
          }
        });
        dialogue.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        dialogue.sampleRate = dialogue.audioContext.sampleRate || 48000;
        const source = dialogue.audioContext.createMediaStreamSource(dialogue.stream);
        dialogue.analyser = dialogue.audioContext.createAnalyser();
        dialogue.analyser.fftSize = 1024;
        source.connect(dialogue.analyser);
        const processor = dialogue.audioContext.createScriptProcessor(4096, 1, 1);
        const silentGain = dialogue.audioContext.createGain();
        silentGain.gain.value = 0;
        source.connect(processor);
        processor.connect(silentGain);
        silentGain.connect(dialogue.audioContext.destination);
        processor.onaudioprocess = handleDialoguePcm;
        dialogue.processor = processor;
        dialogue.silentGain = silentGain;
        dialogue.active = true;
        dialogue.speechMs = 0;
        dialogue.silenceMs = 0;
        dialogue.pcmChunks = [];
        dialogue.pcmPreRoll = [];
        dialogue.pcmPreRollSamples = 0;
        dialogue.noiseFloor = 0.012;
        dialogue.lastTick = performance.now();
        els.dialogueBtn.textContent = 'Detener diálogo';
        setDialogueInfo('Escuchando en modo rápido... hacé una pausa corta y respondo.');
        monitorDialogue();
      } catch (err) {
        setDialogueInfo(`No pude abrir el micrófono: ${err.message}`);
      }
    }

    function stopDialogue() {
      dialogue.active = false;
      dialogue.processing = false;
      dialogue.speaking = false;
      dialogue.bargeInSpeechMs = 0;
      dialogue.suppressUntil = 0;
      if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
      if (dialogue.monitorId) {
        cancelAnimationFrame(dialogue.monitorId);
      }
      if (dialogue.finalizeTimeoutId) {
        window.clearTimeout(dialogue.finalizeTimeoutId);
      }
      dialogue.finalizeTimeoutId = 0;
      if (dialogue.stream) {
        dialogue.stream.getTracks().forEach(track => track.stop());
      }
      if (dialogue.processor) {
        try {
          dialogue.processor.disconnect();
        } catch (_) {}
      }
      if (dialogue.silentGain) {
        try {
          dialogue.silentGain.disconnect();
        } catch (_) {}
      }
      if (dialogue.audioContext) {
        dialogue.audioContext.close().catch(() => {});
      }
      els.dialoguePlayer.pause();
      els.dialoguePlayer.removeAttribute('src');
      dialogue.stream = null;
      dialogue.audioContext = null;
      dialogue.analyser = null;
      dialogue.processor = null;
      dialogue.silentGain = null;
      dialogue.pcmChunks = [];
      dialogue.pcmPreRoll = [];
      dialogue.pcmPreRollSamples = 0;
      dialogue.finalizing = false;
      els.dialogueBtn.textContent = 'Dialogar';
      setDialogueInfo('Diálogo apagado.');
    }

    function monitorDialogue() {
      if (!dialogue.active || !dialogue.analyser) {
        return;
      }
      const now = performance.now();
      const delta = Math.max(16, Math.min(250, now - (dialogue.lastTick || now)));
      dialogue.lastTick = now;
      const level = micLevel();
      const threshold = Math.max(dialogue.minThreshold, dialogue.noiseFloor * dialogue.thresholdMultiplier);
      const releaseThreshold = threshold * 0.72;
      const isSpeech = dialogue.recording ? level >= releaseThreshold : level >= threshold;
      if (now < (dialogue.suppressUntil || 0)) {
        dialogue.speechMs = 0;
        dialogue.silenceMs += delta;
        dialogue.monitorId = requestAnimationFrame(monitorDialogue);
        return;
      }
      if (isSpeech) {
        dialogue.speechMs += delta;
        dialogue.silenceMs = 0;
      } else {
        dialogue.silenceMs += delta;
        dialogue.speechMs = Math.max(0, dialogue.speechMs - delta * 0.5);
        if (!dialogue.recording && !dialogue.processing && !dialogue.speaking) {
          dialogue.noiseFloor = dialogue.noiseFloor * 0.96 + level * 0.04;
        }
      }
      if (dialogue.speaking && isSpeech && !dialogue.recording) {
        dialogue.bargeInSpeechMs += delta;
        if (dialogue.bargeInSpeechMs >= dialogue.bargeInMs) {
          stopAssistantSpeechForBargeIn();
        }
      } else if (!isSpeech) {
        dialogue.bargeInSpeechMs = 0;
      }
      if (dialogue.speaking && !dialogue.recording) {
        // Mientras habla Fusion, no grabamos su propia voz como si fuera el usuario.
      } else if (!dialogue.speaking && !dialogue.processing && !dialogue.recording && !dialogue.finalizing && dialogue.speechMs >= dialogue.speechStartMs) {
        beginDialogueRecording();
      }
      if (dialogue.recording) {
        const elapsed = now - dialogue.startedAt;
        if ((elapsed >= dialogue.minRecordMs && dialogue.silenceMs >= dialogue.silenceStopMs) || elapsed >= dialogue.maxRecordMs) {
          stopDialogueRecording();
        }
      }
      dialogue.monitorId = requestAnimationFrame(monitorDialogue);
    }

    function stopAssistantSpeechForBargeIn() {
      const interruptedWhileSpeech = dialogue.bargeInSpeechMs > 0;
      if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
      els.dialoguePlayer.pause();
      els.dialoguePlayer.currentTime = 0;
      dialogue.speaking = false;
      dialogue.bargeInSpeechMs = 0;
      if (interruptedWhileSpeech) {
        // Conservamos el arranque de la frase que disparo el barge-in para que
        // comandos cortos como "toma nota..." no pierdan sus primeras silabas.
        dialogue.speechMs = Math.max(dialogue.speechMs, dialogue.speechStartMs);
      } else {
        dialogue.speechMs = 0;
      }
      dialogue.silenceMs = 0;
      dialogue.pcmChunks = [];
      dialogue.finalizing = false;
      dialogue.suppressUntil = performance.now() + 40;
      addChatMessage('system', 'Interrumpiste la respuesta.');
      setDialogueInfo('Te escucho...');
    }

    function micLevel() {
      const data = new Uint8Array(dialogue.analyser.fftSize);
      dialogue.analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const value of data) {
        const centered = (value - 128) / 128;
        sum += centered * centered;
      }
      return Math.sqrt(sum / data.length);
    }

    function dialoguePreRollLimitSamples() {
      return Math.max(0, Math.round((dialogue.sampleRate || 48000) * (dialogue.preRollMs / 1000)));
    }

    function appendPcmChunk(target, chunk) {
      if (!chunk || !chunk.length) {
        return 0;
      }
      target.push(chunk);
      return chunk.length;
    }

    function trimPcmPreRoll() {
      const limit = dialoguePreRollLimitSamples();
      while (dialogue.pcmPreRoll.length > 1 && dialogue.pcmPreRollSamples > limit) {
        const removed = dialogue.pcmPreRoll.shift();
        dialogue.pcmPreRollSamples = Math.max(0, dialogue.pcmPreRollSamples - (removed ? removed.length : 0));
      }
    }

    function handleDialoguePcm(event) {
      if (!dialogue.active || !event || !event.inputBuffer) {
        return;
      }
      const source = event.inputBuffer.getChannelData(0);
      if (!source || !source.length) {
        return;
      }
      const chunk = new Float32Array(source.length);
      chunk.set(source);
      if (!dialogue.recording && !dialogue.finalizing) {
        dialogue.pcmPreRollSamples += appendPcmChunk(dialogue.pcmPreRoll, chunk);
        trimPcmPreRoll();
        return;
      }
      if (dialogue.recording || (dialogue.finalizing && performance.now() <= (dialogue.captureStopAt || 0))) {
        appendPcmChunk(dialogue.pcmChunks, chunk);
      }
    }

    function encodeDialogueWav(chunks, sampleRate) {
      const safeRate = Math.max(8000, Number(sampleRate || 48000));
      const totalSamples = chunks.reduce((sum, chunk) => sum + (chunk ? chunk.length : 0), 0);
      const buffer = new ArrayBuffer(44 + totalSamples * 2);
      const view = new DataView(buffer);
      let offset = 0;
      const writeString = value => {
        for (let i = 0; i < value.length; i += 1) {
          view.setUint8(offset + i, value.charCodeAt(i));
        }
        offset += value.length;
      };
      const writeUint32 = value => {
        view.setUint32(offset, value, true);
        offset += 4;
      };
      const writeUint16 = value => {
        view.setUint16(offset, value, true);
        offset += 2;
      };
      writeString('RIFF');
      writeUint32(36 + totalSamples * 2);
      writeString('WAVE');
      writeString('fmt ');
      writeUint32(16);
      writeUint16(1);
      writeUint16(1);
      writeUint32(safeRate);
      writeUint32(safeRate * 2);
      writeUint16(2);
      writeUint16(16);
      writeString('data');
      writeUint32(totalSamples * 2);
      for (const chunk of chunks) {
        if (!chunk) {
          continue;
        }
        for (let i = 0; i < chunk.length; i += 1) {
          const sample = Math.max(-1, Math.min(1, chunk[i] || 0));
          view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
          offset += 2;
        }
      }
      return new Blob([buffer], { type: 'audio/wav' });
    }

    function finishDialogueRecording() {
      if (!dialogue.finalizing) {
        return;
      }
      dialogue.finalizing = false;
      dialogue.captureStopAt = 0;
      dialogue.finalizeTimeoutId = 0;
      const blob = encodeDialogueWav(dialogue.pcmChunks, dialogue.sampleRate);
      dialogue.pcmChunks = [];
      sendDialogueAudio(blob, 'audio/wav');
    }

    function beginDialogueRecording() {
      if (!dialogue.active || dialogue.recording || dialogue.processing || dialogue.finalizing || !dialogue.stream) {
        return;
      }
      const now = performance.now();
      dialogue.turnId += 1;
      dialogue.pcmChunks = dialogue.pcmPreRoll.slice();
      dialogue.recording = true;
      dialogue.finalizing = false;
      dialogue.chunkIndex = visibleChunkIndex();
      dialogue.startedAt = now;
      dialogue.turnStartedAt = now;
      dialogue.trace = {
        turnId: dialogue.turnId,
        speechStartAt: now,
        chunkIndex: dialogue.chunkIndex,
        chunkNumber: dialogue.chunkIndex === null || dialogue.chunkIndex === undefined ? null : dialogue.chunkIndex + 1,
        silenceStopMs: dialogue.silenceStopMs,
        finalFlushMs: dialogue.finalFlushMs,
        flushWaitMs: dialogueFlushWaitMs()
      };
      dialogue.silenceMs = 0;
      setDialogueInfo('Te escucho...');
    }

    function stopDialogueRecording() {
      if (!dialogue.recording || dialogue.finalizing) {
        return;
      }
      dialogue.recording = false;
      dialogue.finalizing = true;
      dialogue.captureStopAt = performance.now() + dialogueFlushWaitMs();
      const heardMs = Math.max(0, performance.now() - (dialogue.turnStartedAt || performance.now()));
      if (dialogue.trace) {
        dialogue.trace.speechStopAt = performance.now();
        dialogue.trace.recordedMs = heardMs;
      }
      setDialogueInfo(`Procesando (${Math.round(heardMs)} ms de audio)...`);
      if (dialogue.finalizeTimeoutId) {
        window.clearTimeout(dialogue.finalizeTimeoutId);
      }
      dialogue.finalizeTimeoutId = window.setTimeout(finishDialogueRecording, dialogueFlushWaitMs());
    }

    async function sendDialogueAudio(blob, mimeType) {
      if (!dialogue.active || dialogue.processing) {
        return;
      }
      dialogue.finalizing = false;
      dialogue.speechMs = 0;
      dialogue.silenceMs = 0;
      if (blob.size < 1200) {
        setDialogueInfo('Escuchando...');
        return;
      }
      dialogue.processing = true;
      const requestStartedAt = performance.now();
      const turnTrace = dialogue.trace ? { ...dialogue.trace, sendStartedAt: requestStartedAt, blobSize: blob.size } : { sendStartedAt: requestStartedAt, blobSize: blob.size };
      try {
        const params = new URLSearchParams({ filename: 'dialogue.wav' });
        if (dialogue.chunkIndex !== null && dialogue.chunkIndex !== undefined) {
          params.set('chunk_index', String(dialogue.chunkIndex));
        }
        const res = await fetch(`/api/dialogue/turn?${params.toString()}`, {
          method: 'POST',
          headers: { 'Content-Type': mimeType || blob.type || 'audio/webm' },
          body: blob
        });
        const data = await res.json();
        turnTrace.responseAt = performance.now();
        if (!res.ok || data.ok === false) {
          const provider = data.stt_provider ? ` (${data.stt_provider})` : '';
          const detail = data.detail ? `: ${data.detail}` : '';
          throw new Error(`${data.error || 'dialogue_failed'}${provider}${detail}`);
        }
        const wallMs = Math.round(performance.now() - requestStartedAt);
        const traceText = formatDialogueTrace(data, turnTrace, wallMs);
        if (data.ignored || data.detail === 'hallucinated_transcript') {
          addChatMessage('system', 'Ignoré una transcripción espuria de Whisper.');
          addChatMessage('system', traceText);
          setDialogueInfo(traceText);
          return;
        }
        addChatMessage('user', data.transcript || '(audio)');
        if (data.model === 'reader_control') {
          addChatMessage('system', 'Respuesta detenida.');
        } else {
          addChatMessage('assistant', data.answer || '(sin respuesta)');
        }
        if (data.note) {
          await refreshNotes();
        }
        addChatMessage('system', traceText);
        setDialogueInfo(traceText);
        await playDialogueAnswer(data);
      } catch (err) {
        addChatMessage('system', `Falló el diálogo: ${err.message}`);
        setDialogueInfo(`Falló el diálogo: ${err.message}`);
      } finally {
        dialogue.processing = false;
        dialogue.chunkIndex = null;
        if (!dialogue.speaking && dialogue.active) {
          setDialogueInfo('Escuchando...');
        }
      }
    }

    els.prevBtn.addEventListener('click', () => navigate('/api/previous'));
    els.nextBtn.addEventListener('click', () => navigate('/api/next'));
    els.repeatBtn.addEventListener('click', readCurrent);
    els.readBtn.addEventListener('click', readCurrent);
    els.jumpBtn.addEventListener('click', () => navigate('/api/jump', { index: Number(els.jumpInput.value || 1) }));
    els.prepareBtn.addEventListener('click', prepareDocument);
    els.cancelPrepareBtn.addEventListener('click', cancelPrepare);
    els.saveNoteBtn.addEventListener('click', saveCurrentNote);
    els.sendChatBtn.addEventListener('click', sendChat);
    els.clearLabHistoryBtn.addEventListener('click', clearLaboratoryHistory);
    els.dialogueBtn.addEventListener('click', toggleDialogue);
    els.chatInput.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendChat();
      }
    });
    els.player.addEventListener('ended', readNextWhenAudioEnds);
    els.dialoguePlayer.addEventListener('ended', () => {
      dialogue.speaking = false;
      if (dialogue.active) {
        setDialogueInfo('Escuchando...');
      }
    });
    els.chooseFileBtn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      els.fileInput.click();
    });
    els.dropzone.addEventListener('click', () => els.fileInput.click());
    els.dropzone.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        els.fileInput.click();
      }
    });
    els.fileInput.addEventListener('change', () => {
      loadFile(els.fileInput.files && els.fileInput.files[0]);
      els.fileInput.value = '';
    });
    ['dragenter', 'dragover'].forEach(name => {
      els.dropzone.addEventListener(name, event => {
        event.preventDefault();
        els.dropzone.classList.add('dragover');
      });
    });
    ['dragleave', 'drop'].forEach(name => {
      els.dropzone.addEventListener(name, event => {
        event.preventDefault();
        els.dropzone.classList.remove('dragover');
      });
    });
    els.dropzone.addEventListener('drop', event => {
      const files = event.dataTransfer && event.dataTransfer.files;
      loadFile(files && files[0]);
    });

    refresh().catch(err => log(`Arranque incompleto: ${err.message}`));
  </script>
</body>
</html>
"""


def library_items() -> list[dict]:
    if not LIBRARY_ROOT.exists():
        return []
    items: list[dict] = []
    for path in sorted(LIBRARY_ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_LIBRARY_SUFFIXES:
            continue
        rel = path.relative_to(LIBRARY_ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            text = ""
        preview = " ".join(text.split())[:170]
        items.append({
            "id": rel,
            "title": path.name,
            "bytes": path.stat().st_size,
            "preview": preview,
        })
    return items


def resolve_library_path(book_id: str) -> Path:
    raw = unquote(str(book_id or "")).strip()
    rel = Path(raw)
    if not raw or rel.is_absolute() or any(part == ".." for part in rel.parts):
        raise ValueError("invalid_book_id")
    path = (LIBRARY_ROOT / rel).resolve()
    library_root = LIBRARY_ROOT.resolve()
    if path != library_root and library_root not in path.parents:
        raise ValueError("book_outside_library")
    if path.suffix.lower() not in ALLOWED_LIBRARY_SUFFIXES:
        raise ValueError("unsupported_book_type")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("book_not_found")
    return path


def audio_url_for(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value).resolve()
    cache_root = APP.cache.root.resolve()
    if path.parent != cache_root or not path.exists():
        return ""
    return f"/audio/{path.name}"


def cached_audio_path(url_path: str) -> Path | None:
    filename = Path(unquote(url_path.removeprefix("/audio/"))).name
    audio_path = (APP.cache.root / filename).resolve()
    cache_root = APP.cache.root.resolve()
    if audio_path.parent != cache_root or not audio_path.exists():
        return None
    return audio_path


def load_imported_document(imported) -> dict:
    CONVERTED_ROOT.mkdir(parents=True, exist_ok=True)
    target = CONVERTED_ROOT / f"{imported.doc_id}.txt"
    target.write_text(imported.text, encoding="utf-8")
    out = APP.load_text(imported.doc_id, imported.title, imported.text, prefetch=False, source_path=str(target), source_type=imported.source_type)
    out["source_type"] = imported.source_type
    out["import_detail"] = imported.detail
    out["converted_text"] = str(target)
    out["converted_bytes"] = target.stat().st_size
    return out


def new_import_job(filename: str, mime: str, upload_path: Path, size_bytes: int) -> dict:
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    job = {
        "ok": True,
        "job_id": job_id,
        "filename": filename,
        "mime": mime,
        "status": "queued",
        "stage": "queued",
        "current": 0,
        "total": 0,
        "percent": 0,
        "message": "Documento recibido. Esperando conversión...",
        "size_bytes": size_bytes,
        "created_ts": now,
        "updated_ts": now,
        "result": None,
        "error": "",
    }
    with IMPORT_JOBS_LOCK:
        IMPORT_JOBS[job_id] = job
        prune_import_jobs_locked()
    return dict(job)


def prune_import_jobs_locked(max_age_seconds: int = 6 * 60 * 60) -> None:
    now = time.time()
    stale = [
        job_id
        for job_id, job in IMPORT_JOBS.items()
        if now - float(job.get("updated_ts") or job.get("created_ts") or now) > max_age_seconds
        and str(job.get("status")) in {"done", "error"}
    ]
    for job_id in stale:
        IMPORT_JOBS.pop(job_id, None)


def update_import_job(job_id: str, **changes) -> None:
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        if not job:
            return
        job.update(changes)
        current = int(job.get("current") or 0)
        total = int(job.get("total") or 0)
        if total > 0:
            job["percent"] = max(0, min(100, int(current * 100 / total)))
        job["updated_ts"] = time.time()


def import_progress_for(job_id: str):
    def progress(stage: str, current: int = 0, total: int = 0, message: str = "") -> None:
        update_import_job(job_id, status="running", stage=stage, current=int(current or 0), total=int(total or 0), message=message or stage)

    return progress


def import_job_worker(job_id: str, filename: str, upload_path: Path, mime: str) -> None:
    update_import_job(job_id, status="running", stage="starting", message="Preparando conversión...")
    try:
        imported = import_document_path(filename, upload_path, mime=mime, progress=import_progress_for(job_id))
        update_import_job(job_id, status="running", stage="loading", current=0, total=0, message="Cargando texto convertido en el lector...")
        result = load_imported_document(imported)
        update_import_job(
            job_id,
            status="done",
            stage="done",
            current=1,
            total=1,
            percent=100,
            message=f"{filename} cargado. {result.get('total') or 0} bloques listos.",
            result=result,
        )
    except Exception as exc:
        update_import_job(job_id, status="error", stage="error", message=f"No pude convertir el documento: {exc}", error=str(exc))
    finally:
        upload_path.unlink(missing_ok=True)


def get_import_job(job_id: str) -> dict | None:
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        return dict(job) if job else None


class Handler(BaseHTTPRequestHandler):
    server_version = "FusionReaderV2/0.1"

    def _send(self, status: int, content_type: str, raw: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", raw)

    def _result(self, status: int, payload: dict) -> None:
        out = dict(payload)
        if out.get("audio"):
            out["audio_url"] = audio_url_for(str(out.get("audio") or ""))
        self._json(status, out)

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _read_body_to_temp(self, filename: str) -> Path:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            raise ValueError("missing_file_data")
        suffix = Path(filename).suffix
        fd, name = tempfile.mkstemp(prefix="fusion_reader_upload_", suffix=suffix)
        path = Path(name)
        remaining = length
        try:
            with os.fdopen(fd, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if remaining:
            path.unlink(missing_ok=True)
            raise ValueError("incomplete_upload")
        return path

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
            return
        if path in ("/health", "/api/status"):
            self._json(200, APP.status())
            return
        if path == "/api/library":
            self._json(200, {"ok": True, "items": library_items()})
            return
        if path == "/api/voice/voices":
            self._json(200, APP.voices())
            return
        if path == "/api/voice/metrics":
            self._json(200, APP.recent_voice_metrics())
            return
        if path == "/api/voice/metrics/summary":
            self._json(200, APP.voice_metrics_summary())
            return
        if path == "/api/voice/metrics/documents":
            self._json(200, APP.voice_metrics_by_document())
            return
        if path == "/api/voice/metrics/chunks":
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            doc_id = str((params.get("doc_id") or [""])[0])
            limit = int((params.get("limit") or ["20"])[0])
            self._json(200, APP.voice_metrics_by_chunk(doc_id=doc_id, limit=limit))
            return
        if path == "/api/prepare/status":
            self._json(200, APP.prepare_status())
            return
        if path == "/api/notes":
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            doc_id = str((params.get("doc_id") or [""])[0])
            current_only = str((params.get("current_only") or ["0"])[0]).lower() in {"1", "true", "yes"}
            chunk_index_raw = str((params.get("chunk_index") or [""])[0])
            chunk_index = int(chunk_index_raw) if chunk_index_raw else None
            self._json(200, APP.list_notes(doc_id=doc_id, chunk_index=chunk_index, current_only=current_only))
            return
        if path == "/api/dialogue/status":
            self._json(200, APP.dialogue_status())
            return
        if path == "/api/import-status":
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            job_id = str((params.get("id") or [""])[0])
            job = get_import_job(job_id)
            if not job:
                self._json(404, {"ok": False, "error": "import_job_not_found"})
                return
            self._json(200, job)
            return
        if path.startswith("/audio/"):
            audio_path = cached_audio_path(path)
            if not audio_path:
                self._json(404, {"ok": False, "error": "audio_not_found"})
                return
            self._send(200, "audio/wav", audio_path.read_bytes())
            return
        self._json(404, {"ok": False, "error": "not_found"})

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            raw = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return
        if path.startswith("/audio/"):
            audio_path = cached_audio_path(path)
            if not audio_path:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(audio_path.stat().st_size))
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/import-file/start":
                params = parse_qs(parsed.query)
                filename = str((params.get("filename") or ["documento"])[0])
                mime = str((params.get("mime") or [self.headers.get("Content-Type", "") or ""])[0])
                tmp_path = self._read_body_to_temp(filename)
                job = new_import_job(filename, mime, tmp_path, tmp_path.stat().st_size)
                thread = threading.Thread(
                    target=import_job_worker,
                    args=(str(job["job_id"]), filename, tmp_path, mime),
                    name=f"fusion-import-{job['job_id']}",
                    daemon=True,
                )
                thread.start()
                self._json(202, job)
                return
            if path == "/api/import-file":
                params = parse_qs(parsed.query)
                filename = str((params.get("filename") or ["documento"])[0])
                mime = str((params.get("mime") or [self.headers.get("Content-Type", "") or ""])[0])
                tmp_path = self._read_body_to_temp(filename)
                try:
                    imported = import_document_path(filename, tmp_path, mime=mime)
                finally:
                    tmp_path.unlink(missing_ok=True)
                self._json(200, load_imported_document(imported))
                return
            if path == "/api/dialogue/turn" and "application/json" not in (self.headers.get("Content-Type", "") or ""):
                content_type = self.headers.get("Content-Type", "") or ""
                params = parse_qs(parsed.query)
                filename = str((params.get("filename") or ["dialogue.webm"])[0])
                raw_chunk_index = (params.get("chunk_index") or [None])[0]
                chunk_index = int(raw_chunk_index) if raw_chunk_index not in (None, "") else None
                tmp_path = self._read_body_to_temp(filename)
                try:
                    self._result(200, APP.dialogue_turn_audio(tmp_path, mime=content_type, model=str((params.get("model") or [""])[0]), chunk_index=chunk_index))
                finally:
                    tmp_path.unlink(missing_ok=True)
                return
            payload = self._payload()
            if path == "/api/load":
                if payload.get("book_id"):
                    self._json(200, APP.load_file(resolve_library_path(str(payload.get("book_id"))), prefetch=False))
                    return
                if payload.get("text"):
                    self._json(
                        200,
                        APP.load_text(
                            str(payload.get("doc_id") or "manual"),
                            str(payload.get("title") or "Manual"),
                            str(payload.get("text")),
                            prefetch=False,
                            source_type="manual",
                        ),
                    )
                    return
                if payload.get("path"):
                    self._json(200, APP.load_file(resolve_library_path(str(payload.get("path"))), prefetch=False))
                    return
                self._json(400, {"ok": False, "error": "missing_text_or_book_id"})
                return
            if path == "/api/import":
                filename = str(payload.get("filename") or "documento")
                mime = str(payload.get("mime") or "")
                raw_b64 = str(payload.get("data_b64") or "")
                if not raw_b64:
                    self._json(400, {"ok": False, "error": "missing_file_data"})
                    return
                imported = import_document_bytes(filename, base64.b64decode(raw_b64), mime=mime)
                self._json(200, load_imported_document(imported))
                return
            if path == "/api/read":
                self._result(200, APP.read_current(play=bool(payload.get("play", False))))
                return
            if path == "/api/next":
                self._json(200, APP.next())
                return
            if path == "/api/previous":
                self._json(200, APP.previous())
                return
            if path == "/api/jump":
                self._json(200, APP.jump(int(payload.get("index", 1))))
                return
            if path == "/api/prepare/start":
                self._json(200, APP.prepare_document(start=str(payload.get("start") or "cursor")))
                return
            if path == "/api/prepare/cancel":
                self._json(200, APP.cancel_prepare())
                return
            if path == "/api/notes/create":
                chunk_index = payload.get("chunk_index")
                self._json(200, APP.create_note(str(payload.get("text") or ""), chunk_index=int(chunk_index) if chunk_index is not None else None))
                return
            if path == "/api/notes/update":
                self._json(200, APP.update_note(str(payload.get("note_id") or ""), str(payload.get("text") or ""), doc_id=str(payload.get("doc_id") or "")))
                return
            if path == "/api/notes/rename":
                self._json(200, APP.rename_note(str(payload.get("note_id") or ""), str(payload.get("label") or ""), doc_id=str(payload.get("doc_id") or "")))
                return
            if path == "/api/notes/delete":
                self._json(200, APP.delete_note(str(payload.get("note_id") or ""), doc_id=str(payload.get("doc_id") or "")))
                return
            if path == "/api/dialogue/reset":
                self._json(200, APP.dialogue_reset())
                return
            if path in ("/api/laboratory/reset", "/api/chat/reset"):
                self._json(200, APP.clear_laboratory_history())
                return
            if path == "/api/dialogue/turn":
                content_type = self.headers.get("Content-Type", "") or ""
                if "application/json" in content_type:
                    raw_chunk_index = payload.get("chunk_index")
                    self._result(200, APP.dialogue_turn_text(str(payload.get("text") or ""), model=str(payload.get("model") or ""), chunk_index=int(raw_chunk_index) if raw_chunk_index is not None else None))
                    return
            if path == "/api/voice/test":
                self._result(200, APP.test_voice(str(payload.get("text") or "Prueba de voz neural del lector conversacional."), play=bool(payload.get("play", False))))
                return
            if path == "/api/chat":
                raw_chunk_index = payload.get("chunk_index")
                self._result(200, APP.chat(str(payload.get("message") or ""), model=str(payload.get("model") or ""), chunk_index=int(raw_chunk_index) if raw_chunk_index is not None else None))
                return
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return
        self._json(404, {"ok": False, "error": "not_found"})


def main() -> None:
    print(f"Fusion Reader v2 API listening on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
