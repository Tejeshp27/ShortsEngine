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

# Build the already-used whisper.cpp CLI for CPU transcription on Linux.
# A separate build stage keeps compilers and source out of the runtime image.
FROM python:3.11-slim AS whisper-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    git cmake g++ ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Pin the official release; GGML_NATIVE=OFF makes the binary portable to
# another CPU of the same architecture instead of the build machine only.
RUN git clone --depth 1 --branch v1.9.5 https://github.com/ggml-org/whisper.cpp.git /tmp/whisper \
    && cmake -S /tmp/whisper -B /tmp/whisper/build \
       -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF \
       -DGGML_OPENMP=OFF -DWHISPER_BUILD_TESTS=OFF -DWHISPER_CURL=OFF \
    && cmake --build /tmp/whisper/build --config Release --target whisper-cli -j 2

# Start from the official slim Python 3.11 image.
# "slim" means no extra OS packages — keeps the image small.
FROM python:3.11-slim

# Set the working directory inside the container.
WORKDIR /app

# Install FFmpeg (needed for video rendering) from the system package manager.
# We do this before copying Python files so Docker can cache this layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Speech recognition runs locally; the model is downloaded into a volume once.
COPY --from=whisper-builder /tmp/whisper/build/bin/whisper-cli /usr/local/bin/whisper-cli

# Copy only requirements.txt first. Docker caches this layer, so
# the pip install step only reruns when requirements change — not on every code edit.
COPY requirements.txt .

# Install Python dependencies.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project into the container.
# .dockerignore keeps secrets (.env), the database and videos out.
COPY . .

# Create the folders the app writes to at runtime.
# They are excluded from git but need to exist in the container.
# data/ holds the database (see DATABASE_PATH in docker-compose.yml).
RUN mkdir -p output cache data models recordings

# Tell Docker which port the dashboard listens on.
EXPOSE 8000

# Default command: start the web dashboard.
# Override this in docker-compose.yml to run the pipeline instead.
CMD ["python", "-m", "src.dashboard.app"]
