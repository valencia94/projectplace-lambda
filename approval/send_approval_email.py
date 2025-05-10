#!/usr/bin/env python3
"""
send_approval_email.py
─────────────────────────────────────────────────────────────
• Generates an approval_token (UUID-4)  
• Persists token + “pending” status to DynamoDB  
• Sends a branded email (HTML) via SES with Approve / Reject links  
• Attaches the Acta PDF stored in S3

ENV VARS (must exist in Lambda or GitHub secrets → workflow)
─────────────────────────────────────────────────────────────
AWS_REGION              us-east-2 (falls back to boto3 default)
ACTA_API_ID             4r0pt34gx4
API_STAGE               prod   (optional, default = prod)
EMAIL_SOURCE            AutomationSolutionsCenter@cvdexinfo.com
DYNAMODB_TABLE_NAME     ProjectPlace_DataExtractor_landing_table_v3
S3_BUCKET_NAME          projectplace-dv-2025-x9a7b

import os, uuid, json, base64, urllib.parse, mimetypes
import boto3
from email.message import EmailMessage
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# ─── ENV ────────────────────────────────────────────────────────────
REGION        = os.getenv("AWS_REGION", boto3.Session().region_name)
API_ID        = os.getenv("ACTA_API_ID", "")
API_STAGE     = os.getenv("API_STAGE", "prod")
EMAIL_SOURCE  = os.environ["EMAIL_SOURCE"]
TABLE_NAME = (os.getenv("DYNAMODB_ENRICHMENT_TABLE")    # ← preferred (v2)
              or os.getenv("DYNAMODB_TABLE_NAME"))      # ← fallback (v3
BUCKET_NAME   = os.environ["S3_BUCKET_NAME"]

# ─── CLIENTS ────────────────────────────────────────────────────────
ses   = boto3.client("ses", region_name=REGION)
s3    = boto3.client("s3", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)

# ─── CONST ──────────────────────────────────────────────────────────
API_BASE = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve"
BRAND_COLOR = "#4AC795"          # Ikusi green

def build_html(recipient_name: str, approve_url: str, reject_url: str) -> str:
    """Returns a simple, brand-coloured HTML template."""
    return f"""
    <html>
      <body style="font-family:Verdana; line-height:1.5">
        <h2 style="color:{BRAND_COLOR}; margin-bottom:0">Project Acta for your review</h2>
        <p>Hi {recipient_name or 'there'},</p>
        <p>
          Please review the attached Acta and click one of the buttons below
          to let us know your decision.<br>
          <small>(If no response is received within 5 days, the Acta will be auto-approved.)</small>
        </p>
        <a href="{approve_url}" style="background:{BRAND_COLOR};color:white;
           padding:10px 18px; text-decoration:none; border-radius:4px;">Approve</a>
        &nbsp;
        <a href="{reject_url}" style="background:#d9534f;color:white;
           padding:10px 18px; text-decoration:none; border-radius:4px;">Reject</a>
        <p style="margin-top:32px;font-size:12px;color:#888">
          CVDex Tech Solutions – delivering excellence through automation
        </p>
      </body>
    </html>
    """

def lambda_handler(event, context):  # noqa: C901 – keep single handler for Lambda
    # Expected: { "project_id": "...", "card_id": "...", "recipient": "foo@bar.com", "pdf_key": "..." }
    try:
        payload = json.loads(event["body"]) if isinstance(event.get("body"), str) else event
        project_id  = payload["project_id"]
        card_id     = payload["card_id"]
        recipient   = payload["recipient"]
        pdf_key     = payload["pdf_key"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"statusCode": 400, "body": "Missing or malformed request body"}

    # ── 1. Generate & persist token ─────────────────────────────────
    approval_token = str(uuid.uuid4())
    table.update_item(
        Key={"project_id": project_id, "card_id": card_id},
        UpdateExpression="SET approval_token=:t, approval_status=:s",
        ExpressionAttributeValues={":t": approval_token, ":s": "pending"}
    )

    # ── 2. Build signed URLs ───────────────────────────────────────
    safe_token = urllib.parse.quote_plus(approval_token)
    approve_url = f"{API_BASE}?token={safe_token}&status=approved"
    reject_url  = f"{API_BASE}?token={safe_token}&status=rejected"

    # ── 3. Fetch PDF from S3 and create e-mail ─────────────────────
    obj = s3.get_object(Bucket=BUCKET_NAME, Key=pdf_key)
    pdf_bytes = obj["Body"].read()

    msg = EmailMessage()
    msg["Subject"] = "Action required – Project Acta ready for approval"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = recipient
    msg.set_content("HTML e-mail required. If you see this, please view in an HTML-capable client.")
    msg.add_alternative(build_html(None, approve_url, reject_url), subtype="html")

    maintype, subtype = mimetypes.guess_type(pdf_key)[0].split("/")
    msg.add_attachment(pdf_bytes, maintype=maintype, subtype=subtype,
                       filename=os.path.basename(pdf_key))

    # ── 4. Send via SES ────────────────────────────────────────────
    try:
        res = ses.send_raw_email(
            Source=EMAIL_SOURCE,
            Destinations=[recipient],
            RawMessage={"Data": msg.as_bytes()}
        )
    except ClientError as e:
        return {"statusCode": 500, "body": f"SES send failed: {e.response['Error']['Message']}"}

    return {"statusCode": 200, "body": json.dumps({"MessageId": res["MessageId"]})}
