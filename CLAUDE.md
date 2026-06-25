# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

KindCaddy is an AI-powered golf caddy app (GPT-4o) with three interfaces: a Python CLI, a FastAPI REST backend, and a native iOS SwiftUI app. The system builds contextual prompts for the LLM using golfer profiles, round state, agent observations, and historical insights.

## Commands

### Python Backend

```bash
# Install dependencies
pip install -r requirements.txt

# Run CLI caddy
OPENAI_API_KEY=sk-... python -m kindcaddy --profile profiles/example.json

# Run API server
OPENAI_API_KEY=sk-... uvicorn kindcaddy.api:app --host 0.0.0.0 --port 8000

# Initialize database manually
KINDCADDY_DB_PATH=data/kindcaddy.db python -c "from kindcaddy.db import init_db; init_db()"
```

### Tests

```bash
# Unit tests (pytest)
python -m pytest tests/

# Run a single test file
python -m pytest tests/test_memory.py

# E2E integration test (requires running server on port 8765)
KINDCADDY_DB_PATH=data/test_e2e.db uvicorn kindcaddy.api:app --port 8765 &
python tests/test_e2e_round.py

# Benchmark model quality against test scenarios
OPENAI_API_KEY=sk-... python scenarios/benchmark.py --model gpt-4o
```

### iOS App

Open `ios/KindCaddy/KindCaddy.xcodeproj` in Xcode, select a development team in Signing & Capabilities, then build and run. Set backend URL and API key in `ios/KindCaddy/KindCaddy/Config.swift`.

## Architecture

### Request Flow

When a user asks for advice, the flow is:
1. iOS `APIClient` (or CLI) sends `POST /advice` with `{session_id, text}`
2. `api.py` looks up the in-memory `Caddy` instance for that session
3. `Caddy.get_advice()` in `caddy.py` builds a full context: profile + round state + agent observations + conversation history + user insights
4. `prompts.py` generates the system prompt (club selection algorithm, wind/altitude rules, etc.)
5. GPT-4o returns advice, which is streamed back to the client

**Sessions are memory-resident** — the `Caddy` object lives in a dict in `api.py`, not in the database. Round data is only written to SQLite on `POST /finish-round`.

### Agent Tool System

Four proactive agents in `kindcaddy/agent/` follow a protocol defined in `base.py`:
- `AgentTool` protocol: `check(state) -> bool` + `execute(state) -> str`
- Triggered at events: `interaction`, `hole_change`, `score_logged`, `shot_logged`
- Each tool surfaces text alerts that get appended to the LLM context automatically

Agents: `WeatherTool`, `ShotTracker` (miss pattern detection), `ScoreCalculator` (strategic alerts, turn report), `FatigueModel` (back-9 carry reduction).

### Database (`kindcaddy/db.py`)

SQLite with WAL mode. `init_db()` is idempotent — safe to call on every startup. Key tables:
- `users` — auth (Apple/Google/email + bcrypt)
- `rounds` — stores a `profile_snapshot` JSON at start so old rounds are self-contained
- `round_shots` / `round_scores` — shot and hole-by-hole data
- `user_insights` — computed aggregates (miss tendencies, club carry deltas, scoring trends); updated after each finished round

### Authentication (`kindcaddy/auth.py`)

JWT bearer tokens (HS256, 24-hour expiry). Three providers: Apple Sign-In (OIDC), Google Sign-In (OIDC), email + bcrypt. The `KINDCADDY_JWT_SECRET` env var must be set in production.

### Key Environment Variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Required for all LLM calls |
| `KINDCADDY_DB_PATH` | SQLite path (default: `data/kindcaddy.db`) |
| `KINDCADDY_JWT_SECRET` | JWT signing secret (production) |

### iOS ↔ Backend

The iOS app mirrors backend Pydantic models in `Models.swift`. `APIClient.swift` handles JWT auth, attaches the token to all requests, and uses Swift actors for thread safety. On-device speech recognition (no audio sent to server); TTS playback comes back as audio from the API.

### Core Product Logic

The heart of the product is in `kindcaddy/prompts.py` — detailed instructions to GPT-4o for a 10-step club selection algorithm covering wind, altitude, temperature, lie, shot shape, and fatigue. Editing this file has the highest impact on advice quality.

## Deployment

See `deploy/OPERATIONS.md` for the full EC2 runbook. The server runs as a systemd service (`kindcaddy.service`) behind a Caddy reverse proxy for HTTPS.
