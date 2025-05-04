#!/usr/bin/env python3
"""
Handle /approve?token=...&status=approved|rejected

• Validates token in DynamoDB
• Updates approval_status + timestamp
• Returns simple branded HTML page
"""

import os, json, boto3, datetime
from boto3.dynamodb.conditions import Key

REGION          = os.environ["AWS_REGION"]
TABLE_NAME      = os.environ["DYNAMODB_TABLE_NAME"]

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table    = dynamodb.Table(TABLE_NAME)

HTML = """
<html><body style="font-family:Verdana">
<h2>{title}</h2>
<p>{msg}</p>
</body></html>
"""

def lambda_handler(event, _ctx):
    params = event.get("queryStringParameters") or {}
    token  = params.get("token")
    status = params.get("status")

    if not token or status not in ("approved","rejected"):
        return {"statusCode":400,"headers":{"Content-Type":"text/html"},
                "body":HTML.format(title="Invalid Request", msg="Missing or invalid parameters.")}

    # --- lookup token record ---
    res = table.query(
        IndexName="approval_token-index" if "approval_token-index" in [i["IndexName"] for i in table.global_secondary_indexes or []] else None,
        KeyConditionExpression=Key("approval_token").eq(token)
    ) if "approval_token" in table.key_schema[0]["AttributeName"] else table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr("approval_token").eq(token))

    items = res["Items"]
    if not items:
        return {"statusCode":404,"headers":{"Content-Type":"text/html"},
                "body":HTML.format(title="Invalid Token", msg="This approval link is invalid or expired.")}

    item = items[0]
    pk   = {"project_id": item["project_id"]} if "project_id" in item else {"approval_token": token}

    table.update_item(
        Key = pk,
        UpdateExpression = "SET approval_status = :s, approval_timestamp = :t",
        ExpressionAttributeValues = {
            ":s": status,
            ":t": datetime.datetime.utcnow().isoformat()
        }
    )

    return {"statusCode":200,"headers":{"Content-Type":"text/html"},
            "body":HTML.format(title="Thank you!", msg=f"Your Acta has been {status}.")}
