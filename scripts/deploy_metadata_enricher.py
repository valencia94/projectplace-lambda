#!/usr/bin/env python3

import os
import boto3
import zipfile
import json
import sys

# --------- ENV VALIDATION ---------
def require_env(key):
    val = os.getenv(key)
    if not val:
        print(f"❌ Missing required environment variable: {key}")
        sys.exit(1)
    return val

REGION = require_env("AWS_REGION")
ACCOUNT_ID = require_env("AWS_ACCOUNT_ID")
LAMBDA_ROLE = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectPlaceLambdaRole"
TABLE_NAME = require_env("DYNAMODB_TABLE_NAME")
SECRET_NAME = require_env("PROJECTPLACE_SECRET_NAME")
ZIP_DIR = "./deployment_zips"

LAMBDA_NAME = "projectMetadataEnricher"
HANDLER_NAME = "project_metadata_enricher.lambda_handler"
SOURCE_FILE = "approval/project_metadata_enricher.py"

# --------- ZIP PACKAGE CREATION ---------
def create_zip():
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = os.path.join(ZIP_DIR, f"{LAMBDA_NAME}.zip")
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        zipf.write(SOURCE_FILE, arcname=os.path.basename(SOURCE_FILE))
    print(f"📦 Created zip at: {zip_path}")
    return zip_path

# --------- LAMBDA DEPLOYMENT ---------
def deploy_lambda(zip_path):
    client = boto3.client("lambda", region_name=REGION)
    with open(zip_path, 'rb') as f:
        zipped_code = f.read()

    try:
        client.get_function(FunctionName=LAMBDA_NAME)
        print(f"🔁 Updating existing Lambda: {LAMBDA_NAME}")
        client.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=zipped_code)
        client.update_function_configuration(
            FunctionName=LAMBDA_NAME,
            Environment={
                "Variables": {
                    "DYNAMODB_TABLE_NAME": TABLE_NAME,
                    "PROJECTPLACE_SECRET_NAME": SECRET_NAME
                }
            }
    except client.exceptions.ResourceNotFoundException:
        print(f"🆕 Creating new Lambda: {LAMBDA_NAME}")
        client.create_function(
            FunctionName=LAMBDA_NAME,
            Runtime="python3.9",
            Role=LAMBDA_ROLE,
            Handler=HANDLER_NAME,
            Code={"ZipFile": zipped_code},
            Timeout=60,
            MemorySize=256,
            Environment={
                "Variables": {
                    "AWS_REGION": REGION,
                    "DYNAMODB_TABLE_NAME": TABLE_NAME,
                    "PROJECTPLACE_SECRET_NAME": SECRET_NAME
                }
            }
        )

# --------- MAIN RUN ---------
if __name__ == "__main__":
    print("🚀 Starting deployment for projectMetadataEnricher...")
    zip_path = create_zip()
    deploy_lambda(zip_path)
    print("✅ Lambda deployed successfully.")
