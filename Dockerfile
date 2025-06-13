###############################################################################
# ProjectPlaceDataExtractor – production container image for AWS Lambda
###############################################################################
# We stay on Python 3.10 so pandas 1.3.5 wheels resolve cleanly.
FROM python:3.10-slim-bookworm

# ---------- OS packages (LibreOffice for DOCX→PDF & basic fonts) ------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libreoffice-core libreoffice-writer fonts-dejavu-core \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# ---------- Python deps ------------------------------------------------------
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ---------- Function code ----------------------------------------------------
COPY lambda_handler.py           /app/
COPY logo/company_logo.png       /app/logo/company_logo.png

# ---------- Lambda entry-point ----------------------------------------------
# awslambdaric exposes the handler the same way the AWS base image would.
CMD [ "awslambdaric", "lambda_handler.lambda_handler" ]
