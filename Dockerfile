##############################################################################
# ProjectPlaceDataExtractor – AWS Lambda container image (Python 3.11)
# * Stage 1 installs all Python wheels to /opt/python (Lambda default path)
# * Stage 2 copies code + wheels into the official Lambda runtime base
##############################################################################

# ---------- Stage 1: build dependencies -------------------------------------
FROM public.ecr.aws/lambda/python:3.11 AS build

# Copy and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t /opt/python

# ---------- Stage 2: runtime image ------------------------------------------
FROM public.ecr.aws/lambda/python:3.11

# Copy dependencies from build stage
COPY --from=build /opt/python /opt/python

# Copy function code (add more COPY lines if you have extra modules)
COPY lambda_handler.py ${LAMBDA_TASK_ROOT}/

# (Optional) LibreOffice CLI for DOCX→PDF – **huge**, install only if required
# RUN yum install -y libreoffice && yum clean all

# Entrypoint
CMD ["lambda_handler.lambda_handler"]
