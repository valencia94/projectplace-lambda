#!/usr/bin/env python3

import os
import boto3
import zipfile
import json
import time  # <--- Added here for sleep timing control

# Environment variables
REGION = os.environ.get("AWS_REGION")
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID")
if not ACCOUNT_ID:
    raise Exception("AWS_ACCOUNT_ID environment variable not set.")

LAMBDA_ROLE = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"
TABLE_NAME = "ProjectPlace_DataExtrator_landing_table_v3"
SES_EMAIL = "noreply@notifications.cvdextech.com"
S3_BUCKET = os.environ.get("S3_BUCKET_NAME")
DOMAIN_NAME = "api.cvdextech.com"
ZIP_DIR = "./deployment_zips"

SEND_EMAIL_FN = "sendApprovalEmail"
HANDLE_CB_FN = "handleApprovalCallback"

# --- Create ZIP function ---
def create_zip(source_file, zip_name):
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = os.path.join(ZIP_DIR, zip_name)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        # Add all files from "approval" folder
        for root, dirs, files in os.walk("approval"):
            for file in files:
                filepath = os.path.join(root, file)
                zipf.write(filepath, arcname=os.path.relpath(filepath, start="approval"))
        # Add main Lambda handler
        zipf.write(source_file, arcname=os.path.basename(source_file))
    return zip_path

# --- Deploy Lambda function ---
def deploy_lambda(lambda_name, zip_path, handler_name, env_vars):
    client = boto3.client("lambda", region_name=REGION)
    with open(zip_path, 'rb') as f:
        zipped_code = f.read()

    try:
        print(f"Checking if Lambda {lambda_name} exists...")
        client.get_function(FunctionName=lambda_name)
        print(f"Updating existing Lambda: {lambda_name}")
        client.update_function_code(FunctionName=lambda_name, ZipFile=zipped_code)

        # Wait for AWS Lambda update to finalize
        print(f"Waiting for AWS to finalize code update for {lambda_name}...")
        time.sleep(30)

        client.update_function_configuration(
            FunctionName=lambda_name,
            Environment={"Variables": env_vars}
        )
    except client.exceptions.ResourceNotFoundException:
        print(f"Creating new Lambda: {lambda_name}")
        print(f"Using IAM Role ARN: {LAMBDA_ROLE}")
        client.create_function(
            FunctionName=lambda_name,
            Runtime="python3.9",
            Role=LAMBDA_ROLE,
            Handler=handler_name,
            Code={"ZipFile": zipped_code},
            Timeout=30,
            MemorySize=256,
            Environment={"Variables": env_vars}
        )

# --- Create API Gateway ---
def create_api_gateway():
    apig = boto3.client("apigateway", region_name=REGION)
    lambd = boto3.client("lambda", region_name=REGION)
    rest_apis = apig.get_rest_apis()
    existing = next((item for item in rest_apis['items'] if item['name'] == 'ActaApprovalAPI'), None)

    if existing:
        print("API Gateway already exists: ActaApprovalAPI")
        return existing['id']

    api = apig.create_rest_api(name="ActaApprovalAPI")
    root_id = apig.get_resources(restApiId=api['id'])['items'][0]['id']

    resource = apig.create_resource(
        restApiId=api['id'],
        parentId=root_id,
        pathPart="approve"
    )

    apig.put_method(
        restApiId=api['id'], resourceId=resource['id'],
        httpMethod="GET", authorizationType="NONE"
    )

    lambda_arn = f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:{HANDLE_CB_FN}"
    uri = f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"

    apig.put_integration(
        restApiId=api['id'], resourceId=resource['id'], httpMethod="GET",
        type="AWS_PROXY", integrationHttpMethod="POST", uri=uri
    )

    lambd.add_permission(
        FunctionName=HANDLE_CB_FN,
        StatementId="AllowAPIGatewayInvoke",
        Action="lambda:InvokeFunction",
        Principal="apigateway.amazonaws.com",
        SourceArn=f"arn:aws:execute-api:{REGION}:{ACCOUNT_ID}:{api['id']}/*/GET/approve"
    )

    print("API Gateway /approve endpoint created.")
    return api['id']

# --- Main Execution ---
if __name__ == "__main__":
    print("📦 Creating deployment ZIPs...")
    zip_send = create_zip("approval/sendApprovalEmail.py", "sendApprovalEmail.zip")
    zip_cb = create_zip("approval/handleApprovalCallback.py", "handleApprovalCallback.zip")

    print("🚀 Deploying Lambda functions...")
    deploy_lambda(SEND_EMAIL_FN, zip_send, "sendApprovalEmail.lambda_handler", {
        "DYNAMODB_TABLE_NAME": TABLE_NAME,
        "EMAIL_SOURCE": SES_EMAIL,
        "S3_BUCKET_NAME": S3_BUCKET,
        "DOMAIN": DOMAIN_NAME
    })
    deploy_lambda(HANDLE_CB_FN, zip_cb, "handleApprovalCallback.lambda_handler", {
        "DYNAMODB_TABLE_NAME": TABLE_NAME
    })

    print("🌐 Creating or confirming API Gateway...")
    api_id = create_api_gateway()
    print(f"✅ Deployment complete | API Gateway ID: {api_id}")
