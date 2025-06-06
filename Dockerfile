# ---------------
# Dockerfile
# ---------------

FROM public.ecr.aws/lambda/python:3.10

# (A) copy source
COPY lambda_handler.py ./
COPY requirements.txt ./

# 👉 (B) copy logo assets  ❗ NEW
COPY logo/ ./logo/

# (C) install deps
RUN pip3 install --no-cache-dir -r requirements.txt

# (optional) LibreOffice, etc.
# RUN yum -y install libreoffice-headless

# (D) Lambda start-up
ENTRYPOINT ["/lambda-entrypoint.sh"]
CMD ["lambda_handler.lambda_handler"]
