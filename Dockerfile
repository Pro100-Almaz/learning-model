FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install system dependencies
# libssl-dev + pkg-config are needed to build cryptography from source on aarch64
# (prebuilt wheels use CPU instructions some Docker Desktop VMs don't expose -> SIGILL)
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        curl \
        build-essential \
        libpq-dev \
        libssl-dev \
        pkg-config \
        git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Rust (needed to build cryptography from source - see below)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
ENV PATH="/root/.cargo/bin:${PATH}"

# Copy ONLY dependency files first (better caching - changes infrequently)
COPY pyproject.toml uv.lock ./

# Export and install dependencies from lock file (cached layer when deps don't change).
# `cryptography` is forced to build from source: its prebuilt aarch64 wheel uses
# ARMv8.x CPU instructions that some Docker Desktop VMs don't expose, producing
# SIGILL ("Illegal instruction") at runtime during google/jwt auth init.
RUN uv export --no-hashes --all-extras --no-emit-project > requirements.txt && \
    uv pip install --system --no-cache --no-binary cryptography -r requirements.txt

# Copy application code (changes frequently - separate layer)
COPY . .

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000
