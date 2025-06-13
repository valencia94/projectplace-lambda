###############################################################################
#  Dockerfile – ProjectPlace Lambda                                           #
#  - Multi-stage build                                                        #
#    • Stage 1  = build image → compile ALL wheels (incl. transitive deps)    #
#    • Stage 2  = runtime   → lean image with offline wheel install + code    #
###############################################################################

# ─────────────────────────────────────────────────────────────────────────────
#  Stage 1 – build base: compile every dependency wheel (incl. libres)        #
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.10-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /tmp/build

# LibreOffice & fonts for headless DOCX→PDF conversion
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libreoffice-core libreoffice-writer fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 1. dependency wheels (NO --no-deps so transitive deps are wheeled too)
# ---------------------------------------------------------------------------
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip wheel --no-cache-dir -r requirements.txt -w /tmp/wheels

# ─────────────────────────────────────────────────────────────────────────────
#  Stage 2 – runtime image: slim, offline-installed wheels + application code #
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ---- copy LibreOffice runtime bits (binary + resources + fonts) ------------
COPY --from=build /usr/bin/soffice       /usr/bin/
COPY --from=build /usr/lib/libreoffice   /usr/lib/libreoffice
COPY --from=build /usr/share/libreoffice /usr/share/libreoffice
COPY --from=build /usr/share/fonts       /usr/share/fonts

# ---- copy pre-built wheels & install completely offline --------------------
COPY --from=build /tmp/wheels /tmp/wheels
RUN pip install --no-index --find-links=/tmp/wheels /tmp/wheels/*.whl && \
    rm -rf /tmp/wheels

# ---- application code ------------------------------------------------------
WORKDIR /app
COPY . .

# ---- default entry-point ---------------------------------------------------
CMD ["python", "lambda_handler.py"]
