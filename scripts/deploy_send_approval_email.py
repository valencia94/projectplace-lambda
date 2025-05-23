#!/usr/bin/env python3
import os, zipfile, boto3, sys, shutil

def require_env(key):
    val = os.getenv(key)
    if not val:
        print(f"❌ Missing env var: {key}")
        sys.exit(1)
    return val

print("🟢 STARTING deploy_send_approval_email.py")

# 1) gather all of the env-vars our Lambda now expects
REGION        = require_env("AWS_REGION")
ACCOUNT_ID    = require_env("AWS_ACCOUNT_ID")
TABLE_NAME    = require_env("DYNAMODB_TABLE_NAME")       # fallback v3
ENRICH_TABLE  = os.getenv("DYNAMODB_ENRICHMENT_TABLE")  # optionally v2
EMAIL_SOURCE  = require_env("EMAIL_SOURCE")
S3_BUCKET     = require_env("S3_BUCKET_NAME")
ACTA_API_ID   = require_env("ACTA_API_ID")
API_STAGE     = os.getenv("API_STAGE", "prod")

FUNCTION      = "sendApprovalEmail"
HANDLER       = "sendApprovalEmail.lambda_handler"
ROLE_ARN      = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"
ZIP_DIR       = "./deployment_zips"
SRC_FILE      = "approval/sendApprovalEmail.py"

if not os.path.exists(SRC_FILE):
    print(f"❌ ERROR: Lambda source missing → {SRC_FILE}")
    sys.exit(1)

# 2) build the zip
if os.path.exists(ZIP_DIR):
    shutil.rmtree(ZIP_DIR)
os.makedirs(ZIP_DIR, exist_ok=True)
zip_path = f"{ZIP_DIR}/{FUNCTION}.zip"
print(f"📦 Creating zip → {zip_path}")
with zipfile.ZipFile(zip_path, "w") as z:
    z.write(SRC_FILE, arcname=os.path.basename(SRC_FILE))

# 3) push to Lambda
client = boto3.client("lambda", region_name=REGION)
with open(zip_path, "rb") as f:
    code = f.read()

# pick the “live” table: v2 if present, else fallback v3
ddb_table_var = ENRICH_TABLE or TABLE_NAME

env_vars = {
    "AWS_REGION": REGION,
    "DYNAMODB_TABLE_NAME": TABLE_NAME,
    "DYNAMODB_ENRICHMENT_TABLE": ddb_table_var,
    "EMAIL_SOURCE": EMAIL_SOURCE,
    "S3_BUCKET_NAME": S3_BUCKET,
    "ACTA_API_ID": ACTA_API_ID,
    "API_STAGE": API_STAGE
}

try:
    print("🔁 Updating existing Lambda…")
    client.get_function(FunctionName=FUNCTION)
    client.update_function_code(FunctionName=FUNCTION, ZipFile=code)
    client.get_waiter("function_updated").wait(FunctionName=FUNCTION)
    print("⚙️ Updating env & timeout…")
    client.update_function_configuration(
        FunctionName=FUNCTION,
        Environment={"Variables": env_vars},
        Timeout=120
    )
except client.exceptions.ResourceNotFoundException:
    print("🚀 Creating Lambda from scratch…")
    client.create_function(
        FunctionName=FUNCTION,
        Runtime="python3.9",
        Role=ROLE_ARN,
        Handler=HANDLER,
        Code={"ZipFile": code},
        Timeout=120,
        MemorySize=256,
        Publish=True,
        Environment={"Variables": env_vars}
    )

arn = client.get_function(FunctionName=FUNCTION)["Configuration"]["FunctionArn"]
print(f"✅ sendApprovalEmail deployed → {arn}")
