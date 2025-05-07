#!/usr/bin/env python3
import os, zipfile, boto3, sys, shutil

def require_env(key):
    val = os.getenv(key)
    if not val:
        print(f"❌ Missing env var: {key}")
        sys.exit(1)
    return val

print("🟢 STARTING deploy_send_approval_email.py")

try:
    REGION       = require_env("AWS_REGION")
    ACCOUNT_ID   = require_env("AWS_ACCOUNT_ID")
    TABLE_NAME   = require_env("DYNAMODB_TABLE_NAME")
    EMAIL_SOURCE = require_env("EMAIL_SOURCE")
    S3_BUCKET    = require_env("S3_BUCKET_NAME")
    API_ID       = require_env("ACTA_API_ID")

    FUNCTION     = "sendApprovalEmail"
    HANDLER      = "sendApprovalEmail.lambda_handler"
    ROLE_ARN     = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"
    ZIP_DIR      = "./deployment_zips"
    SRC_FILE     = "approval/sendApprovalEmail.py"

    if not os.path.exists(SRC_FILE):
        print(f"❌ ERROR: Lambda source file missing → {SRC_FILE}")
        sys.exit(1)

    if os.path.exists(ZIP_DIR):
        shutil.rmtree(ZIP_DIR)
    os.makedirs(ZIP_DIR, exist_ok=True)

    zip_path = f"{ZIP_DIR}/{FUNCTION}.zip"
    print(f"📦 Creating zip → {zip_path}")
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(SRC_FILE, arcname=os.path.basename(SRC_FILE))

    client = boto3.client("lambda", region_name=REGION)
    with open(zip_path, "rb") as f:
        zipped_code = f.read()

    env_vars = {
        "DYNAMODB_TABLE_NAME": TABLE_NAME,
        "EMAIL_SOURCE": EMAIL_SOURCE,
        "S3_BUCKET_NAME": S3_BUCKET,
        "ACTA_API_ID": API_ID
    }

    try:
        print("🔁 Updating Lambda code...")
        client.get_function(FunctionName=FUNCTION)
        client.update_function_code(FunctionName=FUNCTION, ZipFile=zipped_code)

        print("⏳ Waiting for update to complete...")
        client.get_waiter("function_updated").wait(FunctionName=FUNCTION)

        print("⚙️ Updating Lambda environment...")
        client.update_function_configuration(
            FunctionName=FUNCTION,
            Environment={"Variables": env_vars},
            Timeout=120
        )
    except client.exceptions.ResourceNotFoundException:
        print("🚀 Creating new Lambda function...")
        client.create_function(
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

    arn = client.get_function(FunctionName=FUNCTION)["Configuration"]["FunctionArn"]
    print(f"✅ Lambda deployed → {arn}")

except Exception as e:
    print("❌ DEPLOY FAILED:", str(e))
    sys.exit(1)
