# Lector Conversacional — Agent Rules

## Identity
This is a **conversational reader** — not a general assistant, browser, or desktop automation tool.

## Allowed Operations
- Reader commands: load, read, navigate, bookmark, resume
- Voice/TTS/STT operations
- Chat about current reading content
- Model routing (Ollama/cloud) for reader conversations

## Prohibited Operations
- No browser automation
- No YouTube/web integration
- No n8n workflows
- No desktop file operations
- No general AI assistant tasks

## Project Isolation
- This repo is self-contained
- Port 8000 is the reader HTTP server
- AllTalk TTS on port 7851 (external dependency)
- Ollama for LLM backend (external dependency)

## Testing
```bash
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```
