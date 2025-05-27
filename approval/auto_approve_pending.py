#!/usr/bin/env python3
"""
Marks every Acta stuck in ‘pending’ for ≥ 7 days as ‘auto-approved’.
Triggered daily by EventBridge.
"""

import os, boto3, datetime
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError          # ← NEW

REGION = os.environ["AWS_REGION"]                    # auto-injected by Lambda
TABLE  = os.environ["DYNAMODB_ENRICHMENT_TABLE"]

ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
NOW     = datetime.datetime.utcnow()
CUTOFF  = NOW - datetime.timedelta(days=7)           # 7-day rule

def _pending_items() -> list[dict]:
    """Query GSI if present, else full scan."""
    try:
        return ddb.query(
            IndexName="approval_status-index",
            KeyConditionExpression=Key("approval_status").eq("pending")
        ).get("Items", [])
    except ClientError as e:
        if e.response["Error"]["Code"] != "ValidationException":
            raise                                              # genuine failure
        # GSI missing → fall back to scan
        return ddb.scan(
            FilterExpression=Attr("approval_status").eq("pending")
        ).get("Items", [])

def lambda_handler(event, _ctx):
    items = _pending_items()
    auto_count = 0
    for row in items:
        ts = row.get("sent_timestamp")
        if not ts:
            continue
        sent_dt = datetime.datetime.fromisoformat(ts.rstrip("Z"))
        if sent_dt <= CUTOFF:
            ddb.update_item(
                Key={"project_id": row["project_id"], "card_id": row["card_id"]},
                UpdateExpression=("SET approval_status = :s, "
                                  "approval_timestamp = :ts, "
                                  "approval_comment  = :c"),
                ExpressionAttributeValues={
                    ":s": "auto-approved",
                    ":ts": NOW.isoformat() + "Z",
                    ":c": "Automatically approved after 7 days"
                }
            )
            auto_count += 1

    return {"statusCode": 200, "body": f"{auto_count} records auto-approved"}
