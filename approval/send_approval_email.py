#!/usr/bin/env python3
"""
send_approval_email.py  –  v1.5  (2025-05-11)

• Generates approval_token
• Persists token + pending status to DynamoDB v2
• Fetches the *latest* row so we can pull comments + PM name
• Sends SES HTML e-mail (Outlook-safe buttons + optional comments panel)
• Attaches the Acta PDF
"""

import os, uuid, json, base64, urllib.parse, mimetypes, time
import boto3
from email.message import EmailMessage
from botocore.exceptions import ClientError
from decimal import Decimal
from typing import Any, Dict

# ── ENV ─────────────────────────────────────────────────────────────
REGION        = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME    = (os.getenv("DYNAMODB_ENRICHMENT_TABLE")        # ← v2 (live)
                 or os.getenv("DYNAMODB_TABLE_NAME"))          # ← fallback
API_ID        = os.getenv("ACTA_API_ID")
API_STAGE     = os.getenv("API_STAGE", "prod")
EMAIL_SOURCE  = os.environ["EMAIL_SOURCE"]
BUCKET_NAME   = os.environ["S3_BUCKET_NAME"]

API_BASE = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve"
BRAND_COLOR = "#1b998b"             # Ikusi green

# ── CLIENTS ─────────────────────────────────────────────────────────
ses   = boto3.client("ses",  region_name=REGION)
s3    = boto3.client("s3",   region_name=REGION)
ddb   = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

# ── HTML builder (single function keeps code in one file) ───────────
def build_html(project_name: str,
               approve_url: str,
               reject_url: str,
               comments: str | None) -> str:
    btn = ("display:inline-block;padding:10px 26px;margin:4px 6px;"
           "border-radius:4px;font-family:Arial,sans-serif;font-size:15px;"
           "color:#ffffff;text-decoration:none;")
    approve_btn = (f'<a href="{approve_url}" target="_blank" rel="noopener noreferrer" '
                   f'style="{btn}background:{BRAND_COLOR};">Approve</a>')
    reject_btn  = (f'<a href="{reject_url}"  target="_blank" rel="noopener noreferrer" '
                   f'style="{btn}background:#d9534f;">Reject</a>')

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
              Please review the attached Acta for<br>
              <strong>{project_name}</strong> and choose an option:
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
    try:
        body  = (json.loads(event["body"]) if isinstance(event.get("body"), str)
                 else event)
        project_id = body["project_id"]
        card_id    = body["card_id"]
        recipient  = body["recipient"]
        pdf_key    = body["pdf_key"]
    except (KeyError, json.JSONDecodeError, TypeError):
        return {"statusCode": 400, "body": "Missing / malformed request body"}

    # 1️⃣  generate token + mark row pending
    token = str(uuid.uuid4())
    ddb.update_item(
        Key={"project_id": project_id, "card_id": card_id},
        UpdateExpression="SET approval_token=:t, approval_status=:s, "
                         "sent_timestamp=:ts",
        ExpressionAttributeValues={":t": token, ":s": "pending", ":ts": int(time.time())}
    )

    # 2️⃣  read back row to pull project name + first comment
    resp = ddb.get_item(Key={"project_id": project_id, "card_id": card_id})
    item: Dict[str,Any] = resp.get("Item", {})
    proj_name = (item.get("project", {}) or {}).get("name", f"Project {project_id}")

    comment_preview = ""
    if item.get("comments"):
        comment_preview = (item["comments"][0].get("text",""))[:250]

    # 3️⃣  URLs (explicit https:// so SafeLinks keeps query string)
    q = urllib.parse.quote_plus(token)
    approve_url = f"https://{API_BASE.split('://')[1]}?token={q}&status=approved"
    reject_url  = f"https://{API_BASE.split('://')[1]}?token={q}&status=rejected"

    # 4️⃣  fetch PDF
    pdf_bytes = s3.get_object(Bucket=BUCKET_NAME, Key=pdf_key)["Body"].read()
    maintype, subtype = mimetypes.guess_type(pdf_key)[0].split("/")

    # 5️⃣  build & send e-mail
    msg = EmailMessage()
    msg["Subject"] = "Action required – Project Acta approval"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = recipient
    msg.set_content("Please view HTML e-mail.")
    msg.add_alternative(
        build_html(proj_name, approve_url, reject_url, comment_preview),
        subtype="html"
    )
    msg.add_attachment(pdf_bytes, maintype=maintype, subtype=subtype,
                       filename=os.path.basename(pdf_key))

    try:
        res = ses.send_raw_email(Source=EMAIL_SOURCE,
                                 Destinations=[recipient],
                                 RawMessage={"Data": msg.as_bytes()})
    except ClientError as e:
        return {"statusCode": 500,
                "body": f"SES send failed: {e.response['Error']['Message']}"}

    return {"statusCode": 200,
            "body": json.dumps({"MessageId": res["MessageId"]})}
