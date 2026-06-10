# Prosper Health Voice Agent

A voice AI agent for a health clinic that schedules appointments over the phone. Patients can identify themselves, book new appointments, and cancel existing ones by speaking naturally. Built on [Pipecat](https://github.com/pipecat-ai/pipecat) with ElevenLabs for STT and TTS, OpenAI as the LLM, and a custom EHR HTTP API backed by SQLite.

See [SOLUTION.md](./SOLUTION.md) for architecture decisions and tradeoffs.

## Prerequisites

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager

## Setup

1. Clone the repository and install dependencies:

   ```bash
   git clone <repository-url>
   cd prosper-challenge
   uv sync
   ```

2. Create a `.env` file with your API keys:

   ```bash
   cp env.example .env
   ```

   ```ini
   ELEVENLABS_API_KEY=your_elevenlabs_api_key
   OPENAI_API_KEY=your_openai_api_key
   ```

## Running

Both services must be running simultaneously. Start the EHR first.

**Terminal 1 — EHR service** (http://localhost:8000, Swagger at `/docs`):

```bash
uv run uvicorn ehr.main:app --port 8000 --reload
```

**Terminal 2 — Voice bot** (http://localhost:7860):

```bash
uv run bot.py
```

Open http://localhost:7860 in your browser and click **Connect** to start a call.

> First run may take ~20 seconds while Pipecat downloads the turn detection and VAD models.

## Evaluation harness

The `eval/` directory contains an LLM-to-LLM conversation simulator that tests agent behaviour without any audio or browser. Two OpenAI calls alternate turns in a plain Python loop, the agent calls the live EHR, and an LLM judge scores the result against a rubric. DB assertions verify the final state.

```bash
# EHR must be running (see above)
uv run -m eval
```

Results are appended to `eval/report.json`.
