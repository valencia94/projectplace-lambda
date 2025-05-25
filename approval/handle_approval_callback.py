#!/usr/bin/env python3
"""
handle_approval_callback.py
─────────────────────────────────────────────────────────────
Receives GET /approve?token=...&status=approved|rejected  
Looks up token in DynamoDB (GSI preferred, falls back to scan)  
Writes approval_status + timestamp and returns branded HTML page
"""

import os, json, datetime, urllib.parse, boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

REGION      = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME = (os.getenv("DYNAMODB_ENRICHMENT_TABLE")    # ← preferred (v2)
              or os.getenv("DYNAMODB_TABLE_NAME"))      # ← fallback (v3

ddb   = boto3.resource("dynamodb", region_name=REGION)
table = ddb.Table(TABLE_NAME)

BRAND_COLOR = "#4AC795"
HTML_TPL = """\
<html>
  <body style="font-family:Verdana; text-align:center; margin-top:40px">
    <h2 style="color:{color}">{title}</h2>
    <p>{msg}</p>
  </body>
</html>"""

def lambda_handler(event, context):
    qs = event.get("queryStringParameters") or {}
    token  = qs.get("token")
    status = qs.get("status")

    if not token or status not in ("approved", "rejected"):
        return _html(400, "Invalid request",
                     "Missing or incorrect parameters in the URL.")

    # ── 1. Find record by approval_token ───────────────────────────
    try:
        if _gsi_exists():
            resp = table.query(
                IndexName="approval_token-index",
                KeyConditionExpression=Key("approval_token").eq(token)
            )
            items = resp.get("Items", [])
        else:  # slow path – unlikely after infra patch
            scan = table.scan(FilterExpression=Attr("approval_token").eq(token))
            items = scan.get("Items", [])
    except ClientError as e:
        return _html(500, "Database error", e.response["Error"]["Message"])

    if not items:
        return _html(404, "Token not found",
                     "This approval link is no longer valid.")

    item = items[0]
    pk   = {"project_id": item["project_id"], "card_id": item["card_id"]}

    # ── 2. Write decision + timestamp ──────────────────────────────
    table.update_item(
        Key=pk,
        UpdateExpression="SET approval_status=:s, approval_timestamp=:ts",
        ExpressionAttributeValues={
            ":s": status,
            ":ts": datetime.datetime.utcnow().isoformat() + "Z"
        }
    )

    return _html(200,
                 "Thank you for your response",
                 f"The Acta has been successfully marked as <b>{status.upper()}</b>.")

# ─── helpers ───────────────────────────────────────────────────────────
def _gsi_exists() -> bool:
    meta = table.meta.client.describe_table(TableName=TABLE_NAME)
    gsis = meta["Table"].get("GlobalSecondaryIndexes", [])
    return any(g["IndexName"] == "approval_token-index" for g in gsis)

def _html(code: int, title: str, msg: str):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "text/html"},
        "body": HTML_TPL.format(title=title, msg=msg, color=BRAND_COLOR)
    }
