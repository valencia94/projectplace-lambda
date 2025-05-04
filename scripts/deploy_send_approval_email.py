#!/usr/bin/env python3
import os, zipfile, boto3, sys

def require_env(key, optional=False):
    val = os.environ.get(key)
    if not val and not optional:
        print(f"❌ Missing env var: {key}")
        sys.exit(1)
    return val

AWS_REGION = require_env("AWS_REGION")
ACCOUNT_ID = require_env("AWS_ACCOUNT_ID")
TABLE_NAME = require_env("DYNAMODB_TABLE_NAME")
EMAIL_SRC  = require_env("EMAIL_SOURCE")
ACTA_API_ID = os.environ.get("ACTA_API_ID")  # Optional
API_STAGE   = os.environ.get("API_STAGE", "prod")
ROLE_ARN    = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"

ZIP_DIR     = "./deployment_zips"
SRC_FILE    = "approval/sendApprovalEmail.py"
FUNCTION    = "sendApprovalEmail"
HANDLER     = "sendApprovalEmail.lambda_handler"

def make_zip():
    os.makedirs(ZIP_DIR, exist_ok=True)
    zp = f"{ZIP_DIR}/{FUNCTION}.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.write(SRC_FILE, arcname=os.path.basename(SRC_FILE))
    return zp

def deploy(zip_path):
    client = boto3.client("lambda", region_name=AWS_REGION)
    with open(zip_path, "rb") as f:
        code = f.read()

    env_vars = {
        "AWS_REGION": AWS_REGION,
        "DYNAMODB_TABLE_NAME": TABLE_NAME,
        "EMAIL_SOURCE": EMAIL_SRC,
        "API_STAGE": API_STAGE
    }
    if ACTA_API_ID:
        env_vars["ACTA_API_ID"] = ACTA_API_ID

    try:
        client.get_function(FunctionName=FUNCTION)
        print("🔁 Updating Lambda code & env...")
        client.update_function_code(FunctionName=FUNCTION, ZipFile=code)
        client.update_function_configuration(
            FunctionName=FUNCTION,
            Environment={"Variables": env_vars}
        )
    except client.exceptions.ResourceNotFoundException:
        print("🚀 Creating Lambda...")
        client.create_function(
            FunctionName=FUNCTION,
            Runtime="python3.9",
            Role=ROLE_ARN,
            Handler=HANDLER,
            Code={"ZipFile": code},
            Timeout=60,
            MemorySize=256,
            Environment={"Variables": env_vars}
        )

if __name__ == "__main__":
    zp = make_zip()
    deploy(zp)
