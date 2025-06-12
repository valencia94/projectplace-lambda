FROM public.ecr.aws/lambda/python:3.10

# ── system packages ────────────────────────────────────────────────────────
# 1) add EPEL repo
RUN yum -y update && \
    yum -y install epel-release && \       # this works in the Lambda image
    # 2) install LibreOffice (headless) + a basic font
RUN yum -y install libreoffice-headless dejavu-sans-fonts && \
    yum clean all && rm -rf /var/cache/yum

# ── python deps ────────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip3 install --no-cache-dir --upgrade pip \
 && pip3 install --no-cache-dir -r requirements.txt

# ── your code & assets ─────────────────────────────────────────────────────
COPY lambda_handler.py ./
COPY logo/ ./logo/

# ── Lambda bootstrap remains unchanged ─────────────────────────────────────
CMD ["lambda_handler.lambda_handler"]
