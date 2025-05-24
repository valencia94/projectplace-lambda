#!/usr/bin/env python3
"""
handle_approval_callback.py  –  v1.2
"""

import os, json, urllib.parse, datetime
import boto3
from typing import Any, Dict

REGION      = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME  = os.environ["DYNAMODB_ENRICHMENT_TABLE"]
ddb         = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

HTML_OK = """<!doctype html><html><body
  style="font-family:Arial;text-align:center;padding:48px">
  <h2 style="color:#1b998b">Thank you – your decision was recorded.</h2>
  {comment_block}
  <p style="font-size:13px;color:#888">You may now close this tab.</p>
</body></html>"""

HTML_ERR = """<!doctype html><html><body
  style="font-family:Arial;text-align:center;padding:48px">
  <h2 style="color:#d9534f">Sorry – that link is invalid or expired.</h2>
</body></html>"""

def lambda_handler(event: Dict[str,Any], _ctx):
    qs = urllib.parse.parse_qs(event["queryStringParameters"] or "")
    token  = qs.get("token",  [""])[0]
    status = qs.get("status", [""])[0]          # approved / rejected
    comment = (qs.get("comment", [""])[0]).strip()

    # simple lookup by GSI (token)
    resp = ddb.query(
        IndexName="approval_token-index",
        KeyConditionExpression=boto3.dynamodb.conditions.Key("approval_token").eq(token),
        Limit=1
    )
    if not resp["Items"]:
        return {"statusCode": 400, "headers":{"Content-Type":"text/html"}, "body": HTML_ERR}

    row = resp["Items"][0]

    ddb.update_item(
        Key={"project_id": row["project_id"], "card_id": row["card_id"]},
        UpdateExpression="SET approval_status=:s, approval_timestamp=:ts"
                         + (", approval_comment=:c" if comment else ""),
        ExpressionAttributeValues={
            ":s": status,
            ":ts": datetime.datetime.utcnow().isoformat(timespec="seconds"),
            **({":c": comment} if comment else {})
        }
    )

    c_block = (f'<p style="border-left:4px solid #1b998b;padding:12px;background:#f7f7f7">'
               f"<em>Your comment:</em><br>{urllib.parse.unquote_plus(comment)}</p>"
               if comment else "")
    return {"statusCode": 200,
            "headers": {"Content-Type": "text/html"},
            "body": HTML_OK.format(comment_block=c_block)}

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
