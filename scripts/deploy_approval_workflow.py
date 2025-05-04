#!/usr/bin/env python3
"""
Deploy / update the Acta approval workflow:

1. sendApprovalEmail          Lambda
2. handleApprovalCallback     Lambda
3. REST API  /approve (GET)   ➜  Lambda proxy
4. Grants API ➜ Lambda invoke permission
5. Publishes to stage “prod”
"""

import os, zipfile, boto3, pathlib, json, sys, time

# ─── ENV & CONSTANTS ────────────────────────────────────────────────
AWS_REGION   = os.environ["AWS_REGION"]
ACCOUNT_ID   = os.environ["AWS_ACCOUNT_ID"]

EMAIL_SOURCE = os.environ["EMAIL_SOURCE"]                      # verified SES sender
S3_BUCKET    = os.environ["S3_BUCKET_NAME"]
DDB_TABLE    = os.environ["DYNAMODB_TABLE_NAME"]

LAMBDA_ROLE  = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"
RUNTIME      = "python3.9"
ZIP_DIR      = "./deployment_zips"
API_STAGE    = "prod"

lambda_client = boto3.client("lambda",    region_name=AWS_REGION)
apig          = boto3.client("apigateway",region_name=AWS_REGION)


# ─── HELPERS ────────────────────────────────────────────────────────
def mkzip(src: str, name: str) -> str:
    """Zip a single source file into deployment_zips/"""
    os.makedirs(ZIP_DIR, exist_ok=True)
    zpath = f"{ZIP_DIR}/{name}.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(src, arcname=pathlib.Path(src).name)
    return zpath


def upsert_lambda(fn_name: str, handler: str, zip_path: str, extra_env: dict | None = None) -> str:
    """Create or update a Lambda, wait until ACTIVE, then set environment."""
    with open(zip_path, "rb") as f:
        code_bytes = f.read()

    env = {
        "Variables": {
            "AWS_REGION":          AWS_REGION,
            "EMAIL_SOURCE":        EMAIL_SOURCE,
            "S3_BUCKET_NAME":      S3_BUCKET,
            "DYNAMODB_TABLE_NAME": DDB_TABLE,
            **(extra_env or {}),
        }
    }

    created = False
    try:
        lambda_client.get_function(FunctionName=fn_name)
        lambda_client.update_function_code(FunctionName=fn_name, ZipFile=code_bytes)
    except lambda_client.exceptions.ResourceNotFoundException:
        created = True
        lambda_client.create_function(
            FunctionName=fn_name,
            Runtime=RUNTIME,
            Role=LAMBDA_ROLE,
            Handler=handler,
            Code={"ZipFile": code_bytes},
            Timeout=60,
            MemorySize=256,
            Publish=True,
            Environment=env,
        )

    # Wait until code update completes (avoids ResourceConflictException)
    waiter = lambda_client.get_waiter("function_active_v2" if created else "function_updated")
    waiter.wait(FunctionName=fn_name)

    # Safe env-var update (needed only on update path)
    if not created:
        lambda_client.update_function_configuration(FunctionName=fn_name, Environment=env)

    arn = lambda_client.get_function(FunctionName=fn_name)["Configuration"]["FunctionArn"]
    print("✅  Lambda ready:", fn_name, "→", arn)
    return arn


def ensure_resource(api_id: str, parent_id: str, part: str) -> str:
    resources = apig.get_resources(restApiId=api_id)["items"]
    for r in resources:
        if r.get("pathPart") == part:
            return r["id"]
    return apig.create_resource(restApiId=api_id, parentId=parent_id, pathPart=part)["id"]


def ensure_method(api_id: str, res_id: str, http_method: str, lambda_arn: str) -> None:
    try:
        apig.put_method(
            restApiId=api_id,
            resourceId=res_id,
            httpMethod=http_method,
            authorizationType="NONE",
        )
    except apig.exceptions.ConflictException:
        pass

    apig.put_integration(
        restApiId=api_id,
        resourceId=res_id,
        httpMethod=http_method,
        type="AWS_PROXY",
        integrationHttpMethod="POST",
        uri=f"arn:aws:apigateway:{AWS_REGION}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations",
    )


# ─── MAIN ───────────────────────────────────────────────────────────
def main() -> None:
    # 1. Package source files
    zip_email = mkzip("approval/sendApprovalEmail.py", "sendApprovalEmail")
    zip_cb    = mkzip("approval/handleApprovalCallback.py", "handleApprovalCallback")

    # 2. Deploy Lambdas (without ACTA_API_ID yet)
    send_arn = upsert_lambda("sendApprovalEmail", "sendApprovalEmail.lambda_handler", zip_email)
    cb_arn   = upsert_lambda("handleApprovalCallback", "handleApprovalCallback.lambda_handler", zip_cb)

    # 3. Upsert REST API
    apis = apig.get_rest_apis()["items"]
    api  = next((a for a in apis if a["name"] == "ActaApprovalAPI"), None) \
           or apig.create_rest_api(name="ActaApprovalAPI")
    api_id  = api["id"]
    root_id = next(r["id"] for r in apig.get_resources(restApiId=api_id)["items"] if r["path"] == "/")

    approve_id = ensure_resource(api_id, root_id, "approve")
    ensure_method(api_id, approve_id, "GET", cb_arn)

    # 4. Allow API to call callback Lambda
    try:
        lambda_client.add_permission(
            FunctionName="handleApprovalCallback",
            StatementId=f"apig-{api_id}-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{AWS_REGION}:{ACCOUNT_ID}:{api_id}/*/GET/approve",
        )
    except lambda_client.exceptions.ResourceConflictException:
        pass

    # 5. Deploy API to stage
    apig.create_deployment(restApiId=api_id, stageName=API_STAGE)
    api_url = f"https://{api_id}.execute-api.{AWS_REGION}.amazonaws.com/{API_STAGE}/approve"
    print("🌐  API URL:", api_url)

    # 6. Inject ACTA_API_ID into sendApprovalEmail env
    lambda_client.update_function_configuration(
        FunctionName="sendApprovalEmail",
        Environment={
            "Variables": {
                "AWS_REGION":          AWS_REGION,
                "EMAIL_SOURCE":        EMAIL_SOURCE,
                "S3_BUCKET_NAME":      S3_BUCKET,
                "DYNAMODB_TABLE_NAME": DDB_TABLE,
                "ACTA_API_ID":         api_id,
            }
        },
    )
    print("🔄  sendApprovalEmail environment updated with ACTA_API_ID")


if __name__ == "__main__":
    main()
