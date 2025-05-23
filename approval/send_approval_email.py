#!/usr/bin/env python3
"""
send_approval_email.py – v1.7

• Accepts only project_id + recipient
• Finds the latest Acta PDF (from Dynamo or S3)
• Generates a one-time token and marks “pending” in DynamoDB
• Builds branded HTML with Approve / Reject buttons
• Sends via SES (raw email + PDF attachment)
"""

import os
import json
import uuid
import time
import urllib.parse
import mimetypes
import boto3
from boto3.dynamodb.conditions import Key
from email.message import EmailMessage
from botocore.exceptions import ClientError

# ── CONFIG ─────────────────────────────────────────────────────────────
REGION        = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME    = os.getenv("DYNAMODB_ENRICHMENT_TABLE", os.getenv("DYNAMODB_TABLE_NAME"))
API_ID        = os.environ["ACTA_API_ID"]
API_STAGE     = os.getenv("API_STAGE", "prod")
EMAIL_SOURCE  = os.environ["EMAIL_SOURCE"]
BUCKET_NAME   = os.environ["S3_BUCKET_NAME"]
API_BASE_HOST = f"{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve"
BRAND_COLOR   = "#1b998b"

# ── AWS CLIENTS ─────────────────────────────────────────────────────────
ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
s3  = boto3.client("s3", region_name=REGION)
ses = boto3.client("ses", region_name=REGION)

# ── HTML builder ────────────────────────────────────────────────────────
def build_html(project_name: str, approve_url: str, reject_url: str, comments: str = None) -> str:
    btn = ("display:inline-block;padding:10px 26px;margin:4px;"
           "border-radius:4px;font-family:Arial,sans-serif;font-size:15px;"
           "color:#ffffff;text-decoration:none;")
    approve_btn = (
        f'<a href="{approve_url}" target="_blank" '
        f'style="{btn}background:{BRAND_COLOR};">Approve</a>'
    )
    reject_btn = (
        f'<a href="{reject_url}" target="_blank" '
        f'style="{btn}background:#d9534f;">Reject</a>'
    )
    comment_panel = (
        f"""
        <tr><td style="padding-top:22px">
          <div style="border:1px solid #e0e0e0;border-left:4px solid {BRAND_COLOR};
                      background:#fafafa;padding:14px;font-family:Arial;font-size:14px;
                      line-height:19px;color:#333;">
            <strong>Last comment</strong><br>{comments}
          </div>
        </td></tr>
        """
        if comments else ""
    )

    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#fff">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr><td align="center" style="padding:28px 0">
        <table width="600" cellpadding="0" cellspacing="0"
               style="border:1px solid #ddd;border-radius:6px">
          <tr><td style="background:{BRAND_COLOR};padding:20px 24px;
                         font-family:Arial;font-size:20px;color:#fff">
              Project Acta ready for review
          </td></tr>
          <tr><td style="padding:24px;font-family:Arial;font-size:15px;color:#333">
              Hi there,<br><br>
              Please review the Acta for <strong>{project_name}</strong> and choose:
          </td></tr>
          <tr><td align="center" style="padding:4px 0 18px 0">
              {approve_btn}{reject_btn}
          </td></tr>
          {comment_panel}
          <tr><td style="padding:24px;font-family:Arial;font-size:12px;color:#888;
                         border-top:1px solid #eee">
              CVDex Tech Solutions — empowering excellence through automation
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""

# ── HANDLER ─────────────────────────────────────────────────────────────
def lambda_handler(event, context):
    # 1️⃣ Parse inputs
    try:
        payload    = json.loads(event.get("body","{}")) if isinstance(event.get("body"), str) else event
        project_id = payload["project_id"]
        recipient  = payload["recipient"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"statusCode": 400, "body": "Missing project_id or recipient"}

    # 2️⃣ Find latest PDF key
    pdf_key = None
    # a) try DynamoDB “s3_pdf_path” on latest card
    resp = ddb.query(
        KeyConditionExpression=Key("project_id").eq(project_id),
        ScanIndexForward=False,
        Limit=1
    )
    items = resp.get("Items", [])
    if items and items[0].get("s3_pdf_path"):
        pdf_key = items[0]["s3_pdf_path"]
        card_id = items[0]["card_id"]
    else:
        # b) fallback to S3 listing
        listing = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=str(project_id))
        for obj in listing.get("Contents", []):
            if obj["Key"].lower().endswith(".pdf"):
                pdf_key = obj["Key"]
                # we’ll reuse card_id from Dynamo if available
                card_id = items[0]["card_id"] if items else None
                break

    if not pdf_key:
        return {"statusCode": 404, "body": "Could not locate Acta PDF"}

    # 3️⃣ Generate approval_token & mark pending
    token = str(uuid.uuid4())
    ddb.update_item(
        Key={"project_id": project_id, "card_id": card_id},
        UpdateExpression="SET approval_token=:t, approval_status=:s, sent_timestamp=:ts",
        ExpressionAttributeValues={
            ":t": token, ":s": "pending", ":ts": int(time.time())
        }
    )

    # 4️⃣ Read back for project name + (optional) comment
    resp  = ddb.get_item(Key={"project_id":project_id, "card_id":card_id})
    item  = resp.get("Item", {})
    proj_name    = (item.get("project") or {}).get("name", f"Project {project_id}")
    comments_arr = item.get("comments") or []
    last_comment = comments_arr[0].get("text","") if comments_arr else None

    # 5️⃣ Build URLs
    q           = urllib.parse.quote_plus(token)
    approve_url = f"https://{API_BASE_HOST}?token={q}&status=approved"
    reject_url  = f"https://{API_BASE_HOST}?token={q}&status=rejected"

    # 6️⃣ Fetch PDF bytes
    try:
        pdf_bytes = s3.get_object(Bucket=BUCKET_NAME, Key=pdf_key)["Body"].read()
    except ClientError as e:
        return {"statusCode": 500, "body": f"S3 fetch failed: {e}"}

    maintype, subtype = mimetypes.guess_type(pdf_key)[0].split("/")

    # 7️⃣ Build & send email
    msg = EmailMessage()
    msg["Subject"] = f"Action required – {proj_name}"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = recipient
    msg.set_content("Please view this email in HTML format.")
    msg.add_alternative(
        build_html(proj_name, approve_url, reject_url, last_comment),
        subtype="html"
    )
    msg.add_attachment(pdf_bytes, maintype=maintype, subtype=subtype,
                       filename=os.path.basename(pdf_key))

    try:
        res = ses.send_raw_email(
            Source=EMAIL_SOURCE,
            Destinations=[recipient],
            RawMessage={"Data": msg.as_bytes()}
        )
    except ClientError as e:
        return {"statusCode": 500, "body": f"SES send failed: {e.response['Error']['Message']}"}

    return {"statusCode": 200, "body": json.dumps({"MessageId": res["MessageId"]})}
