#!/usr/bin/env python3
"""
Re-deploy the *sendApprovalEmail* Lambda.
– Falls back to DYNAMODB_TABLE_NAME if DYNAMODB_ENRICHMENT_TABLE is absent.
"""

import os, sys, zipfile, shutil, boto3

def need(var: str) -> str:
    v = os.getenv(var)
    if not v:
        print(f"❌ Missing env var: {var}")
        sys.exit(1)
    return v

print("🟢  Deploying sendApprovalEmail Lambda")

REGION       = need("AWS_REGION")
ACCOUNT_ID   = need("AWS_ACCOUNT_ID")
TABLE_NAME   = os.getenv("DYNAMODB_ENRICHMENT_TABLE") or need("DYNAMODB_TABLE_NAME")
EMAIL_SOURCE = need("EMAIL_SOURCE")
S3_BUCKET    = need("S3_BUCKET_NAME")
API_ID       = need("ACTA_API_ID")

FUNCTION = "sendApprovalEmail"
HANDLER  = "send_approval_email.lambda_handler"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"

ZIP_DIR  = "deployment_zips"
SRC      = "approval/send_approval_email.py"
os.makedirs(ZIP_DIR, exist_ok=True)
ZIP_PATH = f"{ZIP_DIR}/{FUNCTION}.zip"
with zipfile.ZipFile(ZIP_PATH, "w") as zf:
    zf.write(SRC, arcname=os.path.basename(SRC))
print(f"📦  Created {ZIP_PATH}")

lambda_client = boto3.client("lambda", region_name=REGION)
with open(ZIP_PATH, "rb") as f:
    code = f.read()

env = {
    "DYNAMODB_TABLE_NAME": TABLE_NAME,
    "EMAIL_SOURCE":        EMAIL_SOURCE,
    "S3_BUCKET_NAME":      S3_BUCKET,
    "ACTA_API_ID":         API_ID
}

try:
    lambda_client.get_function(FunctionName=FUNCTION)
    print("🔁 Updating code …")
    lambda_client.update_function_code(FunctionName=FUNCTION, ZipFile=code)
    lambda_client.get_waiter("function_updated").wait(FunctionName=FUNCTION)
    lambda_client.update_function_configuration(
        FunctionName=FUNCTION,
        Environment={"Variables": env},
        Timeout=120
    )
except lambda_client.exceptions.ResourceNotFoundException:
    print("🚀 Creating function …")
    lambda_client.create_function(
        FunctionName=FUNCTION,
        Runtime="python3.9",
        Role=ROLE_ARN,
        Handler=HANDLER,
        Code={"ZipFile": code},
        Timeout=120,
        MemorySize=256,
        Publish=True,
        Environment={"Variables": env}
    )
print("✅  Lambda ready")
