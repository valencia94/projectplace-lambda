#
# Dockerfile — ProjectPlaceDataExtractor (LibreOffice via Debian stage)
#

###########################
# Stage 1 – LibreOffice   #
###########################
FROM debian:bookworm-slim AS libre
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libreoffice-core libreoffice-writer libreoffice-base-core \
        fonts-dejavu-core && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

###########################
# Stage 2 – Python wheels #
###########################
FROM public.ecr.aws/lambda/python:3.11 AS build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t /opt/python

###########################
# Stage 3 – Final image   #
###########################
FROM public.ecr.aws/lambda/python:3.11

# Python deps
COPY --from=build  /opt/python               /opt/python
# LibreOffice runtime (program + shared libs + fonts)
COPY --from=libre  /usr/lib/libreoffice      /usr/lib/libreoffice
COPY --from=libre  /usr/share/fonts          /usr/share/fonts
ENV PATH="/usr/lib/libreoffice/program:${PATH}"

# app code
WORKDIR ${LAMBDA_TASK_ROOT}
COPY lambda_handler.py .
COPY logo/company_logo.png ./logo/company_logo.png

CMD ["lambda_handler.lambda_handler"]
