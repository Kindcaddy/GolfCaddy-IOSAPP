# KindCaddy

AI golf caddy powered by GPT-4o. Get tour-level caddy advice -- transparent reasoning, personalized to your game. Includes a Python CLI, REST API, and native iOS voice app.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment (copy the template and fill in your keys)
cp .env.example .env
export OPENAI_API_KEY=sk-...

# 3. Run the CLI
python -m kindcaddy --profile profiles/example.json
```

> **Configuration:** all backend secrets/config are read from environment variables. See `.env.example` for the full list (`OPENAI_API_KEY`, `KINDCADDY_JWT_SECRET`, etc.). Never commit your `.env`.

## Usage (CLI)

Start a round, set weather, and ask about your shots:

```
/newround                              # Start a new round
/weather 72F wind 12mph SW             # Set conditions
/hole 7                                # Set current hole

162 out, pin back right, water right   # Ask for advice

/shot 7i 155 right                     # Log shot result
/score 4                               # Log score, advance to next hole
/summary                               # Get round analysis
```

## Commands

| Command | Description |
|---|---|
| `/profile` | View your golfer profile |
| `/weather <conditions>` | Set weather (e.g., `72F wind 12mph SW`) |
| `/altitude <feet>` | Set course altitude |
| `/newround` | Start a new round |
| `/hole <number>` | Set current hole |
| `/score <strokes>` | Log score for current hole |
| `/shot <club> <dist> [miss]` | Log a shot (e.g., `/shot 7i 150 right`) |
| `/scorecard` | View scorecard |
| `/summary` | Get round analysis |
| `/help` | Show all commands |
| `/quit` | Exit |

## Golfer Profile

Edit `profiles/example.json` with your actual club distances and tendencies. The caddy uses this data for every recommendation.

## Agent Tools

KindCaddy includes an agent framework that proactively monitors your round:

- **WeatherTool** -- Detects wind shifts and condition changes
- **ShotTrackerTool** -- Identifies patterns (hitting short, missing right)
- **ScoreCalculatorTool** -- Strategic advice based on scoring situation
- **FatigueModelTool** -- Adjusts distances for back-9 fatigue

## REST API

The API wraps the caddy engine so the iOS app (or any client) can get advice over HTTP.

```bash
# Start the API server (your Mac or a server)
OPENAI_API_KEY=sk-... uvicorn kindcaddy.api:app --host 0.0.0.0 --port 8000
```

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/session` | Create a caddy session (send golfer profile) |
| POST | `/advice` | Ask a shot question, get caddy advice |
| POST | `/command` | Run a command (newround, hole, weather, score, shot, etc.) |
| GET | `/session/{id}` | Get current round state (hole, conditions) |

### Example

```bash
# Create session
curl -X POST http://localhost:8000/session \
  -H "Content-Type: application/json" \
  -d '{"profile": ...}'   # returns {"session_id": "abc123"}

# Get advice
curl -X POST http://localhost:8000/advice \
  -H "Content-Type: application/json" \
  -d '{"session_id": "abc123", "text": "155 out, pin center, no wind"}'
```

Interactive docs at `http://localhost:8000/docs`.

## iOS App

Native SwiftUI app in `ios/KindCaddy/`. Uses on-device speech recognition (STT) and text-to-speech (TTS) -- no audio leaves the phone. Talks to the API backend for caddy advice via GPT-4o.

### Setup

1. Copy the secrets template: `cp ios/KindCaddy/Secrets.xcconfig.example ios/KindCaddy/Secrets.xcconfig` and fill in your backend URL and API key. `Secrets.xcconfig` is gitignored.
2. Open `ios/KindCaddy/KindCaddy.xcodeproj` in Xcode.
3. Select your development team in Signing & Capabilities.
4. Build and run on a device or simulator (iOS 17+).
5. Fill in your profile and tap "Start Round".
6. Hold the mic button, speak your question, release to send.

> **On-device LLM (optional):** `LocalLLMService.swift` can run a local model via [llama.cpp](https://github.com/ggerganov/llama.cpp). The llama.cpp source is **not vendored** in this repo — clone it into `ios/vendor/llama.cpp` (gitignored) if you want to build that feature. The default app talks to the GPT-4o backend over HTTP and does not require it.

### Features

- **Setup screen**: Golfer profile, physical attributes, tendencies, backend URL.
- **Voice round**: Push-to-talk mic button, live transcript, caddy response with TTS playback.
- **Voice commands**: "new round", "hole 7", "weather 72 wind 10 southwest", "score 4", "shot 7i 155 right", "scorecard", "summary".

## Voice APP

Use your voice from the terminal. Uses OpenAI Whisper for STT and OpenAI TTS for speech.

```bash
OPENAI_API_KEY=sk-... python -m kindcaddy.voice_app --profile profiles/example.json
```

## Benchmarking

Run pre-built golf scenarios to evaluate model quality:

```bash
OPENAI_API_KEY=sk-... python scenarios/benchmark.py --model gpt-4o
```

## Architecture

```
iOS App (SwiftUI)          Python Backend (FastAPI)
 ├─ Setup screen            ├─ POST /session
 ├─ Voice round UI          ├─ POST /advice  ──→ Caddy Engine ──→ GPT-4o
 ├─ On-device STT           ├─ POST /command
 └─ On-device TTS           └─ GET  /session/{id}
```

The caddy engine (`kindcaddy/caddy.py`) builds context from the golfer profile, weather, round state, and agent tools, then sends it to GPT-4o and returns the response.

## Deployment

See [`deploy/`](deploy/) for EC2 + Caddy (HTTPS) runbooks. Hostnames, paths, and IPs in those docs are placeholders (`api.yourdomain.com`, `EC2_PUBLIC_IP`, `YOUR_KEY.pem`) — substitute your own.

## Security

- No secrets are committed to this repo. Backend config comes from environment variables (`.env.example`) and the iOS app reads from `Secrets.xcconfig` (gitignored).
- If you fork/deploy this, generate your own `KINDCADDY_JWT_SECRET` and never reuse example values.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Jim Wu.
