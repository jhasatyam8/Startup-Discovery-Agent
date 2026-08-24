# Startup Discovery Agent

An autonomous AI-driven startup discovery system that monitors YouTube and other funding/news sources, extracts startup funding events, verifies them, stores them in a database, and exposes them through a dashboard and Telegram bot.

This project is designed for discovering recently funded Indian startups and surfacing useful lead information such as founders, HR, and hiring contacts.

## Overview

The system combines multiple data sources and workflows:

- YouTube search and transcript analysis
- Funding keyword detection and startup extraction via LLMs
- Web verification against public sources
- Inc42 funding news ingestion
- Shark Tank startup ingestion
- Storage in SQLite/PostgreSQL via SQLAlchemy
- Google Sheets sync for reporting and downstream processing
- FastAPI dashboard for browsing discovered startups
- Telegram bot for notifications and quick search
- LinkedIn lead discovery for startup contacts

## Key Features

- Automated discovery of funding-related videos from YouTube
- Funding milestone extraction from video transcripts
- Optional OCR-based visual extraction from funding screens
- Duplicate prevention and confidence scoring
- Database-backed startup records with investors, funding round, industry, and HQ
- Daily scheduled pipeline execution
- API dashboard for summary metrics and startup browsing
- Telegram bot with role-based preference toggles and startup search
- Lead generation for founders and hiring teams using free web-based discovery strategies

## Tech Stack

- Python 3.10+
- FastAPI
- SQLAlchemy
- SQLite / PostgreSQL
- Google GenAI / OpenAI
- yt-dlp and YouTube transcript APIs
- DuckDuckGo / web-verification techniques
- Google Sheets API
- APScheduler
- Docker and Docker Compose

## Repository Structure

```text
.
├── dashboard/
│   ├── app.py
│   └── static/
├── db/
│   ├── connection.py
│   ├── models.py
│   ├── sync_all_data.py
│   └── ...
├── services/
│   ├── inc42_scraper.py
│   ├── linkedin_finder.py
│   ├── llm_extractor.py
│   ├── reporter.py
│   ├── sheets.py
│   ├── transcript.py
│   ├── youtube.py
│   └── ...
├── .env.example
├── .env.template
├── docker-compose.yml
├── Dockerfile
├── main.py
├── pipeline.py
├── run_all.py
├── scheduler.py
├── telegram_bot.py
├── pyproject.toml
├── render.yaml
├── README.md
└── ...
```

## How It Works

1. The pipeline searches YouTube for funding-related content using keywords and/or configured channels.
2. Videos are checked for transcript availability and funding relevance.
3. Relevant transcript content is analyzed by an LLM to extract startup names, rounds, and funding details.
4. Results are verified against web sources and saved to the database.
5. Records are synced to Google Sheets and surfaced in the dashboard.
6. For high-confidence startups, lead discovery attempts to identify founders and recruiters.
7. The bot and dashboard allow users to inspect the results and trigger runs on demand.

## Environment Configuration

Copy the example file and configure your API keys and settings:

```bash
cp .env.example .env
```

Important configuration values include:

- `GEMINI_API_KEY` or `OPENAI_API_KEY`
- `DATABASE_URL`
- `GOOGLE_SHEETS_CREDENTIALS_JSON`
- `GOOGLE_SHEETS_SPREADSHEET_ID`
- `TELEGRAM_BOT_TOKEN`
- `PORT` and `HOST`
- `SEARCH_KEYWORDS`
- `ENABLE_LEAD_FINDER`
- `LEAD_MIN_CONFIDENCE`

A typical `.env` setup is shown in the example file and includes optional settings for:

- YouTube API key
- Daily run time
- Search lookback windows
- Slack/Discord webhook
- LinkedIn lead-finder tuning

## Installation

### Option 1: Local Python Environment

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# or
.venv\Scripts\activate      # Windows

pip install --upgrade pip
pip install -e .
```

### Option 2: Docker

```bash
docker-compose up --build
```

## Running the Project

### Start the dashboard and API

```bash
python main.py --start-dashboard
```

### Run the discovery pipeline manually

```bash
python main.py --run-pipeline
```

### Run Inc42 ingestion

```bash
python main.py --run-inc42
```

### Load Shark Tank data

```bash
python main.py --load-shark-tank
```

### Find LinkedIn leads for startups already in the database

```bash
python main.py --find-leads
```

### Find leads for a specific startup

```bash
python main.py --find-leads-for "Zepto"
```

### Run everything together

```bash
python run_all.py
```

This starts:

- the FastAPI dashboard
- the Telegram bot polling loop
- the scheduled pipeline job

## Scheduler

The scheduler runs the pipeline daily based on `DAILY_RUN_TIME`, defaulting to `02:00` in IST timezone.

The scheduler is implemented in [scheduler.py](scheduler.py) and is also launched by the dashboard startup hook and the full multi-process runner.

## Dashboard

The dashboard is built with FastAPI and serves the frontend in the `dashboard/static/` folder.

It exposes APIs such as:

- `/api/summary`
- `/api/startups`
- `/api/shark-tank`
- `/api/pipeline-status`
- `/api/run-pipeline`

This makes it easy to monitor startups, funding rounds, investor trends, and pipeline health.

## Telegram Bot

The Telegram bot supports:

- `/start` — welcome and role preferences
- `/help` — command list
- `/search <query>` — search startup data
- `/ask <question>` — ask AI questions about startups
- `/stop` — pause daily updates
- admin commands like lead refresh and sheets sync

The bot is implemented in [telegram_bot.py](telegram_bot.py). It stores user preferences in the database and can push curated research updates.

## Database Model

The project uses SQLAlchemy models for:

- `ProcessedVideo`
- `Startup`
- `SharkTankStartup`
- `LeadProfile`
- `User`
- `ResearchCache`

These live in [db/models.py](db/models.py). The default database is SQLite (`sqlite:///startups.db`) unless `DATABASE_URL` is changed.

## Deployment

A Docker deployment is supported via [docker-compose.yml](docker-compose.yml) and [Dockerfile](Dockerfile).

For Render or similar hosting, the project also includes [render.yaml](render.yaml).

## Notes

- The project is designed around Indian startup funding discovery, but the pipeline can be adapted for other markets by changing search keywords and filters.
- Lead generation uses free or semi-free search patterns and may require rate-limiting and careful tuning.
- If Google Sheets credentials or Telegram bot tokens are missing, some features will be skipped gracefully.

## License

This project does not currently include a formal license file in the repository snapshot. If you are distributing or deploying it publicly, you should confirm the intended licensing terms before release.

## Summary

This project is essentially an AI-powered startup intelligence engine that continuously discovers funding events from public video and web sources, validates them, stores them in a database, exposes them through a dashboard, and helps teams identify startup opportunities and relevant contacts.
