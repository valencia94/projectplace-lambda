###############################################################################
# ---------- Stage 0 : build Python wheels ------------------------------------
###############################################################################
FROM public.ecr.aws/lambda/python:3.11 AS build

COPY requirements.txt .

# --prefer-binary is belt-and-braces: forces wheels even if an sdist were newer
RUN python -m pip install --upgrade pip --no-cache-dir && \
    python -m pip install --no-cache-dir --prefer-binary \
        -r requirements.txt -t /opt/python


###############################################################################
# ---------- Stage 1 : LibreOffice binaries + fonts ---------------------------
###############################################################################
FROM debian:bookworm-slim AS libre
RUN set -e; \
    apt-get update -qq; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libreoffice-core libreoffice-writer fonts-dejavu-core; \
    rm -rf /var/lib/apt/lists/*

###############################################################################
# ---------- Stage 2 : Final Lambda image -------------------------------------
###############################################################################
FROM public.ecr.aws/lambda/python:3.11

# Python deps
COPY --from=build  /opt/python          /opt/python

# LibreOffice runtime
COPY --from=libre  /usr/lib/libreoffice /usr/lib/libreoffice
COPY --from=libre  /usr/share/fonts     /usr/share/fonts
ENV  PATH="/usr/lib/libreoffice/program:${PATH}"

# ---------- your code --------------------------------------------------------
COPY lambda_handler.py ${LAMBDA_TASK_ROOT}/
COPY logo/             /app/logo/

CMD ["lambda_handler.lambda_handler"]
