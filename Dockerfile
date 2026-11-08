# =============================================================
# Dockerfile — containerise ShortsEngine for cloud deployment
#
# Builds a slim Python image with all dependencies installed.
# The dashboard runs on port 8000. The pipeline is run as a
# one-off command (see docker-compose.yml for details).
#
# Build:   docker build -t shortsengine .
# Run:     docker run -p 8000:8000 --env-file .env shortsengine
# =============================================================

# Start from the official slim Python 3.11 image.
# "slim" means no extra OS packages — keeps the image small.
FROM python:3.11-slim

# Set the working directory inside the container.
WORKDIR /app

# Install FFmpeg (needed for video rendering) from the system package manager.
# We do this before copying Python files so Docker can cache this layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements.txt first. Docker caches this layer, so
# the pip install step only reruns when requirements change — not on every code edit.
COPY requirements.txt .

# Install Python dependencies.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project into the container.
COPY . .

# Create the folders the app writes to at runtime.
# They are excluded from git but need to exist in the container.
RUN mkdir -p output cache

# Tell Docker which port the dashboard listens on.
EXPOSE 8000

# Default command: start the web dashboard.
# Override this in docker-compose.yml to run the pipeline instead.
CMD ["python", "-m", "src.dashboard.app"]
