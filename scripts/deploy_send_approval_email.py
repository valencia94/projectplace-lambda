#!/usr/bin/env python3
"""
Package & (re)deploy the **sendApprovalEmail** Lambda.
Reads env vars emitted by the workflow.

Required env:
  AWS_REGION                 e.g. us-east-2
  AWS_ACCOUNT_ID
  EMAIL_SOURCE               verified SES address
  DYNAMODB_DATA_TABLE        landing v3 table   ←  **new canonical name**
  S3_BUCKET_NAME
"""

import os, sys, zipfile, boto3, shutil

def need(k):
    v = os.getenv(k)
    if not v:
        print(f"❌ Missing env var: {k}"); sys.exit(1)
    return v

# ── ENV ──────────────────────────────────────────────────────────────────────
REGION       = need("AWS_REGION")
ACCOUNT_ID   = need("AWS_ACCOUNT_ID")
EMAIL_SRC    = need("EMAIL_SOURCE")
DATA_TABLE   = need("DYNAMODB_DATA_TABLE")
S3_BUCKET    = need("S3_BUCKET_NAME")

ROLE_ARN   = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"
FUNCTION   = "sendApprovalEmail"
HANDLER    = "sendApprovalEmail.lambda_handler"
RUNTIME    = "python3.9"
ZIP_DIR    = "./deployment_zips"
SRC_FILE   = "approval/sendApprovalEmail.py"

# ── ZIP PACKAGE ──────────────────────────────────────────────────────────────
if os.path.exists(ZIP_DIR): shutil.rmtree(ZIP_DIR)
os.makedirs(ZIP_DIR, exist_ok=True)
zip_path = f"{ZIP_DIR}/{FUNCTION}.zip"
with zipfile.ZipFile(zip_path, "w") as zf:
    zf.write(SRC_FILE, arcname=os.path.basename(SRC_FILE))
print(f"📦  Created {zip_path}")

# ── DEPLOY / UPDATE ──────────────────────────────────────────────────────────
env = {
    "AWS_REGION":          REGION,
    "EMAIL_SOURCE":        EMAIL_SRC,
    "DYNAMODB_DATA_TABLE": DATA_TABLE,
    "S3_BUCKET_NAME":      S3_BUCKET
}

lambda_client = boto3.client("lambda", region_name=REGION)
with open(zip_path, "rb") as f:
    code_blob = f.read()

try:
    lambda_client.get_function(FunctionName=FUNCTION)
    print("🔁 Updating Lambda code & env …")
    lambda_client.update_function_code(FunctionName=FUNCTION, ZipFile=code_blob)
    lambda_client.update_function_configuration(
        FunctionName=FUNCTION,
        Environment={"Variables": env}
    )
except lambda_client.exceptions.ResourceNotFoundException:
    print("🚀 Creating Lambda …")
    lambda_client.create_function(
        FunctionName=FUNCTION,
        Role=ROLE_ARN,
        Runtime=RUNTIME,
        Handler=HANDLER,
        Code={"ZipFile": code_blob},
        Timeout=120,
        MemorySize=256,
        Publish=True,
        Environment={"Variables": env}
    )

print("✅ sendApprovalEmail Lambda ready")
