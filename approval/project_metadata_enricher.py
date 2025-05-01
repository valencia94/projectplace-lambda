#!/usr/bin/env python3

import os
import boto3
import zipfile
import json

REGION = os.environ.get("AWS_REGION")
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID")
LAMBDA_ROLE = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectPlaceLambdaRole"
TABLE_NAME = "ProjectPlace_DataExtrator_landing_table_v3"
SECRET_NAME = "ProjectPlaceAPICredentials"
ZIP_DIR = "./deployment_zips"

LAMBDA_NAME = "projectMetadataEnricher"
HANDLER_NAME = "project_metadata_enricher.lambda_handler"
SOURCE_FILE = "approval/project_metadata_enricher.py"


def create_zip():
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = os.path.join(ZIP_DIR, f"{LAMBDA_NAME}.zip")
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        zipf.write(SOURCE_FILE, arcname=os.path.basename(SOURCE_FILE))
    return zip_path


def deploy_lambda(zip_path):
    client = boto3.client("lambda", region_name=REGION)
    with open(zip_path, 'rb') as f:
        zipped_code = f.read()

    try:
        client.get_function(FunctionName=LAMBDA_NAME)
        print(f"Updating existing Lambda: {LAMBDA_NAME}")
        client.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=zipped_code)
        client.update_function_configuration(
            FunctionName=LAMBDA_NAME,
            Environment={
                "Variables": {
                    "AWS_REGION": REGION,
                    "DYNAMODB_TABLE_NAME": TABLE_NAME,
                    "PROJECTPLACE_SECRET_NAME": SECRET_NAME
                }
            }
        )
    except client.exceptions.ResourceNotFoundException:
        print(f"Creating new Lambda: {LAMBDA_NAME}")
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


if __name__ == "__main__":
    print("📦 Creating deployment ZIP...")
    zip_path = create_zip()
    print("🚀 Deploying Lambda...")
    deploy_lambda(zip_path)
    print("✅ Deployment complete.")
