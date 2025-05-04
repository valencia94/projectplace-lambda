#!/usr/bin/env python3
import os, zipfile, boto3, pathlib

AWS_REGION   = os.environ["AWS_REGION"]
ACCOUNT_ID   = os.environ["AWS_ACCOUNT_ID"]
EMAIL_SOURCE = os.environ["EMAIL_SOURCE"]
S3_BUCKET    = os.environ["S3_BUCKET_NAME"]
DDB_TABLE    = os.environ["DYNAMODB_TABLE_NAME"]

ROLE_ARN  = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"
RUNTIME   = "python3.9"
ZIP_DIR   = "./deployment_zips"
API_STAGE = "prod"

lambda_client = boto3.client("lambda",    region_name=AWS_REGION)
apig          = boto3.client("apigateway",region_name=AWS_REGION)

# ── helper: zip single file ─────────────────────────────────────────
def make_zip(src, name):
    os.makedirs(ZIP_DIR, exist_ok=True)
    zpath = f"{ZIP_DIR}/{name}.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(src, arcname=pathlib.Path(src).name)
    return zpath

# ── helper: create or update lambda ────────────────────────────────
def upsert_lambda(name, handler, zpath, extra_env=None):
    with open(zpath, "rb") as f:
        code = f.read()

    env_vars = {
        "EMAIL_SOURCE":        EMAIL_SOURCE,
        "S3_BUCKET_NAME":      S3_BUCKET,
        "DYNAMODB_TABLE_NAME": DDB_TABLE,
        **(extra_env or {})
    }

    created = False
    try:
        lambda_client.get_function(FunctionName=name)
        lambda_client.update_function_code(FunctionName=name, ZipFile=code)
    except lambda_client.exceptions.ResourceNotFoundException:
        created = True
        lambda_client.create_function(
            FunctionName=name, Runtime="python3.9", Role=ROLE_ARN, Handler=handler,
            Code={"ZipFile": code}, Timeout=60, MemorySize=256, Publish=True,
            Environment={"Variables": env_vars}
        )

    waiter = lambda_client.get_waiter("function_active_v2" if created else "function_updated")
    waiter.wait(FunctionName=name)

    if not created:
        lambda_client.update_function_configuration(
            FunctionName=name, Environment={"Variables": env_vars})

    arn = lambda_client.get_function(FunctionName=name)["Configuration"]["FunctionArn"]
    return arn

# ── helper: ensure /approve GET exists ─────────────────────────────
def ensure_resource(api_id, parent_id, part):
    for r in apig.get_resources(restApiId=api_id)["items"]:
        if r.get("pathPart") == part:
            return r["id"]
    return apig.create_resource(restApiId=api_id, parentId=parent_id, pathPart=part)["id"]

def connect_get(api_id, res_id, lambda_arn):
    try:
        apig.put_method(restApiId=api_id, resourceId=res_id, httpMethod="GET", authorizationType="NONE")
    except apig.exceptions.ConflictException:
        pass
    apig.put_integration(
        restApiId=api_id, resourceId=res_id, httpMethod="GET",
        type="AWS_PROXY", integrationHttpMethod="POST",
        uri=f"arn:aws:apigateway:{AWS_REGION}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"
    )

# ── main deploy sequence ───────────────────────────────────────────
def main():
    email_zip = make_zip("approval/sendApprovalEmail.py", "sendApprovalEmail")
    cb_zip    = make_zip("approval/handleApprovalCallback.py", "handleApprovalCallback")

    email_arn = upsert_lambda("sendApprovalEmail", "sendApprovalEmail.lambda_handler", email_zip)
    cb_arn    = upsert_lambda("handleApprovalCallback","handleApprovalCallback.lambda_handler", cb_zip)

    api = next((a for a in apig.get_rest_apis()['items'] if a['name'] == "ActaApprovalAPI"),
               None) or apig.create_rest_api(name="ActaApprovalAPI")
    api_id  = api["id"]
    root_id = next(r["id"] for r in apig.get_resources(restApiId=api_id)["items"] if r["path"] == "/")

    approve_id = ensure_resource(api_id, root_id, "approve")
    connect_get(api_id, approve_id, cb_arn)

    try:
        lambda_client.add_permission(
            FunctionName="handleApprovalCallback",
            StatementId=f"apig-{api_id}-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{AWS_REGION}:{ACCOUNT_ID}:{api_id}/*/GET/approve")
    except lambda_client.exceptions.ResourceConflictException:
        pass

    apig.create_deployment(restApiId=api_id, stageName=API_STAGE)
    print("API URL:",
          f"https://{api_id}.execute-api.{AWS_REGION}.amazonaws.com/{API_STAGE}/approve")

    lambda_client.update_function_configuration(
        FunctionName="sendApprovalEmail",
        Environment={"Variables":{
            "EMAIL_SOURCE":        EMAIL_SOURCE,
            "S3_BUCKET_NAME":      S3_BUCKET,
            "DYNAMODB_TABLE_NAME": DDB_TABLE,
            "ACTA_API_ID":         api_id
        }}
    )

    # expose to workflow
    global last_api_id; last_api_id = api_id


if __name__ == "__main__":
    main()
