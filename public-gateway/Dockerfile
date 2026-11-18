FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
WORKDIR /app
COPY requirements.txt .
# cffi has no armv7l wheel: it is compiled on arm/v7 and the toolchain is removed afterwards
RUN set -e; \
    build_deps="gcc libc6-dev libffi-dev"; \
    arch="$(dpkg --print-architecture)"; \
    if [ "$arch" = armhf ]; then \
        apt-get update; \
        apt-get install -y --no-install-recommends $build_deps; \
    fi; \
    pip install --no-cache-dir -r requirements.txt; \
    if [ "$arch" = armhf ]; then \
        apt-mark manual libffi8; \
        apt-get purge -y --auto-remove $build_deps; \
        rm -rf /var/lib/apt/lists/*; \
    fi; \
    python -c "import bcrypt, cryptography.fernet as f; f.Fernet(f.Fernet.generate_key())"
COPY . .
CMD ["python", "main.py"]
