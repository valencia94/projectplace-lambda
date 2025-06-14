###############################################################################
# ---------- Stage 0 : build Python wheels  -----------------------------------
###############################################################################
FROM public.ecr.aws/lambda/python:3.11 AS build

COPY requirements.txt .
RUN python -m pip install --upgrade pip --no-cache-dir && \
    python -m pip install --no-cache-dir \
        --prefer-binary --only-binary=:all: --no-binary=python-docx \
        -r requirements.txt -t /opt/python

###############################################################################
# ---------- Stage 1 : LibreOffice binaries + fonts ---------------------------
###############################################################################
FROM debian:bookworm-slim AS libre
RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get install -y --no-install-recommends \
        libreoffice-core libreoffice-writer fonts-dejavu-core \
        libxinerama1 libxrandr2 libxext6 libxrender1 libsm6 libice6 libxt6 libx11-6 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

###############################################################################
# ---------- Stage 2 : Final Lambda image -------------------------------------
###############################################################################
FROM public.ecr.aws/lambda/python:3.11

# Python site-packages layer
COPY --from=build /opt/python /opt/python
COPY --from=libre /usr/lib/libreoffice /usr/lib/libreoffice
COPY --from=libre /usr/share/fonts      /usr/share/fonts
COPY --from=libre /usr/bin/soffice      /usr/bin/libreoffice
ENV PATH="/usr/lib/libreoffice/program:${PATH}"

RUN ln -sf /usr/lib/libreoffice/program/soffice /usr/bin/libreoffice
# ---------- your code --------------------------------------------------------
COPY lambda_handler.py ./
COPY logo/ ./logo/

# Lambda entrypoint
CMD ["lambda_handler.lambda_handler"]
