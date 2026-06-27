# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**diagnostico-ia** is a predictive health AI system ("Sistema de Saúde Preditiva com IA") — a Python FastAPI backend that accepts free-text symptom reports, analyzes them using NLP, and returns possible diagnoses with natural and pharmaceutical treatment recommendations. All sessions are authenticated via Firebase and persisted to MongoDB.

## Running the Application

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

The server starts at `http://localhost:8000`. There is no frontend in this repo; it is a pure REST API.

## Repository Structure Warning

The repository has a deeply nested directory artifact caused by repeated commits into subdirectories named `backend/`. The canonical source files are scattered across these layers. The **intended** logical layout is:

```
backend/
  firebase.py          # Firebase Admin SDK init & token verification
  database.py          # MongoDB connection; exposes historico_collection & usuarios_collection
  nlp_model.py         # HuggingFace transformers NLP (distilbert Q&A pipeline)
  laudos.py            # Generates structured medical report dicts
  main.py              # FastAPI entry point (the deepest main.py is most complete)
dados/
  sintomas.json        # Array of {doenca, sintomas[], tratamentos[]} — the symptom-to-disease map
  tratamentos_naturais.json        # Natural treatment records keyed by doenca
  tratamentos_farmaceuticos.json   # Pharmaceutical treatment records keyed by doenca
models/
  sintomas_input.py    # Pydantic model: SintomasInput {texto: str, usuario_id: str}
requirements.txt
backend/credenciais-firebase.json  # NOT in repo — must be provided at deploy time
```

## Key Architecture

### Request Flow

1. Client sends `POST /diagnostico/` with `Authorization: Bearer <firebase_token>` and `{"texto": "<symptom description>", "usuario_id": "<uid>"}`.
2. `firebase.py:verificar_token()` validates the Firebase JWT and extracts the UID. Returns `401` on failure.
3. `main.py` loads `sintomas.json` at startup and does keyword matching: if any symptom string from an entry appears in the lowercased input text, that disease is flagged.
4. Matching diseases are used to filter `tratamentos_naturais.json` and `tratamentos_farmaceuticos.json`.
5. `laudos.py:gerar_laudo()` bundles everything into a report dict (with UTC timestamp and a fixed disclaimer).
6. The laudo is saved to `database.py:historico_collection` (MongoDB Atlas).
7. The full laudo is returned as JSON.

### NLP Model

`nlp_model.py` wraps `distilbert-base-uncased-distilled-squad` from HuggingFace as a Q&A pipeline (`NLPDiagnostico.responder_pergunta(pergunta, contexto)`). The class is instantiated in `main.py` but the current diagnostic flow uses keyword matching against `sintomas.json` — the NLP pipeline is available for future richer analysis.

### Data Files

`sintomas.json` drives diagnosis: each entry maps a disease to a list of symptom keywords and suggested treatments. `tratamentos_naturais.json` and `tratamentos_farmaceuticos.json` provide detailed treatment objects filtered by disease name. These files must be placed under `dados/` relative to where `main.py` runs.

### External Services

- **Firebase Admin SDK**: Requires `backend/credenciais-firebase.json` (service account key). Set via the path in `firebase.py`.
- **MongoDB Atlas**: Connection string goes in `database.py`. Currently hardcoded as a placeholder (`<usuario>:<senha>@<cluster>`); replace with the real URI or use an environment variable.

## Dependencies

```
fastapi
uvicorn
pymongo
firebase-admin
transformers
pydantic
```

Note: `transformers` will download the distilbert model on first run (~250 MB). Pre-download or cache the model in the deployment environment.

## Authentication

All diagnostic requests require a valid Firebase ID token in the `Authorization: Bearer <token>` header. Unauthenticated requests receive `401 Unauthorized`. Firebase project credentials must be configured before the app can start.
