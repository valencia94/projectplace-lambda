#
# Dockerfile — ProjectPlaceDataExtractor (immutable-tag friendly)
#

############################
# Stage 1 – build packages #
############################
FROM public.ecr.aws/lambda/python:3.11 AS build

# ---- system deps (LibreOffice + fonts) ----
RUN yum -y install libreoffice && \
    yum clean all

# ---- Python dependencies ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t /opt/python

##############################
# Stage 2 – final run image  #
##############################
FROM public.ecr.aws/lambda/python:3.11

# copy Python libs + LibreOffice bits from builder
COPY --from=build /opt/python          /opt/python
COPY --from=build /usr/lib64/libreoffice /usr/lib64/libreoffice
ENV PATH="/usr/lib64/libreoffice/program:${PATH}"

# app code
WORKDIR ${LAMBDA_TASK_ROOT}
COPY lambda_handler.py .
COPY logo/company_logo.png ./logo/company_logo.png

# Lambda entrypoint (AWS base image will exec this handler)
CMD ["lambda_handler.lambda_handler"]
