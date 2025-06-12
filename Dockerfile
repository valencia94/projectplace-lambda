# ---------------------------------------------------------------------------
# Base image: official AWS Lambda Python 3.10
# ---------------------------------------------------------------------------
FROM public.ecr.aws/lambda/python:3.10

# ---------------------------------------------------------------------------
# System packages – LibreOffice (writer core) + basic fonts
# ---------------------------------------------------------------------------
RUN yum -y update && \
    yum -y install \
        libreoffice-core \
        libreoffice-writer \
        dejavu-sans-fonts  \
    && yum clean all && rm -rf /var/cache/yum

# ---------------------------------------------------------------------------
# Python deps
#  • awslambdaric = required entry point for Lambda container images
# ---------------------------------------------------------------------------
COPY requirements.txt .
RUN pip3 install --no-cache-dir --upgrade pip \
 && pip3 install --no-cache-dir awslambdaric \
 && pip3 install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Your code & assets
# ---------------------------------------------------------------------------
COPY lambda_handler.py ./
COPY logo/ ./logo/

# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------
ENTRYPOINT ["/usr/bin/python3", "-m", "awslambdaric"]
CMD ["lambda_handler.lambda_handler"]
