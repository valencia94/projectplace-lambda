#!/usr/bin/env python3

import os
import boto3
import zipfile
import json
import time
import sys

# ───────────────────────────────────────────────────────
# CONFIGURATION
# ───────────────────────────────────────────────────────
RESERVED_KEYS = ["AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]

REGION = os.getenv("AWS_REGION", "us-east-2")
ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID")

if not ACCOUNT_ID:
    raise Exception("❌ Missing AWS_ACCOUNT_ID environment variable.")

LAMBDA_ROLE = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectPlaceLambdaRole"
ZIP_DIR = "./deployment_zips"
TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME", "ProjectPlace_DataExtrator_landing_table_v3")
SES_EMAIL = os.getenv("EMAIL_SOURCE", "AutomationSolutionsCenter@cvdexinfo.com")
S3_BUCKET = os.getenv("S3_BUCKET_NAME", "projectplace-dv-2025-x9a7b")
DOMAIN_NAME = os.getenv("DOMAIN", "api.cvdextech.com")

SEND_EMAIL_FN = "sendApprovalEmail"
HANDLE_CB_FN = "handleApprovalCallback"

# ───────────────────────────────────────────────────────
# ENV VALIDATION
# ───────────────────────────────────────────────────────
def require_env(key):
    val = os.getenv(key)
    if not val:
        print(f"❌ ERROR: Missing required env var: {key}")
        sys.exit(1)
    return val

def validate_env(env_dict):
    for k in env_dict:
        if k in RESERVED_KEYS:
            print(f"❌ ERROR: '{k}' is a reserved AWS key and cannot be set.")
            sys.exit(1)
    print("✅ Environment variable validation passed.")

# ───────────────────────────────────────────────────────
# ZIP CREATION
# ───────────────────────────────────────────────────────
def create_zip(lambda_file, zip_name):
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = os.path.join(ZIP_DIR, zip_name)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        # Include all .py files in approval
        for root, _, files in os.walk("approval"):
            for file in files:
                filepath = os.path.join(root, file)
                zipf.write(filepath, arcname=os.path.relpath(filepath, start="."))
        # Include config
        for root, _, files in os.walk("config"):
            for file in files:
                filepath = os.path.join(root, file)
                zipf.write(filepath, arcname=os.path.relpath(filepath, start="."))
        # Include the entrypoint
        zipf.write(lambda_file, arcname=os.path.basename(lambda_file))
    print(f"📦 Created ZIP at: {zip_path}")
    return zip_path

# ───────────────────────────────────────────────────────
# LAMBDA DEPLOYMENT
# ───────────────────────────────────────────────────────
def deploy_lambda(fn_name, zip_path, handler, env_vars):
    validate_env(env_vars)
    client = boto3.client("lambda", region_name=REGION)
    with open(zip_path, 'rb') as f:
        zipped_code = f.read()

    try:
        client.get_function(FunctionName=fn_name)
        print(f"🔁 Updating Lambda: {fn_name}")
        client.update_function_code(FunctionName=fn_name, ZipFile=zipped_code)
        print("⏳ Waiting for code update...")
        time.sleep(5)
        client.update_function_configuration(FunctionName=fn_name, Environment={"Variables": env_vars})
    except client.exceptions.ResourceNotFoundException:
        print(f"🆕 Creating new Lambda: {fn_name}")
        client.create_function(
            FunctionName=fn_name,
            Runtime="python3.9",
            Role=LAMBDA_ROLE,
            Handler=handler,
            Code={"ZipFile": zipped_code},
            Timeout=30,
            MemorySize=256,
            Environment={"Variables": env_vars}
        )

# ───────────────────────────────────────────────────────
# API GATEWAY CONFIGURATION
# ───────────────────────────────────────────────────────
def create_api_gateway():
    apig = boto3.client("apigateway", region_name=REGION)
    lambd = boto3.client("lambda", region_name=REGION)
    rest_apis = apig.get_rest_apis()

    existing = next((api for api in rest_apis['items'] if api['name'] == 'ActaApprovalAPI'), None)
    if existing:
        print(f"🌐 API Gateway already exists: {existing['id']}")
        return existing['id']

    print("🌐 Creating API Gateway: ActaApprovalAPI")
    api = apig.create_rest_api(name="ActaApprovalAPI")
    root_id = apig.get_resources(restApiId=api['id'])['items'][0]['id']

    resource = apig.create_resource(restApiId=api['id'], parentId=root_id, pathPart="approve")
    apig.put_method(restApiId=api['id'], resourceId=resource['id'], httpMethod="GET", authorizationType="NONE")

    lambda_arn = f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:{HANDLE_CB_FN}"
    uri = f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"

    apig.put_integration(
        restApiId=api['id'],
        resourceId=resource['id'],
        httpMethod="GET",
        type="AWS_PROXY",
        integrationHttpMethod="POST",
        uri=uri
    )

    lambd.add_permission(
        FunctionName=HANDLE_CB_FN,
        StatementId="AllowAPIGatewayInvoke",
        Action="lambda:InvokeFunction",
        Principal="apigateway.amazonaws.com",
        SourceArn=f"arn:aws:execute-api:{REGION}:{ACCOUNT_ID}:{api['id']}/*/GET/approve"
    )

    print("✅ API Gateway /approve endpoint created.")
    return api['id']

# ───────────────────────────────────────────────────────
# EXECUTION
# ───────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Starting Lambda deployments...")

    zip_send = create_zip("approval/sendApprovalEmail.py", "sendApprovalEmail.zip")
    zip_cb = create_zip("approval/handleApprovalCallback.py", "handleApprovalCallback.zip")

    deploy_lambda(SEND_EMAIL_FN, zip_send, "sendApprovalEmail.lambda_handler", {
        "DYNAMODB_TABLE_NAME": TABLE_NAME,
        "EMAIL_SOURCE": SES_EMAIL,
        "S3_BUCKET_NAME": S3_BUCKET,
        "DOMAIN": DOMAIN_NAME
    })

    deploy_lambda(HANDLE_CB_FN, zip_cb, "handleApprovalCallback.lambda_handler", {
        "DYNAMODB_TABLE_NAME": TABLE_NAME
    })

    print("🌐 Setting up API Gateway...")
    api_id = create_api_gateway()
    print(f"✅ Deployment complete | Gateway ID: {api_id}")
