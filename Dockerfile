FROM amazonlinux:2

RUN yum -y update && \
    yum -y install python3-pip libreoffice-headless && \
    yum clean all && rm -rf /var/cache/yum

COPY requirements.txt .
RUN pip3 install --upgrade pip && pip3 install -r requirements.txt

COPY lambda_handler.py ./

COPY logo/ ./logo/

CMD [ "lambda_handler.lambda_handler" ]
