#!/usr/bin/env python3
"""
handle_approval_callback.py – v1.4
─────────────────────────────────────────────────────────────
Receives GET /approve?token=...&status=approved|rejected[&comment=...]  
Looks up token in DynamoDB (GSI preferred, falls back to scan)  
Writes approval_status + timestamp + optional comment  
Returns branded HTML confirmation including comment if present.
"""

import os, json, datetime, urllib.parse, boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

REGION      = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME  = os.getenv("DYNAMODB_ENRICHMENT_TABLE") or os.getenv("DYNAMODB_TABLE_NAME")

ddb   = boto3.resource("dynamodb", region_name=REGION)
table = ddb.Table(TABLE_NAME)

BRAND_COLOR = "#4AC795"
HTML_TPL = """\
<html>
  <body style="font-family:Verdana; text-align:center; margin-top:40px">
    <h2 style="color:{{color}}">{{title}}</h2>
    <p>{{msg}}</p>
  </body>
</html>"""

def lambda_handler(event, context):
    qs = event.get("queryStringParameters") or {}
    token  = qs.get("token")
    status = qs.get("status")
    comment = urllib.parse.unquote_plus(qs.get("comment", "")).strip()

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
        else:
            scan = table.scan(FilterExpression=Attr("approval_token").eq(token))
            items = scan.get("Items", [])
    except ClientError as e:
        return _html(500, "Database error", e.response["Error"]["Message"])

    if not items:
        return _html(404, "Token not found",
                     "This approval link is no longer valid.")

    item = items[0]
    pk   = {"project_id": item["project_id"], "card_id": item["card_id"]}

    # ── 2. Write decision + timestamp (+ optional comment) ─────────
    update_expr = "SET approval_status = :s, approval_timestamp = :ts"
    expr_values = {
        ":s": status,
        ":ts": datetime.datetime.utcnow().isoformat() + "Z"
    }
    
    if comment:
        update_expr += ", approval_comment = :c"
        expr_values[":c"] = comment
    
    table.update_item(
        Key=pk,
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values
    )
    
    msg = "The Acta has been successfully marked as <b>{}</b>.".format(status.upper())
    if comment:
        msg += "<br><p><strong>Comment:</strong> {}</p>".format(comment)
    
    return _html(200, "Thank you for your response", msg)

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
