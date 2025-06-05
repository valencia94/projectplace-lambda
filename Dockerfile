FROM public.ecr.aws/lambda/python:3.10

# 1) Update and install curl
RUN yum -y update && yum -y install curl

# 2) Download and install the latest EPEL release for EL7
RUN curl -SL -o /tmp/epel.rpm https://dl.fedoraproject.org/pub/epel/epel-release-latest-7.noarch.rpm && \
    rpm -ivh /tmp/epel.rpm

# 3) Install LibreOffice headless (and fallback options just in case)
RUN yum -y install libreoffice-headless || \
    yum -y install libreoffice || \
    yum -y install libreoffice-core libreoffice-writer || \
    echo "All LibreOffice installs failed. EPEL might not ship it."

# 4) Clean up (recommended for smaller images)
RUN yum clean all && rm -rf /var/cache/yum

# 5) Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# 6) Copy your Lambda code & logo
COPY lambda_handler.py ./

COPY logo/ ./logo/

# 7) Lambda entry point
CMD [ "lambda_handler.lambda_handler" ]
