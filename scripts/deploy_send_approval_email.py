#!/usr/bin/env python3
import os, sys, zipfile, boto3, shutil, subprocess, json, pathlib, textwrap

def env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        print(f"❌  Missing env var: {key}", file=sys.stderr)
        sys.exit(1)
    return val

print("🟢  Deploying sendApprovalEmail Lambda")

# ── ENV & paths ─────────────────────────────────────────────────────
REGION       = env("AWS_REGION")
ACCOUNT_ID   = env("AWS_ACCOUNT_ID")               # GitHub secret
ROLE_ARN     = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"

ZIP_DIR      = pathlib.Path("deployment_zips")
ZIP_DIR.mkdir(exist_ok=True)

FUNC_NAME    = "sendApprovalEmail"
SRC_PATH     = pathlib.Path("approval/send_approval_email.py")
ZIP_PATH     = ZIP_DIR / f"{FUNC_NAME}.zip"

# ── Package ─────────────────────────────────────────────────────────
with zipfile.ZipFile(ZIP_PATH, "w") as z:
    z.write(SRC_PATH, arcname=SRC_PATH.name)
print(f"📦  Created {ZIP_PATH}")

lambda_client = boto3.client("lambda", region_name=REGION)
with open(ZIP_PATH, "rb") as f:
    code_blob = f.read()

env_vars = {
    "AWS_REGION":               REGION,
    "DYNAMODB_ENRICHMENT_TABLE": os.environ["DYNAMODB_ENRICHMENT_TABLE"],
    "S3_BUCKET_NAME":           os.environ["S3_BUCKET_NAME"],
    "EMAIL_SOURCE":             os.environ["EMAIL_SOURCE"],
    "ACTA_API_ID":              os.environ["ACTA_API_ID"],
    "API_STAGE":                os.getenv("API_STAGE", "prod"),
}

try:
    lambda_client.get_function(FunctionName=FUNC_NAME)
    print("🔁  Updating Lambda code + config …")
    lambda_client.update_function_code(FunctionName=FUNC_NAME, ZipFile=code_blob)
    lambda_client.get_waiter("function_updated").wait(FunctionName=FUNC_NAME)
    lambda_client.update_function_configuration(
        FunctionName=FUNC_NAME,
        Environment={"Variables": env_vars},
        Timeout=120, MemorySize=256
    )
except lambda_client.exceptions.ResourceNotFoundException:
    print("🚀  Creating Lambda function …")
    lambda_client.create_function(
        FunctionName=FUNC_NAME,
        Runtime="python3.9",
        Role=ROLE_ARN,
        Handler="send_approval_email.lambda_handler",
        Code={"ZipFile": code_blob},
        Timeout=120, MemorySize=256,
        Environment={"Variables": env_vars}
    )

arn = lambda_client.get_function(FunctionName=FUNC_NAME)["Configuration"]["FunctionArn"]
print(f"✅  Deployed → {arn}")
