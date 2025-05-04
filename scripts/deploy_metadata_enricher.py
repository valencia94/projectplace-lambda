#!/usr/bin/env python3
import os, zipfile, boto3, sys

def require_env(key):
    val = os.getenv(key)
    if not val:
        print(f"❌ Missing env var: {key}")
        sys.exit(1)
    return val

# ─── ENVIRONMENT ─────────────────────────────────────────────
AWS_REGION  = require_env("AWS_REGION")
ACCOUNT_ID  = require_env("AWS_ACCOUNT_ID")
TABLE_NAME  = require_env("DYNAMODB_TABLE_NAME")
SECRET_NAME = require_env("PROJECTPLACE_SECRET_NAME")

FUNCTION    = "projectMetadataEnricher"
HANDLER     = "project_metadata_enricher.lambda_handler"
ROLE_ARN    = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"
ZIP_DIR     = "./deployment_zips"
SRC_FILE    = "approval/project_metadata_enricher.py"

# ─── ZIP PACKAGE ─────────────────────────────────────────────
def make_zip():
    os.makedirs(ZIP_DIR, exist_ok=True)
    zpath = f"{ZIP_DIR}/{FUNCTION}.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(SRC_FILE, arcname=os.path.basename(SRC_FILE))
    return zpath

# ─── DEPLOY LAMBDA ───────────────────────────────────────────
def deploy(zip_path):
    client = boto3.client("lambda", region_name=AWS_REGION)
    with open(zip_path, "rb") as f:
        code = f.read()

    env_vars = {
        "AWS_REGION": AWS_REGION,
        "DYNAMODB_TABLE_NAME": TABLE_NAME,
        "PROJECTPLACE_SECRET_NAME": SECRET_NAME
    }

    try:
        client.get_function(FunctionName=FUNCTION)
        print("🔁 Updating Lambda code & config…")
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
            Timeout=120,
            MemorySize=256,
            Environment={"Variables": env_vars}
        )
    print("✅ Lambda deployed.")

# ─── MAIN ────────────────────────────────────────────────────
if __name__ == "__main__":
    zp = make_zip()
    deploy(zp)
