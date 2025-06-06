# Use AWS’s official Lambda Python base image
FROM public.ecr.aws/lambda/python:3.10

# Copy your function code
COPY lambda_handler.py ./
COPY requirements.txt ./

# Install your dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

# (Optionally) install other packages or libs, e.g., LibreOffice

# The last lines set the container’s startup instructions:
ENTRYPOINT [ "/lambda-entrypoint.sh" ]
CMD [ "lambda_handler.lambda_handler" ]
