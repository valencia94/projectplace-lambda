#!/usr/bin/env python3
"""
deploy_send_approval_email.py
─────────────────────────────
Idempotent deploy script for the sendApprovalEmail Lambda.
Handles the common ‘update in progress’ race by waiting for the
function to reach Active before pushing the env-var update.
"""

import os, sys, zipfile, time, boto3

# ─── ENV ────────────────────────────────────────────────────────
def need(k):
    v = os.getenv(k)
    if not v:
        print(f"❌ Missing env var: {k}")
        sys.exit(1)
    return v

REGION        = need("AWS_REGION")
ACCOUNT_ID    = need("AWS_ACCOUNT_ID")
EMAIL_SOURCE  = need("EMAIL_SOURCE")
S3_BUCKET     = need("S3_BUCKET_NAME")
DDB_TABLE     = need("DYNAMODB_DATA_TABLE")   # original table
ACTA_API_ID   = need("ACTA_API_ID")

FUNCTION  = "sendApprovalEmail"
HANDLER   = "sendApprovalEmail.lambda_handler"
ROLE_ARN  = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"
ZIP_DIR   = "./deployment_zips"
SRC_FILE  = "approval/sendApprovalEmail.py"

# ─── ZIP CODE ───────────────────────────────────────────────────
os.makedirs(ZIP_DIR, exist_ok=True)
zip_path = f"{ZIP_DIR}/{FUNCTION}.zip"
with zipfile.ZipFile(zip_path, "w") as zf:
    zf.write(SRC_FILE, arcname=os.path.basename(SRC_FILE))
print(f"📦  Created {zip_path}")

# ─── DEPLOY ─────────────────────────────────────────────────────
lambda_client = boto3.client("lambda", region_name=REGION)
with open(zip_path, "rb") as f:
    code_bytes = f.read()

env_vars = {
    "AWS_REGION":          REGION,
    "EMAIL_SOURCE":        EMAIL_SOURCE,
    "S3_BUCKET_NAME":      S3_BUCKET,
    "DYNAMODB_TABLE_NAME": DDB_TABLE,
    "ACTA_API_ID":         ACTA_API_ID,
}

def wait_until(fn_name: str, waiter: str):
    w = lambda_client.get_waiter(waiter)
    while True:
        try:
            w.wait(FunctionName=fn_name)
            return
        except lambda_client.exceptions.ResourceNotFoundException:
            time.sleep(2)

try:
    lambda_client.get_function(FunctionName=FUNCTION)
    print("🔁 Updating code …")
    lambda_client.update_function_code(FunctionName=FUNCTION, ZipFile=code_bytes)
    wait_until(FUNCTION, "function_updated")
    print("⚙️  Updating environment …")
    lambda_client.update_function_configuration(
        FunctionName=FUNCTION,
        Environment={"Variables": env_vars},
        Timeout=60,            # keep at 60 s – e-mail send is quick
        MemorySize=256,
    )
except lambda_client.exceptions.ResourceNotFoundException:
    print("🚀 Creating function …")
    lambda_client.create_function(
        FunctionName=FUNCTION,
        Runtime="python3.9",
        Role=ROLE_ARN,
        Handler=HANDLER,
        Code={"ZipFile": code_bytes},
        Timeout=60,
        MemorySize=256,
        Publish=True,
        Environment={"Variables": env_vars},
    )

arn = lambda_client.get_function(FunctionName=FUNCTION)["Configuration"]["FunctionArn"]
print("✅ Deployed →", arn)
