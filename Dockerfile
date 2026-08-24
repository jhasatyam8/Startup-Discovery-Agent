FROM python:3.11-slim

ENV TZ=Asia/Kolkata
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml .
RUN uv pip install --system --no-cache -r pyproject.toml

COPY . .

RUN mkdir -p reports

EXPOSE 8000

ENV PORT=8000
ENV HOST=0.0.0.0

CMD ["python", "run_all.py"]
