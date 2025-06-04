FROM public.ecr.aws/lambda/python:3.10

# 1) Install required tools for LibreOffice install and for Python dependencies
RUN yum -y install wget tar && \
    yum clean all

# 2) Download LibreOffice official RPM archive and extract
ENV LIBREOFFICE_VERSION=7.6.7.2
RUN wget https://download.documentfoundation.org/libreoffice/stable/${LIBREOFFICE_VERSION}/rpm/x86_64/LibreOffice_${LIBREOFFICE_VERSION}_Linux_x86-64_rpm.tar.gz && \
    tar -xvf LibreOffice_${LIBREOFFICE_VERSION}_Linux_x86-64_rpm.tar.gz && \
    rm LibreOffice_${LIBREOFFICE_VERSION}_Linux_x86-64_rpm.tar.gz

# 3) Install LibreOffice RPMs
RUN yum -y install ./LibreOffice_*/RPMS/*.rpm && \
    yum clean all && \
    rm -rf ./LibreOffice_*

# 4) Copy and install Python dependencies
COPY requirements.txt .
RUN pip3 install --upgrade pip && pip3 install -r requirements.txt

# 5) Copy Lambda code and logo assets
COPY lambda_handler.py ./
COPY logo/ ./logo/

# 6) Lambda entry point
CMD [ "lambda_handler.lambda_handler" ]
