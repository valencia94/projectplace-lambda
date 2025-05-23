#!/usr/bin/env python3
"""
deploy_send_approval_email.py
──────────────────────────────────────────────────────────────
• Builds a tiny ZIP containing approval/send_approval_email.py
• Upserts the sendApprovalEmail Lambda (update if it exists,
  create if it doesn’t), and sets all required environment vars.
"""

import os, sys, zipfile, shutil, boto3

# ── helpers ─────────────────────────────────────────────────
def require_env(key: str, fallback: str | None = None) -> str:
    """Exit with an error if the required env-var is missing/empty."""
    val = os.getenv(key, fallback)
    if val is None or val.strip() == "":
        print(f"❌ Missing env var: {key}")
        sys.exit(1)
    return val.strip()

# ── config ──────────────────────────────────────────────────
print("🟢  Deploying sendApprovalEmail Lambda")

REGION        = require_env("AWS_REGION")
ACCOUNT_ID    = require_env("AWS_ACCOUNT_ID")
TABLE_NAME    = require_env("DYNAMODB_ENRICHMENT_TABLE")          # ← v2 table
EMAIL_SOURCE  = require_env("EMAIL_SOURCE")
S3_BUCKET     = require_env("S3_BUCKET_NAME")
API_ID        = require_env("ACTA_API_ID")
ROLE_ARN      = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"

FUNCTION  = "sendApprovalEmail"
HANDLER   = "send_approval_email.lambda_handler"
SRC_FILE  = "approval/send_approval_email.py"
ZIP_DIR   = "./deployment_zips"
ZIP_PATH  = f"{ZIP_DIR}/{FUNCTION}.zip"

# ── package source ──────────────────────────────────────────
if not os.path.exists(SRC_FILE):
    print(f"❌ Source file not found → {SRC_FILE}")
    sys.exit(1)

shutil.rmtree(ZIP_DIR, ignore_errors=True)
os.makedirs(ZIP_DIR, exist_ok=True)

print(f"📦  Creating {ZIP_PATH}")
with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write(SRC_FILE, arcname=os.path.basename(SRC_FILE))

with open(ZIP_PATH, "rb") as f:
    zipped_code = f.read()

# ── Lambda client & env-vars ────────────────────────────────
lambda_client = boto3.client("lambda", region_name=REGION)

env_vars = {
    "AWS_REGION": REGION,
    "DYNAMODB_ENRICHMENT_TABLE": TABLE_NAME,
    "EMAIL_SOURCE": EMAIL_SOURCE,
    "S3_BUCKET_NAME": S3_BUCKET,
    "ACTA_API_ID": API_ID,
}

# ── create or update ────────────────────────────────────────
try:
    lambda_client.get_function(FunctionName=FUNCTION)           # exists → update
    print("🔁  Updating code …")
    lambda_client.update_function_code(FunctionName=FUNCTION, ZipFile=zipped_code)

    print("⚙️  Waiting for update to finish …")
    lambda_client.get_waiter("function_updated").wait(FunctionName=FUNCTION)

    print("⚙️  Updating configuration …")
    lambda_client.update_function_configuration(
        FunctionName=FUNCTION,
        Handler=HANDLER,
        Runtime="python3.9",
        Role=ROLE_ARN,
        Timeout=120,
        MemorySize=256,
        Environment={"Variables": env_vars},
    )
except lambda_client.exceptions.ResourceNotFoundException:          # create fresh
    print("🚀  Creating Lambda …")
    lambda_client.create_function(
        FunctionName=FUNCTION,
        Handler=HANDLER,
        Runtime="python3.9",
        Role=ROLE_ARN,
        Code={"ZipFile": zipped_code},
        Timeout=120,
        MemorySize=256,
        Publish=True,
        Environment={"Variables": env_vars},
    )

arn = lambda_client.get_function(FunctionName=FUNCTION)["Configuration"]["FunctionArn"]
print(f"✅  Lambda deployed → {arn}")
