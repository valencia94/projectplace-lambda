#!/usr/bin/env python3
"""
Deploy or update the sendApprovalEmail Lambda on its own.
• Zips approval/sendApprovalEmail.py (+ config if present)
• Injects env vars: AWS_REGION, EMAIL_SOURCE, ACTA_API_ID, DYNAMODB_TABLE_NAME, S3_BUCKET_NAME
"""

import os, sys, zipfile, boto3, time, pathlib

# ---------- helpers ----------
def env(key):
    val = os.getenv(key)
    if not val:
        print(f"❌ Missing env var: {key}"); sys.exit(1)
    return val

def zip_code(src_file:str, zip_name:str)->str:
    out_dir = "./deployment_zips"; os.makedirs(out_dir, exist_ok=True)
    zpath = f"{out_dir}/{zip_name}.zip"
    with zipfile.ZipFile(zpath,"w") as zf:
        zf.write(src_file, arcname=pathlib.Path(src_file).name)
    print("📦  Created", zpath)
    return zpath

# ---------- config ----------
REGION        = env("AWS_REGION")
ACCOUNT_ID    = env("AWS_ACCOUNT_ID")
ROLE_ARN      = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"

LAMBDA_NAME   = "sendApprovalEmail"
HANDLER       = "sendApprovalEmail.lambda_handler"
SRC_FILE      = "approval/sendApprovalEmail.py"
ZIP_PATH      = zip_code(SRC_FILE, LAMBDA_NAME)

ENV_VARS = {
    "AWS_REGION":          REGION,
    "EMAIL_SOURCE":        env("EMAIL_SOURCE"),
    "ACTA_API_ID":         env("ACTA_API_ID"),          # e.g. 4r0pt34gx4
    "DYNAMODB_TABLE_NAME": env("DYNAMODB_TABLE_NAME"),
    "S3_BUCKET_NAME":      env("S3_BUCKET_NAME")
}

lambda_client = boto3.client("lambda", region_name=REGION)

# ---------- deploy  ----------
with open(ZIP_PATH,"rb") as f: code = f.read()
try:
    lambda_client.get_function(FunctionName=LAMBDA_NAME)
    print("🔁 Updating Lambda code…")
    lambda_client.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=code)
    lambda_client.get_waiter("function_updated").wait(FunctionName=LAMBDA_NAME)
    lambda_client.update_function_configuration(FunctionName=LAMBDA_NAME, Environment={"Variables":ENV_VARS})
except lambda_client.exceptions.ResourceNotFoundException:
    print("🆕 Creating Lambda…")
    lambda_client.create_function(
        FunctionName=LAMBDA_NAME, Runtime="python3.9", Role=ROLE_ARN,
        Handler=HANDLER, Code={"ZipFile":code}, Timeout=60, MemorySize=256,
        Environment={"Variables":ENV_VARS}
    )
print("✅ sendApprovalEmail deployed/updated.")
