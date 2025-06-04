# ─────────────────────────────────────────────────────────────
#  ProjectPlace Lambda image  – stag / prod
#  (LibreOffice installed via amazon-linux-extras)
# ─────────────────────────────────────────────────────────────
FROM public.ecr.aws/lambda/python:3.10

# 1) LibreOffice – single-repo install
RUN amazon-linux-extras enable libreoffice && \
    yum -y install libreoffice-headless && \
    yum clean all

# 2) Python dependencies
COPY requirements.txt .
RUN pip3 install --upgrade pip && pip3 install -r requirements.txt

# 3) Production handler + assets
COPY lambda_handler.py .
COPY logo/ ./logo/

# 4) Tag-aware handler (optional switch at build time)
COPY scripts/lambda_handler_tag.py ./tag.py

# 5) Promote tag.py → lambda_handler.py if requested
ARG USE_TAG_HANDLER=false
RUN if [ "$USE_TAG_HANDLER" = "true" ]; then \
        mv ./tag.py ./lambda_handler.py ; \
    fi

# 6) Lambda entry-point
CMD ["lambda_handler.lambda_handler"]

