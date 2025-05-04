#!/usr/bin/env python3
import os, json, boto3

# ─── ENVIRONMENT ────────────────────────────────────────────────
REGION   = os.environ["AWS_REGION"]
TABLE    = boto3.resource("dynamodb", region_name=REGION) \
                 .Table(os.environ["DYNAMODB_TABLE_NAME"])

def lambda_handler(event, context):
    qs = event.get("queryStringParameters") or {}
    token = qs.get("token")
    if not token:
        return {"statusCode": 400, "body": "Missing token"}

    # 1) Find the item by token
    resp = TABLE.scan(
        FilterExpression="approval_token = :t",
        ExpressionAttributeValues={":t": token},
        ProjectionExpression="project_id, card_id"
    )
    items = resp.get("Items", [])
    if not items:
        return {"statusCode": 404, "body": "Token not found"}

    item = items[0]
    pid, cid = item["project_id"], item["card_id"]

    # 2) Update its approval_status
    status = "approved" if event["httpMethod"] == "GET" else "rejected"
    TABLE.update_item(
        Key={                  # ← BOTH keys required
          "project_id": pid,
          "card_id":    cid
        },
        UpdateExpression="SET approval_status = :s",
        ExpressionAttributeValues={":s": status}
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": f"<h1>Acta {status.capitalize()}</h1>"
    }
