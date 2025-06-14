###############################################################################
# ---------- Stage 0 : build Python wheels and Lambda dependencies ------------
###############################################################################
FROM public.ecr.aws/lambda/python:3.11 AS build

COPY requirements.txt .
RUN python -m pip install --upgrade pip --no-cache-dir && \
    python -m pip install --no-cache-dir \
        --prefer-binary --only-binary=:all: --no-binary=python-docx \
        -r requirements.txt -t /opt/python

###############################################################################
# ---------- Stage 1 : LibreOffice binaries + all X11 fonts/libs --------------
###############################################################################
FROM debian:bookworm-slim AS libre

RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get install -y --no-install-recommends \
        libreoffice-core libreoffice-writer fonts-dejavu-core \
        libxinerama1 libxrandr2 libxext6 libxrender1 \
        libsm6 libice6 libxt6 libx11-6 libglib2.0-0 \
        && rm -rf /var/lib/apt/lists/*

###############################################################################
# ---------- Stage 2 : Final AWS Lambda image ---------------------------------
###############################################################################
FROM public.ecr.aws/lambda/python:3.11

# ----------- Copy Python packages -----------------
COPY --from=build /opt/python /opt/python

# ----------- Copy LibreOffice runtime + X11 fonts/libs -------------
COPY --from=libre /usr/lib/libreoffice /usr/lib/libreoffice
COPY --from=libre /usr/share/fonts /usr/share/fonts
COPY --from=libre /usr/lib/x86_64-linux-gnu/libXinerama.so.1 /usr/lib/x86_64-linux-gnu/
COPY --from=libre /usr/lib/x86_64-linux-gnu/libXrandr.so.2 /usr/lib/x86_64-linux-gnu/
COPY --from=libre /usr/lib/x86_64-linux-gnu/libXext.so.6 /usr/lib/x86_64-linux-gnu/
COPY --from=libre /usr/lib/x86_64-linux-gnu/libXrender.so.1 /usr/lib/x86_64-linux-gnu/
COPY --from=libre /usr/lib/x86_64-linux-gnu/libSM.so.6 /usr/lib/x86_64-linux-gnu/
COPY --from=libre /usr/lib/x86_64-linux-gnu/libICE.so.6 /usr/lib/x86_64-linux-gnu/
COPY --from=libre /usr/lib/x86_64-linux-gnu/libXt.so.6 /usr/lib/x86_64-linux-gnu/
COPY --from=libre /usr/lib/x86_64-linux-gnu/libX11.so.6 /usr/lib/x86_64-linux-gnu/
COPY --from=libre /usr/lib/x86_64-linux-gnu/libglib-2.0.so.0 /usr/lib/x86_64-linux-gnu/

# ----------- Make sure the libreoffice command works as expected -------------
ENV PATH="/usr/lib/libreoffice/program:${PATH}"

# ----------- Symlink for libreoffice CLI (for subprocess call in Lambda)
RUN ln -sf /usr/lib/libreoffice/program/soffice /usr/bin/libreoffice

# ----------- Copy your Lambda handler and logo folder ----------
COPY lambda_handler.py ./
COPY logo/ ./logo/

# ----------- Set Lambda entrypoint -------------
CMD ["lambda_handler.lambda_handler"]
