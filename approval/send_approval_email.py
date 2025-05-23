#!/usr/bin/env python3
"""
send_approval_email.py – v1.8

• Accepts only project_id + recipient
• Finds the Client_Email card in DynamoDB → card_id
• Lists S3 “actas/” prefix → picks newest PDF for project_id
• Persists token + pending status to DynamoDB v2
• Sends SES HTML e-mail with Approve/Reject buttons
"""

import os
import uuid
import json
import time
import urllib.parse
import mimetypes
from email.message import EmailMessage
from typing import Optional, Dict, Any

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

# ── ENV ─────────────────────────────────────────────────────────────
REGION        = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME    = os.getenv("DYNAMODB_ENRICHMENT_TABLE") or os.getenv("DYNAMODB_TABLE_NAME")
API_ID        = os.environ["ACTA_API_ID"]
API_STAGE     = os.getenv("API_STAGE", "prod")
EMAIL_SOURCE  = os.environ["EMAIL_SOURCE"]
BUCKET_NAME   = os.environ["S3_BUCKET_NAME"]

API_BASE      = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve"
BRAND_COLOR   = "#1b998b"

# ── AWS CLIENTS ─────────────────────────────────────────────────────
ses = boto3.client("ses", region_name=REGION)
s3  = boto3.client("s3", region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

def build_html(project_name: str,
               approve_url: str,
               reject_url: str,
               comments: Optional[str] = None) -> str:
    btn = ("display:inline-block;padding:10px 26px;margin:4px 6px;"
           "border-radius:4px;font-family:Arial,sans-serif;font-size:15px;"
           "color:#ffffff;text-decoration:none;")
    approve_btn = f'<a href="{approve_url}" style="{btn}background:{BRAND_COLOR};">Approve</a>'
    reject_btn  = f'<a href="{reject_url}"  style="{btn}background:#d9534f;">Reject</a>'

    comment_panel = ""
    if comments:
        comment_panel = f"""
        <tr><td style="padding-top:22px">
          <div style="border:1px solid #e0e0e0;
                      border-left:4px solid {BRAND_COLOR};
                      background:#fafafa;padding:14px;
                      font-family:Arial;font-size:14px;
                      line-height:19px;color:#333;">
            <strong>Last comment</strong><br>{comments}
          </div>
        </td></tr>
        """

    return f"""\
<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#fff">
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
</body></html>"""

def lambda_handler(event: Dict[str,Any], context):
    # 1️⃣ Parse input
    try:
        body       = json.loads(event.get("body", "{}"))
        project_id = body["project_id"]
        recipient  = body["recipient"]
    except (KeyError, json.JSONDecodeError, TypeError):
        return {"statusCode": 400, "body": "Missing / malformed request body"}

    # 2️⃣ Find the Client_Email card
    resp = ddb.query(
        KeyConditionExpression=Key("project_id").eq(project_id),
        ScanIndexForward=False
    )
    client_rows = [i for i in resp.get("Items", []) if i.get("title") == "Client_Email"]
    if not client_rows:
        return {"statusCode": 404,
                "body": f"No Client_Email row for project {project_id}"}
    card_id    = client_rows[0]["card_id"]
    comment_txt = (client_rows[0].get("comments") or [""])[0]

    # 3️⃣ Pick newest PDF from S3
    objs = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix="actas/").get("Contents", [])
    pdfs = [o for o in objs if o["Key"].lower().endswith(".pdf") and project_id in o["Key"]]
    if not pdfs:
        return {"statusCode": 404, "body": f"No Acta PDF for project {project_id}"}
    newest = max(pdfs, key=lambda o: o["LastModified"])
    pdf_key = newest["Key"]

    # 4️⃣ Generate token + update Dynamo
    token = str(uuid.uuid4())
    ddb.update_item(
        Key={"project_id": project_id, "card_id": card_id},
        UpdateExpression="SET approval_token=:t,approval_status=:s,sent_timestamp=:ts",
        ExpressionAttributeValues={
            ":t": token, ":s": "pending", ":ts": int(time.time())
        }
    )

    # 5️⃣ Build URLs
    q_token     = urllib.parse.quote_plus(token)
    base_domain = API_BASE.split("://", 1)[1]
    approve_url = f"https://{base_domain}?token={q_token}&status=approved"
    reject_url  = f"https://{base_domain}?token={q_token}&status=rejected"

    # 6️⃣ Fetch PDF bytes
    try:
        pdf_bytes = s3.get_object(Bucket=BUCKET_NAME, Key=pdf_key)["Body"].read()
    except ClientError as e:
        return {"statusCode": 500,
                "body": f"S3 fetch failed: {e.response['Error']['Message']}"}

    maintype, subtype = mimetypes.guess_type(pdf_key)[0].split("/")

    # 7️⃣ Send e-mail
    msg = EmailMessage()
    proj_name = client_rows[0].get("project", {}).get("name", f"Project {project_id}")
    msg["Subject"] = f"Action required – {proj_name}"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = recipient
    msg.set_content("Please view this email in HTML format.")
    msg.add_alternative(
        build_html(proj_name, approve_url, reject_url, comment_txt),
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
        return {"statusCode": 500,
                "body": f"SES send failed: {e.response['Error']['Message']}"}

    return {"statusCode": 200, "body": json.dumps({"MessageId": res["MessageId"]})}
