FROM public.ecr.aws/lambda/python:3.10

# 1) Install LibreOffice (headless)
RUN yum install -y libreoffice

# 2) Copy and install dependencies
COPY requirements.txt .
RUN pip3 install --upgrade pip && pip3 install -r requirements.txt

# 3) Copy the main Python code (lambda_handler.py)
COPY lambda_handler.py ./

# 4) Copy the 'logo' folder containing your company_logo.png
COPY logo/ ./logo/

# 5) Lambda entry point
CMD [ "lambda_handler.lambda_handler" ]
