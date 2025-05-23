#!/usr/bin/env python3
"""
send_approval_email.py   ·   v2.0   (2025-05-23)

WHAT IT DOES
────────────
• Expects only two inputs:  project_id   &   recipient   (JSON body)
• Finds the newest Acta PDF in S3 whose key contains the project_id
• Finds the *Client_Email* card row to get a stable card_id
• Generates a UUID token, writes status-pending back into DynamoDB
• Builds branded HTML + Approve / Reject links (API Gateway)
• Sends the e-mail through SES with the PDF attached
"""

import os, json, uuid, time, urllib.parse, mimetypes
from typing import Any, Dict, Optional, List

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from email.message import EmailMessage

# ── ENV ──────────────────────────────────────────────────────────────
REGION        = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME    = os.getenv("DYNAMODB_ENRICHMENT_TABLE") or os.getenv("DYNAMODB_TABLE_NAME")
BUCKET_NAME   = os.environ["S3_BUCKET_NAME"]
EMAIL_SOURCE  = os.environ["EMAIL_SOURCE"]
API_ID        = os.environ["ACTA_API_ID"]
API_STAGE     = os.getenv("API_STAGE", "prod")

API_BASE  = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve"
GREEN     = "#1b998b"          # brand colour

# ── AWS CLIENTS ──────────────────────────────────────────────────────
ses  = boto3.client("ses",  region_name=REGION)
s3   = boto3.client("s3",   region_name=REGION)
ddb  = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

# ── HTML helper ──────────────────────────────────────────────────────
def build_html(project: str, approve_url: str, reject_url: str,
               last_comment: Optional[str] = None) -> str:
    base_btn = ("display:inline-block;padding:10px 24px;margin:4px;"
                "border-radius:4px;font-family:Arial;font-size:15px;color:#fff;text-decoration:none;")
    approve = f'<a href="{approve_url}" style="{base_btn}background:{GREEN}">Approve</a>'
    reject  = f'<a href="{reject_url}"  style="{base_btn}background:#d9534f">Reject</a>'

    comment_html = (f"""
      <div style="border:1px solid #e0e0e0;border-left:4px solid {GREEN};
                   background:#fafafa;padding:12px;margin-top:24px">
        <strong>Latest comment</strong><br>{last_comment}
      </div>""" if last_comment else "")

    return f"""
<!DOCTYPE html>
<html><body style="font-family:Arial,Helvetica,sans-serif;margin:0;padding:24px">
  <h2 style="color:{GREEN};margin-top:0">Project Acta ready for review</h2>
  <p>Please review the attached Acta for <strong>{project}</strong> and choose an option:</p>
  {approve}  {reject}
  {comment_html}
  <p style="font-size:12px;color:#777;margin-top:32px">
    CVDex Tech Solutions — empowering excellence through automation
  </p>
</body></html>"""

# ── HANDLER ──────────────────────────────────────────────────────────
def lambda_handler(event: Dict[str,Any], _ctx):
    # 1⃣  Parse body  (either Lambda-Proxy or direct invoke)
    try:
        body       = json.loads(event["body"]) if isinstance(event.get("body"), str) else event
        project_id = str(body["project_id"])
        recipient  = str(body["recipient"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"statusCode": 400, "body": "Missing project_id or recipient"}

    # 2⃣  Look up the *Client_Email* row => gives stable card_id & (optionally) comment
    rows = ddb.query(KeyConditionExpression=Key("project_id").eq(project_id))["Items"]
    if not rows:
        return {"statusCode": 404, "body": f"Project {project_id} not found in DynamoDB"}

    client_rows = [r for r in rows if r.get("title") == "Client_Email"]
    card_row    = client_rows[0] if client_rows else rows[0]           # fallback: 1st row
    card_id     = card_row["card_id"]
    last_comment = (card_row.get("comments") or [""])[0][:250]

    # 3⃣  Find newest PDF in S3  →  prefix match + newest LastModified
    try:
        listing = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=str(project_id))
        pdf_objs: List[Dict[str,Any]] = [o for o in listing.get("Contents", [])
                                         if o["Key"].lower().endswith(".pdf")]
        if not pdf_objs:
            return {"statusCode": 404, "body": f"No PDF found for {project_id} in S3"}
        pdf_key = max(pdf_objs, key=lambda o: o["LastModified"])["Key"]
        pdf_bytes = s3.get_object(Bucket=BUCKET_NAME, Key=pdf_key)["Body"].read()
    except ClientError as e:
        return {"statusCode": 500, "body": f"S3 error: {e.response['Error']['Message']}"}

    # 4⃣  Token + status update
    token = str(uuid.uuid4())
    ddb.update_item(
        Key={"project_id": project_id, "card_id": card_id},
        UpdateExpression="SET approval_token=:t, approval_status=:s, sent_timestamp=:ts",
        ExpressionAttributeValues={":t": token, ":s": "pending", ":ts": int(time.time())}
    )

    # 5⃣  Build URLs
    safe = urllib.parse.quote_plus(token)
    approve_url = f"{API_BASE}?token={safe}&status=approved"
    reject_url  = f"{API_BASE}?token={safe}&status=rejected"

    # 6⃣  Construct & send e-mail
    maintype, subtype = mimetypes.guess_type(pdf_key)[0].split("/")

    msg = EmailMessage()
    msg["Subject"] = "Action required – Project Acta approval"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = recipient
    msg.set_content("Please view this e-mail in HTML.")
    msg.add_alternative(build_html(project_id, approve_url, reject_url, last_comment),
                        subtype="html")
    msg.add_attachment(pdf_bytes, maintype=maintype, subtype=subtype,
                       filename=os.path.basename(pdf_key))

    try:
        ses.send_raw_email(Source=EMAIL_SOURCE,
                           Destinations=[recipient],
                           RawMessage={"Data": msg.as_bytes()})
    except ClientError as e:
        return {"statusCode": 500,
                "body": f"SES send failed: {e.response['Error']['Message']}"}

    return {"statusCode": 200, "body": json.dumps({"ok": True, "pdf": pdf_key})}
