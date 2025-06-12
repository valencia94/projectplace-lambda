FROM public.ecr.aws/lambda/python:3.10

# ── system packages ────────────────────────────────────────────────────────
RUN yum -y update && yum -y install curl \
 && curl -SL -o /tmp/epel.rpm \
      https://dl.fedoraproject.org/pub/epel/7/Everything/x86_64/Packages/e/epel-release-7-14.noarch.rpm \
 && rpm -ivh /tmp/epel.rpm \
 && yum -y install libreoffice-headless dejavu-sans-fonts \
 && yum clean all && rm -rf /var/cache/yum /tmp/epel.rpm

# ── python deps ────────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip3 install --no-cache-dir --upgrade pip \
 && pip3 install --no-cache-dir -r requirements.txt

# ── code & assets ──────────────────────────────────────────────────────────
COPY lambda_handler.py ./
COPY logo/ ./logo/

CMD ["lambda_handler.lambda_handler"]
