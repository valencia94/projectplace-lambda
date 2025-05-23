#!/usr/bin/env python3
"""
send_approval_email.py – v1.6

• Generates approval_token
• Persists token + pending status to DynamoDB v2
• Fetches the *latest* row so we can pull comments + PM name
• Auto-discovers the Acta PDF in S3 for the project_id
• Sends SES HTML e-mail (Outlook-safe buttons + optional comments panel)
"""

import os, uuid, json, base64, urllib.parse, mimetypes, time
import boto3
from email.message import EmailMessage
from botocore.exceptions import ClientError
from decimal import Decimal
from typing import Any, Dict, Optional  

# ── ENV ─────────────────────────────────────────────────────────────
REGION       = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME   = os.getenv("DYNAMODB_ENRICHMENT_TABLE") or os.getenv("DYNAMODB_TABLE_NAME")
API_ID       = os.environ["ACTA_API_ID"]
API_STAGE    = os.getenv("API_STAGE", "prod")
EMAIL_SOURCE = os.environ["EMAIL_SOURCE"]
BUCKET_NAME  = os.environ["S3_BUCKET_NAME"]

API_BASE = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve"
BRAND_COLOR = "#1b998b"

# ── AWS CLIENTS ─────────────────────────────────────────────────────
ses = boto3.client("ses", region_name=REGION)
s3  = boto3.client("s3", region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

# ── HTML builder ────────────────────────────────────────────────────
def build_html(project_name, approve_url, reject_url, comments=None):
    btn_style = ("display:inline-block;padding:10px 26px;margin:4px 6px;"
                 "border-radius:4px;font-family:Arial,sans-serif;font-size:15px;"
                 "color:#ffffff;text-decoration:none;")
    approve_btn = f'<a href="{approve_url}" style="{btn_style}background:{BRAND_COLOR};">Approve</a>'
    reject_btn  = f'<a href="{reject_url}"  style="{btn_style}background:#d9534f;">Reject</a>'
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

def lambda_handler(event, context):
    # 1) pull in what we really need
    try:
        body       = json.loads(event.get("body", event) if isinstance(event.get("body"), str) else event)
        project_id = body["project_id"]
        recipient  = body["recipient"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"statusCode": 400, "body": "Missing / malformed request body"}

    # 2) look up the latest card for that project in Dynamo
    resp = ddb.query(
        KeyConditionExpression=Key("project_id").eq(project_id),
        ScanIndexForward=False,  # newest first
        Limit=1
    )
    items = resp.get("Items", [])
    if not items:
        return {"statusCode": 404, "body": f"No cards found for project {project_id}"}

    item    = items[0]
    card_id = item["card_id"]
    pdf_key = item.get("s3_pdf_path") or item.get("pdf_key")
    if not pdf_key:
        return {"statusCode": 500, "body": "PDF key missing for latest card"}

    # 1️⃣ Persist a new approval_token
    token = str(uuid.uuid4())
    ddb.update_item(
        Key={"project_id": project_id},
        UpdateExpression="SET approval_token=:t, approval_status=:s, sent_timestamp=:ts",
        ExpressionAttributeValues={":t": token, ":s": "pending", ":ts": int(time.time())}
    )
    
    # 2️⃣ Read back latest record to get project name + comments + PDF key
    resp  = ddb.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("project_id").eq(project_id),
        ScanIndexForward=False, Limit=1
    )
    items = resp.get("Items", [])
    if not items:
        return {"statusCode":404, "body":"Project not found in DynamoDB"}
    item = items[0]

    proj_name     = (item.get("project") or {}).get("name", f"Project {project_id}")
    comments      = (item.get("comments") or [])[:1]
    comment_text  = comments[0].get("text","") if comments else None
    pdf_key       = item.get("s3_pdf_path")

    # 3️⃣ If Dynamo didn’t carry a PDF path, try listing S3 for any “<project_id>*.pdf”
    if not pdf_key:
        lst = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=str(project_id))
        for obj in lst.get("Contents", []):
            if obj["Key"].lower().endswith(".pdf"):
                pdf_key = obj["Key"]
                break
    if not pdf_key:
        return {"statusCode":404, "body":"Could not locate Acta PDF in S3"}

    # 4️⃣ Build URLs
    q = urllib.parse.quote_plus(token)
    base = API_BASE.split("://",1)[1]
    approve_url = f"https://{base}?token={q}&status=approved"
    reject_url  = f"https://{base}?token={q}&status=rejected"

    # 5️⃣ Fetch PDF bytes
    try:
        pdf_bytes = s3.get_object(Bucket=BUCKET_NAME, Key=pdf_key)["Body"].read()
    except ClientError as e:
        return {"statusCode":500, "body":f"S3 fetch failed: {e}"}

    maintype, subtype = mimetypes.guess_type(pdf_key)[0].split("/")

    # 6️⃣ Build & send the email
    msg = EmailMessage()
    msg["Subject"]   = f"Action required – {proj_name}"
    msg["From"]      = EMAIL_SOURCE
    msg["To"]        = recipient
    msg.set_content("Please view this email in HTML format.")
    msg.add_alternative(build_html(proj_name, approve_url, reject_url, comment_text),
                        subtype="html")
    msg.add_attachment(pdf_bytes, maintype=maintype, subtype=subtype,
                       filename=os.path.basename(pdf_key))

    try:
        res = ses.send_raw_email(
            Source=EMAIL_SOURCE,
            Destinations=[recipient],
            RawMessage={"Data": msg.as_bytes()}
        )
    except ClientError as e:
        return {"statusCode":500, "body":f"SES send failed: {e.response['Error']['Message']}"}

    return {"statusCode":200, "body": json.dumps({"MessageId":res["MessageId"]})}
