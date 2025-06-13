# --------------------------------------------------------------------------
# Stage 0 – build layer (grab LibreOffice & Python wheels only once)
# --------------------------------------------------------------------------
FROM python:3.10-slim-bookworm AS build

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /tmp/build

# — system packages (≈ 420 MB → we’ll COPY the bits we need later)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libreoffice-core libreoffice-writer fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

# — python wheels
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip wheel --no-cache-dir --no-deps -r requirements.txt -w /tmp/wheels

# --------------------------------------------------------------------------
# Stage 1 – runtime image (≈ 680 MB → fits comfortably in Lambda)
# --------------------------------------------------------------------------
FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# —— LibreOffice binaries & fonts (copy, don’t reinstall)
COPY --from=build /usr/bin/soffice            /usr/bin/
COPY --from=build /usr/lib/libreoffice        /usr/lib/libreoffice
COPY --from=build /usr/share/libreoffice      /usr/share/libreoffice
COPY --from=build /usr/share/fonts            /usr/share/fonts

# —— Python deps
COPY --from=build /tmp/wheels /tmp/wheels
RUN pip install --no-index --find-links=/tmp/wheels /tmp/wheels/*.whl && \
    rm -rf /tmp/wheels

# —— app code
WORKDIR /app
COPY . .

# —— entry-point
CMD ["python","-u","lambda_handler.py"]
