FROM public.ecr.aws/lambda/python:3.10

# 1) Enable EPEL and clean metadata
RUN amazon-linux-extras enable epel && \
    yum clean metadata

# 2) Install epel-release and attempt installing LibreOffice headless
RUN yum install -y epel-release && \
    yum install -y libreoffice-headless

# 3) Copy and install dependencies
COPY requirements.txt .
RUN pip3 install --upgrade pip && pip3 install -r requirements.txt

# 4) Copy your Lambda code
COPY lambda_handler.py ./
COPY logo/ ./logo/

# 5) Lambda entry point
CMD [ "lambda_handler.lambda_handler" ]
