#!/usr/bin/env python3
import os, zipfile, boto3, sys

def require_env(key):
    val = os.getenv(key)
    if not val:
        print(f"❌ Missing env var: {key}")
        sys.exit(1)
    return val

# ─── ENVIRONMENT ─────────────────────────────────────────────
REGION       = require_env("AWS_REGION")
ACCOUNT_ID   = require_env("AWS_ACCOUNT_ID")
TABLE_NAME   = require_env("DYNAMODB_TABLE_NAME")
SECRET_NAME  = require_env("PROJECTPLACE_SECRET_NAME")

FUNCTION     = "projectMetadataEnricher"
HANDLER      = "project_metadata_enricher.lambda_handler"
ROLE_ARN     = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"
ZIP_DIR      = "./deployment_zips"
SRC_FILE     = "approval/project_metadata_enricher.py"

# ─── PACKAGE SOURCE ──────────────────────────────────────────
def make_zip():
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = f"{ZIP_DIR}/{FUNCTION}.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(SRC_FILE, arcname=os.path.basename(SRC_FILE))
    print(f"📦 Created zip package: {zip_path}")
    return zip_path

# ─── DEPLOY LOGIC ────────────────────────────────────────────
def deploy(zip_path):
    lambda_client = boto3.client("lambda", region_name=REGION)

    with open(zip_path, "rb") as f:
        zipped_code = f.read()

    env_vars = {
        "DYNAMODB_TABLE_NAME": TABLE_NAME,
        "PROJECTPLACE_SECRET_NAME": SECRET_NAME
    }

    try:
        lambda_client.get_function(FunctionName=FUNCTION)
        print("🔁 Updating existing Lambda...")
        lambda_client.update_function_code(FunctionName=FUNCTION, ZipFile=zipped_code)
        lambda_client.update_function_configuration(
            FunctionName=FUNCTION,
            Environment={"Variables": env_vars}
        )
    except lambda_client.exceptions.ResourceNotFoundException:
        print("🚀 Creating new Lambda function...")
        lambda_client.create_function(
            FunctionName=FUNCTION,
            Runtime="python3.9",
            Role=ROLE_ARN,
            Handler=HANDLER,
            Code={"ZipFile": zipped_code},
            Timeout=120,
            MemorySize=256,
            Publish=True,
            Environment={"Variables": env_vars}
        )

    response = lambda_client.get_function(FunctionName=FUNCTION)
    arn = response["Configuration"]["FunctionArn"]
    print(f"✅ Lambda deployed successfully → {arn}")

# ─── ENTRYPOINT ──────────────────────────────────────────────
if __name__ == "__main__":
    zip_file = make_zip()
    deploy(zip_file)
