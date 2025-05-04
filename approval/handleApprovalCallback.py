#!/usr/bin/env python3
import os, json, datetime, boto3
from boto3.dynamodb.conditions import Key, Attr

REGION      = os.environ["AWS_REGION"]
TABLE_NAME  = os.environ["DYNAMODB_TABLE_NAME"]

ddb   = boto3.resource("dynamodb", region_name=REGION)
table = ddb.Table(TABLE_NAME)

HTML = """
<html><body style="font-family:Verdana">
<h2>{title}</h2>
<p>{msg}</p>
</body></html>
"""

def lambda_handler(event, ctx):
    qs     = event.get("queryStringParameters") or {}
    token  = qs.get("token")
    status = qs.get("status")

    if not token or status not in ("approved", "rejected"):
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "text/html"},
            "body": HTML.format(title="Invalid Request", msg="Missing or invalid parameters.")
        }

    try:
        gsis = table.global_secondary_indexes or []
        if any(i.get("IndexName") == "approval_token-index" for i in gsis):
            res = table.query(
                IndexName="approval_token-index",
                KeyConditionExpression=Key("approval_token").eq(token)
            )
        else:
            res = table.scan(
                FilterExpression=Attr("approval_token").eq(token)
            )
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "text/html"},
            "body": HTML.format(title="Lookup Failed", msg=str(e))
        }

    items = res.get("Items", [])
    if not items:
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "text/html"},
            "body": HTML.format(title="Invalid Token", msg="This approval link is invalid or expired.")
        }

    item = items[0]
    key  = {"project_id": item["project_id"], "card_id": item["card_id"]}

    table.update_item(
        Key=key,
        UpdateExpression="SET approval_status = :s, approval_timestamp = :t",
        ExpressionAttributeValues={
            ":s": status,
            ":t": datetime.datetime.utcnow().isoformat()
        }
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": HTML.format(title="Thank you!", msg=f"Your Acta has been {status}.")
    }
