# ---------- base image -------------------------------------------------
FROM python:3.10-slim

# ---------- runtime flags ----------------------------------------------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ---------- working directory ------------------------------------------
WORKDIR /app

# ---------- system packages --------------------------------------------
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libreoffice-core libreoffice-writer fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# ---------- Python deps -------------------------------------------------
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---------- application code -------------------------------------------
COPY . .

# ---------- container entry-point --------------------------------------
CMD ["python", "lambda_handler.py"]
