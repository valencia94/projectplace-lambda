#!/usr/bin/env python3
"""
Deploy / update projectMetadataEnricher Lambda.

Adds the external dependency **requests** into the zip package automatically.
"""

import os, sys, zipfile, boto3, pathlib, subprocess

# ─── ENV ────────────────────────────────────────────────────────────
REGION     = os.environ["AWS_REGION"]
ACCOUNT_ID = os.environ["AWS_ACCOUNT_ID"]
ROLE_ARN   = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"

LAMBDA_NAME = "projectMetadataEnricher"
HANDLER     = "project_metadata_enricher.lambda_handler"
SRC_FILE    = "approval/project_metadata_enricher.py"
ZIP_DIR     = "./deployment_zips"

ENV = {
    "AWS_REGION":            REGION,
    "DYNAMODB_TABLE_NAME":   os.environ["DYNAMODB_TABLE_NAME"],
    "PROJECTPLACE_SECRET_NAME": os.environ["PROJECTPLACE_SECRET_NAME"],
}

lambda_client = boto3.client("lambda", region_name=REGION)


# ─── ZIP BUILD ──────────────────────────────────────────────────────
def build_zip() -> str:
    os.makedirs(ZIP_DIR, exist_ok=True)
    zpath = f"{ZIP_DIR}/{LAMBDA_NAME}.zip"

    # install requests into temp dir
    site_dir = "/tmp/pydeps"
    subprocess.check_call(["python3", "-m", "pip", "install", "-q", "-t", site_dir, "requests"])

    with zipfile.ZipFile(zpath, "w") as zf:
        # add site-packages
        for root, _, files in os.walk(site_dir):
            for f in files:
                full = os.path.join(root, f)
                rel  = os.path.relpath(full, start=site_dir)
                zf.write(full, arcname=rel)

        # add lambda code
        zf.write(SRC_FILE, arcname=pathlib.Path(SRC_FILE).name)

    print("📦  Created deployment zip:", zpath)
    return zpath


# ─── DEPLOY ─────────────────────────────────────────────────────────
def deploy():
    zip_path = build_zip()
    with open(zip_path, "rb") as f:
        code_bytes = f.read()

    env_cfg = {"Variables": ENV}
    try:
        lambda_client.get_function(FunctionName=LAMBDA_NAME)
        lambda_client.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=code_bytes)
        waiter = lambda_client.get_waiter("function_updated")
        waiter.wait(FunctionName=LAMBDA_NAME)
        lambda_client.update_function_configuration(FunctionName=LAMBDA_NAME, Environment=env_cfg)
    except lambda_client.exceptions.ResourceNotFoundException:
        lambda_client.create_function(
            FunctionName=LAMBDA_NAME,
            Runtime="python3.9",
            Role=ROLE_ARN,
            Handler=HANDLER,
            Code={"ZipFile": code_bytes},
            Timeout=60,
            MemorySize=256,
            Environment=env_cfg,
        )
    print("✅  projectMetadataEnricher deployed/updated.")


if __name__ == "__main__":
    missing = [k for k in ("AWS_REGION", "AWS_ACCOUNT_ID", "DYNAMODB_TABLE_NAME", "PROJECTPLACE_SECRET_NAME") if not os.getenv(k)]
    if missing:
        print("Missing env vars:", ", ".join(missing))
        sys.exit(1)
    deploy()
