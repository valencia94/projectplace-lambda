FROM public.ecr.aws/lambda/python:3.10

# Install curl and update
RUN yum -y update && yum -y install curl

# Download and install latest EPEL 7 RPM; check file type for debugging
RUN curl -SL -o /tmp/epel.rpm https://dl.fedoraproject.org/pub/epel/epel-release-latest-7.noarch.rpm && \
    file /tmp/epel.rpm && \
    rpm -ivh /tmp/epel.rpm

# Install LibreOffice (headless) and clean up
RUN yum -y install libreoffice-headless || \
    yum -y install libreoffice || \
    yum -y install libreoffice-core libreoffice-writer || \
    echo "All LibreOffice installs failed. EPEL might not ship it."

RUN yum clean all && rm -rf /var/cache/yum

# 5) Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# 6) Copy your Lambda code & logo
COPY lambda_handler.py ./

COPY logo/ ./logo/

# 7) Lambda entry point
CMD [ "lambda_handler.lambda_handler" ]
