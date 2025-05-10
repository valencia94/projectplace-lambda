#!/usr/bin/env python3
"""
send_approval_email.py  –  v1.4  (2025-05-10)

• Generates an approval_token (UUID-4)
• Persists token + pending status to DynamoDB (Table v2)
• Sends SES HTML e-mail with Approve / Reject buttons
• Attaches the Acta PDF fetched from S3

Handler string in Lambda console:  send_approval_email.lambda_handler
"""

import os, uuid, json, base64, urllib.parse, mimetypes
import boto3
from email.message import EmailMessage
from botocore.exceptions import ClientError

# ─────────────────────────  ENV  ─────────────────────────
REGION        = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME    = (os.getenv("DYNAMODB_ENRICHMENT_TABLE")        # ← v2 (live)
                 or os.getenv("DYNAMODB_TABLE_NAME"))          # ← fallback
API_ID        = os.getenv("ACTA_API_ID", "")
API_STAGE     = os.getenv("API_STAGE", "prod")
EMAIL_SOURCE  = os.environ["EMAIL_SOURCE"]
BUCKET_NAME   = os.environ["S3_BUCKET_NAME"]

# ────────────────────────  CLIENTS  ───────────────────────
ses   = boto3.client("ses", region_name=REGION)
s3    = boto3.client("s3", region_name=REGION)
ddb   = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

API_BASE = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve"
BRAND_COLOR = "#4AC795"      # Ikusi green

# ───────────────────────  HELPERS  ────────────────────────
def build_html(name: str, approve_url: str, reject_url: str) -> str:
    return f"""
    <html><body style="font-family:Verdana;line-height:1.5">
      <h2 style="color:{BRAND_COLOR};margin:0 0 .5em">Project Acta ready for review</h2>
      <p>Hi {name or 'there'},</p>
      <p>Please review the attached Acta and choose an option:</p>
      <a href="{approve_url}" style="background:{BRAND_COLOR};color:#fff;
         padding:10px 18px;text-decoration:none;border-radius:4px">Approve</a>
      &nbsp;
      <a href="{reject_url}" style="background:#d9534f;color:#fff;
         padding:10px 18px;text-decoration:none;border-radius:4px">Reject</a>
      <p style="margin-top:32px;font-size:12px;color:#888">
        CVDex Tech Solutions – empowering excellence through automation
      </p>
    </body></html>"""

# ───────────────────────  HANDLER  ────────────────────────
def lambda_handler(event, context):
    try:                        # accept JSON either raw or APIGW body
        body = (json.loads(event["body"]) if isinstance(event.get("body"), str)
                else event)
        project_id = body["project_id"]
        card_id    = body["card_id"]
        recipient  = body["recipient"]
        pdf_key    = body["pdf_key"]
    except (KeyError, json.JSONDecodeError, TypeError):
        return {"statusCode": 400, "body": "Missing / malformed request body"}

    # 1️⃣ token + Dynamo write
    token = str(uuid.uuid4())
    ddb.update_item(
        Key={"project_id": project_id, "card_id": card_id},
        UpdateExpression="SET approval_token=:t, approval_status=:s",
        ExpressionAttributeValues={":t": token, ":s": "pending"}
    )

    # 2️⃣ URLs
    safe = urllib.parse.quote_plus(token)
    approve_url = f"{API_BASE}?token={safe}&status=approved"
    reject_url  = f"{API_BASE}?token={safe}&status=rejected"

    # 3️⃣ fetch PDF
    pdf_bytes = s3.get_object(Bucket=BUCKET_NAME, Key=pdf_key)["Body"].read()
    maintype, subtype = mimetypes.guess_type(pdf_key)[0].split("/")

    # 4️⃣ e-mail
    msg = EmailMessage()
    msg["Subject"] = "Action required – Project Acta approval"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = recipient
    msg.set_content("Please view HTML e-mail.")
    msg.add_alternative(build_html(None, approve_url, reject_url), subtype="html")
    msg.add_attachment(pdf_bytes, maintype=maintype, subtype=subtype,
                       filename=os.path.basename(pdf_key))

    try:
        res = ses.send_raw_email(Source=EMAIL_SOURCE,
                                 Destinations=[recipient],
                                 RawMessage={"Data": msg.as_bytes()})
    except ClientError as e:
        return {"statusCode": 500,
                "body": f"SES send failed: {e.response['Error']['Message']}"}

    return {"statusCode": 200, "body": json.dumps({"MessageId": res["MessageId"]})}
