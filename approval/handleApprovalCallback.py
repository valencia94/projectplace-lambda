#!/usr/bin/env python3
"""
…same header…
"""

import os, datetime
import boto3
from boto3.dynamodb.conditions import Key

AWS_REGION  = os.getenv("AWS_REGION")       or exit("❌ AWS_REGION")
TABLE_NAME  = os.getenv("DYNAMODB_TABLE_NAME") or exit("❌ DYNAMODB_TABLE_NAME")
API_STAGE   = os.getenv("API_STAGE","prod")

ddb = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)

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
    if not token or status not in ("approved","rejected"):
        return {
          "statusCode":400,
          "headers":{"Content-Type":"text/html"},
          "body":HTML.format(
            title="Invalid Request",
            msg="Missing or invalid params"
          )
        }

    # GSI-only lookup, no scan fallback
    resp = ddb.query(
      IndexName="approval_token-index",
      KeyConditionExpression=Key("approval_token").eq(token)
    )
    items = resp.get("Items",[])
    if not items:
        return {
          "statusCode":404,
          "headers":{"Content-Type":"text/html"},
          "body":HTML.format(
            title="Invalid Token",
            msg="Link expired or bad"
          )
        }

    rec = items[0]
    # tighten key schema: include both project_id+card_id
    key = {"project_id": rec["project_id"], "card_id": rec["card_id"]}

    ddb.update_item(
      Key=key,
      UpdateExpression="SET approval_status=:s, approval_timestamp=:t",
      ExpressionAttributeValues={
        ":s": status,
        ":t": datetime.datetime.utcnow().isoformat()
      }
    )

    return {
      "statusCode":200,
      "headers":{"Content-Type":"text/html"},
      "body":HTML.format(
        title="Thank you!",
        msg=f"Your Acta has been <b>{status}</b>."
      )
    }
