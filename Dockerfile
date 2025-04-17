FROM public.ecr.aws/lambda/python:3.10

# 1) Copy + Install Dependencies
COPY requirements.txt .
RUN pip3 install --upgrade pip && pip3 install -r requirements.txt

# 2) Copy the main Python code (lambda_handler.py) 
COPY lambda_handler.py ./

# 3) Copy the 'logo' folder containing your company_logo.png
#    This ensures your doc creation can reference it in LOGO_IMAGE_PATH
COPY logo/ ./logo/

# 4) Lambda entry point (no placeholders)
CMD [ "lambda_handler.lambda_handler" ]
