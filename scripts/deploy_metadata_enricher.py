#!/usr/bin/env python3

import os
import boto3
import zipfile
import sys
import time

RESERVED_KEYS = ["AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]

# --------- ENV VALIDATION ---------
def require_env(key):
    val = os.getenv(key)
    if not val:
        print(f"❌ Missing required environment variable: {key}")
        sys.exit(1)
    return val

def validate_env(vars_dict):
    for key in vars_dict:
        if key in RESERVED_KEYS:
            print(f"❌ ERROR: '{key}' is a reserved AWS key and cannot be used in Lambda env variables.")
            sys.exit(1)
    print("✅ Environment variable validation passed.")

def get_role_arn(role_name):
    iam = boto3.client("iam")
    try:
        role = iam.get_role(RoleName=role_name)
        print(f"✅ IAM role found: {role_name}")
        return role["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        print(f"❌ IAM role does not exist: {role_name}")
        sys.exit(1)

# --------- CONFIG ---------
REGION = require_env("AWS_REGION")
ACCOUNT_ID = require_env("AWS_ACCOUNT_ID")
ROLE_NAME = "ProjectplaceLambdaRole"
LAMBDA_ROLE = get_role_arn(ROLE_NAME)
ZIP_DIR = "./deployment_zips"
LAMBDA_NAME = "projectMetadataEnricher"
HANDLER_NAME = "project_metadata_enricher.lambda_handler"
SOURCE_FILE = "approval/project_metadata_enricher.py"

def create_zip():
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = os.path.join(ZIP_DIR, f"{LAMBDA_NAME}.zip")
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        zipf.write(SOURCE_FILE, arcname=os.path.basename(SOURCE_FILE))
    print(f"📦 Created zip at: {zip_path}")
    return zip_path

def deploy_lambda(zip_path):
    client = boto3.client("lambda", region_name=REGION)
    with open(zip_path, 'rb') as f:
        zipped_code = f.read()

    env_vars = {
        "DYNAMODB_TABLE_NAME": require_env("DYNAMODB_TABLE_NAME"),
        "PROJECTPLACE_SECRET_NAME": require_env("PROJECTPLACE_SECRET_NAME")
    }
    validate_env(env_vars)

    try:
        client.get_function(FunctionName=LAMBDA_NAME)
        print(f"🔁 Updating existing Lambda: {LAMBDA_NAME}")
        client.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=zipped_code)

        # ✅ Wait for code update to finish before applying configuration
        print("⏳ Waiting for Lambda code update to complete...")
        client.get_waiter("function_updated").wait(FunctionName=LAMBDA_NAME)
        print("✅ Lambda code update confirmed.")

        client.update_function_configuration(FunctionName=LAMBDA_NAME, Environment={"Variables": env_vars})
    except client.exceptions.ResourceNotFoundException:
        print(f"🆕 Creating new Lambda: {LAMBDA_NAME}")
        print("🕒 Waiting 15 seconds for IAM trust policy propagation...")
        time.sleep(15)
        client.create_function(
            FunctionName=LAMBDA_NAME,
            Runtime="python3.9",
            Role=LAMBDA_ROLE,
            Handler=HANDLER_NAME,
            Code={"ZipFile": zipped_code},
            Timeout=60,
            MemorySize=256,
            Environment={"Variables": env_vars}
        )

if __name__ == "__main__":
    print("🚀 Starting deployment for projectMetadataEnricher...")
    zip_path = create_zip()
    deploy_lambda(zip_path)
    print("✅ Lambda deployed successfully.")
