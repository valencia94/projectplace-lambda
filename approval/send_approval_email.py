#!/usr/bin/env python3
"""
send_approval_email.py – v1.7

• Generates an approval_token
• Persists token + pending status to DynamoDB v2
• Fetches the *latest* card record for project_id
• Auto-discovers the Acta PDF in S3 if not already stored
• Builds & sends an SES HTML e-mail with Approve/Reject buttons
"""

import os
import uuid
import json
import urllib.parse
import mimetypes
import time
import boto3
from boto3.dynamodb.conditions import Key
from email.message import EmailMessage
from botocore.exceptions import ClientError
from typing import Optional

# ── ENV ─────────────────────────────────────────────────────────────
REGION       = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME   = os.getenv("DYNAMODB_ENRICHMENT_TABLE") or os.getenv("DYNAMODB_TABLE_NAME")
API_ID       = os.environ["ACTA_API_ID"]
API_STAGE    = os.getenv("API_STAGE", "prod")
EMAIL_SOURCE = os.environ["EMAIL_SOURCE"]
BUCKET_NAME  = os.environ["S3_BUCKET_NAME"]

API_BASE   = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve"
BRAND_COLOR = "#1b998b"

# ── AWS CLIENTS ─────────────────────────────────────────────────────
ses = boto3.client("ses", region_name=REGION)
s3  = boto3.client("s3", region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

# ── HTML builder ────────────────────────────────────────────────────
def build_html(project_name: str,
               approve_url: str,
               reject_url: str,
               comments: Optional[str] = None) -> str:
    btn = ("display:inline-block;padding:10px 26px;margin:4px 6px;"
           "border-radius:4px;font-family:Arial,sans-serif;font-size:15px;"
           "color:#ffffff;text-decoration:none;")
    approve_btn = f'<a href="{approve_url}" style="{btn}background:{BRAND_COLOR};">Approve</a>'
    reject_btn  = f'<a href="{reject_url}"  style="{btn}background:#d9534f;">Reject</a>'
    comment_panel = (f"""
      <tr><td style="padding-top:22px">
        <div style="border:1px solid #e0e0e0;border-left:4px solid {BRAND_COLOR};
                    background:#fafafa;padding:14px;font-family:Arial;font-size:14px;
                    line-height:19px;color:#333;">
          <strong>Last comment</strong><br>{comments}
        </div>
      </td></tr>""" if comments else "")

    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#ffffff">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr><td align="center" style="padding:28px 0">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0"
               style="border:1px solid #dddddd;border-radius:6px">
          <tr><td style="background:{BRAND_COLOR};padding:20px 24px;
                         font-family:Arial;font-size:20px;color:#ffffff;
                         border-top-left-radius:6px;border-top-right-radius:6px">
              Project Acta ready for review
          </td></tr>
          <tr><td style="padding:24px;font-family:Arial;font-size:15px;
                         line-height:21px;color:#333">
              Hi there,<br><br>
              Please review the Acta for <strong>{project_name}</strong> and choose:
          </td></tr>
          <tr><td align="center" style="padding:4px 0 18px 0">
              {approve_btn}{reject_btn}
          </td></tr>
          {comment_panel}
          <tr><td style="padding:24px;font-family:Arial;font-size:12px;
                         color:#888;border-top:1px solid #eeeeee">
              CVDex Tech Solutions — empowering excellence through automation
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""

# ── HANDLER ─────────────────────────────────────────────────────────
def lambda_handler(event, context):
    # 1️⃣ Parse inputs
    try:
        body = (json.loads(event["body"])
                if isinstance(event.get("body"), str) else event)
        project_id = body["project_id"]
        recipient  = body["recipient"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"statusCode": 400, "body": "Missing / malformed request body"}

    # 2️⃣ Get the latest card for this project
    resp = ddb.query(
        KeyConditionExpression=Key("project_id").eq(project_id),
        ScanIndexForward=False,
        Limit=1
    )
    items = resp.get("Items", [])
    if not items:
        return {"statusCode": 404, "body": f"No cards found for project {project_id}"}

    item    = items[0]
    card_id = item["card_id"]

    # 3️⃣ Generate approval token & mark pending
    token = str(uuid.uuid4())
    ddb.update_item(
        Key={"project_id": project_id, "card_id": card_id},
        UpdateExpression="SET approval_token=:t, approval_status=:s, sent_timestamp=:ts",
        ExpressionAttributeValues={
            ":t": token,
            ":s": "pending",
            ":ts": int(time.time())
        }
    )

    # 4️⃣ Pull back the same record (to get project name + comments + any stored PDF path)
    resp = ddb.get_item(Key={"project_id": project_id, "card_id": card_id})
    item = resp.get("Item", {})

    proj_name    = (item.get("project") or {}).get("name", f"Project {project_id}")
    comments     = item.get("comments") or []
    comment_text = comments[0].get("text","") if comments else None
    pdf_key      = item.get("s3_pdf_path") or item.get("pdf_key")

    # 5️⃣ Auto-discover PDF in S3 if not in Dynamo
    if not pdf_key:
        prefix = str(project_id)
        resp_list = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)
        for o in resp_list.get("Contents", []):
            if o["Key"].lower().endswith(".pdf"):
                pdf_key = o["Key"]
                break
    if not pdf_key:
        return {"statusCode": 500, "body": "PDF key missing for latest card"}

    # 6️⃣ Build approval / reject URLs
    q = urllib.parse.quote_plus(token)
    base = API_BASE.split("://",1)[1]
    approve_url = f"https://{base}?token={q}&status=approved"
    reject_url  = f"https://{base}?token={q}&status=rejected"

    # 7️⃣ Fetch the PDF bytes
    try:
        pdf_bytes = s3.get_object(Bucket=BUCKET_NAME, Key=pdf_key)["Body"].read()
    except ClientError as e:
        return {"statusCode": 500, "body": f"S3 fetch failed: {e.response['Error']['Message']}"}

    maintype, subtype = mimetypes.guess_type(pdf_key)[0].split("/")

    # 8️⃣ Build & send the email
    msg = EmailMessage()
    msg["Subject"] = f"Action required – {proj_name}"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = recipient
    msg.set_content("Please view this email in HTML format.")
    msg.add_alternative(
        build_html(proj_name, approve_url, reject_url, comment_text),
        subtype="html"
    )
    msg.add_attachment(
        pdf_bytes,
        maintype=maintype,
        subtype=subtype,
        filename=os.path.basename(pdf_key)
    )

    try:
        res = ses.send_raw_email(
            Source=EMAIL_SOURCE,
            Destinations=[recipient],
            RawMessage={"Data": msg.as_bytes()}
        )
    except ClientError as e:
        return {"statusCode": 500,
                "body": f"SES send failed: {e.response['Error']['Message']}"}

    return {"statusCode": 200,
            "body": json.dumps({"MessageId": res["MessageId"]})}
