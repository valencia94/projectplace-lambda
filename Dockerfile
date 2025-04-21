FROM public.ecr.aws/lambda/python:3.10

# 1) Install curl so we can fetch EPEL RPM
RUN yum -y install curl

# 2) Download the EPEL 7 release RPM 
#    (Amazon Linux 2 is derived from RHEL 7, so we use epel-release-latest-7)
RUN curl -O https://dl.fedoraproject.org/pub/epel/epel-release-latest-7.noarch.rpm

# 3) Install the EPEL release RPM
RUN rpm -ivh epel-release-latest-7.noarch.rpm

# 4) Try installing LibreOffice (headless or full). 
#    If you get "No package libreoffice-headless available",
#    try 'libreoffice' or 'libreoffice-core libreoffice-writer' instead.
RUN yum -y install libreoffice-headless || yum -y install libreoffice || yum -y install libreoffice-core libreoffice-writer

# 5) Copy and install Python dependencies
COPY requirements.txt .
RUN pip3 install --upgrade pip && pip3 install -r requirements.txt

# 6) Copy your Lambda code + logo folder
COPY lambda_handler.py ./
COPY logo/ ./logo/

# 7) Lambda entry point
CMD [ "lambda_handler.lambda_handler" ]
