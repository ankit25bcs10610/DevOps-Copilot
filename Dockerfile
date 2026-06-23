# --- DevOps Copilot image ---
FROM python:3.12-slim

# git is needed by the repo MCP server's git_log tool.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for better layer caching.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir -e .

# Sample fixtures so the container is runnable out of the box.
COPY sample_repo ./sample_repo

EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
