#!/usr/bin/env python3

"""
Deploys / updates:
  • sendApprovalEmail    (Zip-based Lambda)
  • handleApprovalCallback (Zip-based Lambda)
  • API Gateway  REST API  -> /approve (GET) -> Lambda proxy
  • Adds permission for API to invoke Lambda
  • Deploys to stage 'prod'
"""

import os, zipfile, boto3, json, pathlib, sys

AWS_REGION   = os.environ["AWS_REGION"]
ACCOUNT_ID   = os.environ["AWS_ACCOUNT_ID"]

EMAIL_SOURCE = os.environ["EMAIL_SOURCE"]            # verified
S3_BUCKET    = os.environ["S3_BUCKET_NAME"]

LAMBDA_ROLE  = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"
RUNTIME      = "python3.9"
ZIP_DIR      = "./deployment_zips"
API_STAGE    = "prod"

lambda_client = boto3.client("lambda",    region_name=AWS_REGION)
apig          = boto3.client("apigateway",region_name=AWS_REGION)

def package(src_path:str, zip_name:str)->str:
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = f"{ZIP_DIR}/{zip_name}.zip"
    with zipfile.ZipFile(zip_path,"w") as zf:
        zf.write(src_path, arcname=pathlib.Path(src_path).name)
    return zip_path

def upsert_lambda(fn_name:str, handler:str, zip_path:str, extra_env:dict=None)->str:
    with open(zip_path,"rb") as f: code = f.read()
    env = {"Variables": {
        "AWS_REGION": AWS_REGION,
        "EMAIL_SOURCE": EMAIL_SOURCE,
        "S3_BUCKET_NAME": S3_BUCKET,
        **(extra_env or {})
    }}
    try:
        lambda_client.get_function(FunctionName=fn_name)
        lambda_client.update_function_code(FunctionName=fn_name, ZipFile=code)
        lambda_client.update_function_configuration(FunctionName=fn_name, Environment=env)
    except lambda_client.exceptions.ResourceNotFoundException:
        lambda_client.create_function(
            FunctionName=fn_name, Runtime=RUNTIME, Role=LAMBDA_ROLE, Handler=handler,
            Code={"ZipFile": code}, Timeout=60, MemorySize=256, Environment=env
        )
    arn = lambda_client.get_function(FunctionName=fn_name)["Configuration"]["FunctionArn"]
    print("Lambda ready ->", fn_name, arn)
    return arn

def ensure_resource(api_id, parent_id, part):
    res = [r for r in apig.get_resources(restApiId=api_id)['items'] if r['pathPart']==part]
    if res: return res[0]['id']
    return apig.create_resource(restApiId=api_id, parentId=parent_id, pathPart=part)['id']

def ensure_method(api_id, res_id, http_method, lambda_arn):
    try:
        apig.put_method(restApiId=api_id, resourceId=res_id,
                        httpMethod=http_method, authorizationType="NONE")
    except apig.exceptions.ConflictException:
        pass
    apig.put_integration(
        restApiId=api_id, resourceId=res_id, httpMethod=http_method,
        type="AWS_PROXY", integrationHttpMethod="POST",
        uri=f"arn:aws:apigateway:{AWS_REGION}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"
    )

def main():
    # 1) Package & deploy Lambdas
    ap_email_zip = package("approval/sendApprovalEmail.py", "sendApprovalEmail")
    cb_zip       = package("approval/handleApprovalCallback.py", "handleApprovalCallback")

    # sendApprovalEmail needs API_ID later, but we don't have it yet → update env after API deploy
    send_arn = upsert_lambda("sendApprovalEmail", "sendApprovalEmail.lambda_handler", ap_email_zip)
    cb_arn   = upsert_lambda("handleApprovalCallback","handleApprovalCallback.lambda_handler", cb_zip)

    # 2) Upsert REST API
    apis = apig.get_rest_apis()['items']
    api  = next((a for a in apis if a['name']=="ActaApprovalAPI"), None) or \
           apig.create_rest_api(name="ActaApprovalAPI")
    api_id = api['id']
    root_id = [r['id'] for r in apig.get_resources(restApiId=api_id)['items'] if r['path']=="/"][0]

    approve_id = ensure_resource(api_id, root_id, "approve")
    ensure_method(api_id, approve_id, "GET", cb_arn)

    # 3) Grant invoke permission
    try:
        lambda_client.add_permission(
            FunctionName="handleApprovalCallback",
            StatementId=f"apig-{api_id}-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{AWS_REGION}:{ACCOUNT_ID}:{api_id}/*/GET/approve"
        )
    except lambda_client.exceptions.ResourceConflictException:
        pass           # already exists

    # 4) Deploy to stage
    apig.create_deployment(restApiId=api_id, stageName=API_STAGE)
    print("✅ API Gateway ID:", api_id)

    # 5) Update sendApprovalEmail env with ACTA_API_ID
    lambda_client.update_function_configuration(
        FunctionName="sendApprovalEmail",
        Environment={
            "Variables":{
                "AWS_REGION":AWS_REGION,
                "EMAIL_SOURCE":EMAIL_SOURCE,
                "S3_BUCKET_NAME":S3_BUCKET,
                "ACTA_API_ID": api_id,
                "DYNAMODB_TABLE_NAME": os.environ["DYNAMODB_TABLE_NAME"]
            }
        }
    )

if __name__ == "__main__":
    main()
