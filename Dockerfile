FROM python:3.13-slim
WORKDIR /app

# Install sqlite3 for on-the-fly migrations
RUN apt-get update && apt-get install -y sqlite3 && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r pantograph && useradd --create-home -r -g pantograph -u 1001 pantograph
ENV HOME=/home/pantograph
ENV FLASK_ENV=production

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy dependency files first for better cache layering
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev --no-cache

# Copy application code
COPY app/ app/
COPY scripts/ scripts/
COPY wsgi.py ./

# Create directories for uploads and data with proper permissions
RUN mkdir -p uploads data && \
    chown -R pantograph:pantograph /app /home/pantograph && \
    chmod -R 755 /app

# Switch to non-root user
USER pantograph

# Use non-root port and run as non-root user
EXPOSE 5001
CMD ["uv", "run", "gunicorn", "-b", "0.0.0.0:5001", "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]