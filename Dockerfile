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
        libreoffice-core libreoffice-writer fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

###############################################################################
# ---------- Stage 2 : Final Lambda image -------------------------------------
###############################################################################
FROM public.ecr.aws/lambda/python:3.11

# Python site-packages layer
COPY --from=build /opt/python /opt/python

# LibreOffice runtime & fonts
COPY --from=libre /usr/lib/libreoffice /usr/lib/libreoffice
COPY --from=libre /usr/share/fonts      /usr/share/fonts
ENV PATH="/usr/lib/libreoffice/program:${PATH}"

# ---------- your code --------------------------------------------------------
OPY lambda_handler.py ${LAMBDA_TASK_ROOT}/Add commentMore actions
COPY logo/             /app/logo/

# Lambda entrypoint
CMD ["lambda_handler.lambda_handler"]
