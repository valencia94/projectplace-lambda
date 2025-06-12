FROM public.ecr.aws/lambda/python:3.10

# ── system packages ─────────────────────────────────────────────────────────
RUN yum -y update && \
    amazon-linux-extras install epel -y && \
    yum -y install \
        libreoffice-headless \          # pulls libreoffice-core + writer
        dejavu-sans-fonts \
    && yum clean all && rm -rf /var/cache/yum

# ── python deps ─────────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip3 install --no-cache-dir --upgrade pip \
 && pip3 install --no-cache-dir -r requirements.txt

# ── your code & assets ──────────────────────────────────────────────────────
COPY lambda_handler.py ./
COPY logo/ ./logo/

# keep AWS’s default bootstrap
CMD ["lambda_handler.lambda_handler"]
