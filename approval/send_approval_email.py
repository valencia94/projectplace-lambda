#!/usr/bin/env python3
"""
send_approval_email.py – 2025-05-23 (3.9-compatible)

• Generates UUID-token, stores in DynamoDB (status =pending)
• Auto-discovers latest Acta PDF in S3 for the project
• Sends SES HTML e-mail with Approve / Reject links
"""

import os, uuid, json, time, mimetypes, urllib.parse
from typing import Any, Dict, Optional
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from email.message import EmailMessage

# ─── ENV ───────────────────────────────────────────────────────────
REGION        = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME    = os.getenv("DYNAMODB_ENRICHMENT_TABLE") or os.getenv("DYNAMODB_TABLE_NAME")
API_ID        = os.environ["ACTA_API_ID"]
API_STAGE     = os.getenv("API_STAGE", "prod")
EMAIL_SOURCE  = os.environ["EMAIL_SOURCE"]
BUCKET_NAME   = os.environ["S3_BUCKET_NAME"]

API_BASE   = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve"
BRAND_CLR  = "#1b998b"

# ─── AWS CLIENTS ───────────────────────────────────────────────────
ses = boto3.client("ses", region_name=REGION)
s3  = boto3.client("s3",  region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

# ─── UTILITY ───────────────────────────────────────────────────────
def latest_pdf_key(project_id: str) -> Optional[str]:
    prefix = f"actas/"  # all Acta files live here
    probe  = s3.list_objects_v2(Bucket=BUCKET_NAME,
                                Prefix=f"{prefix}Acta_*_{project_id}")
    if not probe.get("Contents"):
        return None
    # newest = max by LastModified
    newest = max(probe["Contents"], key=lambda obj: obj["LastModified"])
    return newest["Key"]

def build_html(name: str, approve: str, reject: str,
               comment: Optional[str] = None) -> str:
    btn = ("display:inline-block;padding:10px 22px;margin:4px;"
           "border-radius:4px;font-family:Arial;font-size:15px;"
           "color:#fff;text-decoration:none;")
    approve_btn = f'<a href="{approve}" style="{btn}background:{BRAND_CLR};">Approve</a>'
    reject_btn  = f'<a href="{reject}"  style="{btn}background:#d9534f;">Reject</a>'
    comment_div = (f'<p style="border-left:4px solid {BRAND_CLR};padding:8px 12px;'
                   f'background:#fafafa;">{comment}</p>' if comment else "")
    return f"""\
<html><body style="font-family:Arial,Helvetica">
<h2 style="color:{BRAND_CLR};margin-bottom:4px">Project Acta ready for review</h2>
<p>Hello {name or 'there'},</p>
<p>Please check the attached Acta and choose an option:</p>
{approve_btn}&nbsp;{reject_btn}
{comment_div}
<p style="margin-top:28px;font-size:12px;color:#888">
CVDex Tech Solutions – powered by automation
</p>
</body></html>"""

# ─── HANDLER ───────────────────────────────────────────────────────
def lambda_handler(event: Dict[str, Any], _ctx):
    # 1️⃣ Parse required fields
    try:
        payload = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event
        project_id = payload["project_id"]
        recipient  = payload["recipient"]
    except (KeyError, json.JSONDecodeError, TypeError):
        return {"statusCode": 400, "body": "project_id and recipient are required"}

    # 2️⃣ Locate the “Client_Email” card to grab comments (optional)
    resp = ddb.query(KeyConditionExpression=Key("project_id").eq(project_id),
                     ScanIndexForward=False, Limit=20)  # scan backward, most-recent first
    card_row = next((r for r in resp.get("Items", [])
                     if r.get("title") == "Client_Email"), {})
    card_id = card_row.get("card_id", "unknown")
    last_comment = ""
    if isinstance(card_row.get("comments"), list) and card_row["comments"]:
        # comments stored as list of strings
        last_comment = str(card_row["comments"][0])[:250]

    # 3️⃣ Find latest Acta PDF in S3
    pdf_key = latest_pdf_key(project_id)
    if not pdf_key:
        return {"statusCode": 404, "body": "Could not locate Acta PDF in S3"}

    # 4️⃣ Generate token + persist
    token = str(uuid.uuid4())
    ddb.update_item(
        Key={"project_id": project_id, "card_id": card_id},
        UpdateExpression="SET approval_token=:t, approval_status=:s, sent_timestamp=:ts",
        ExpressionAttributeValues={":t": token, ":s": "pending", ":ts": int(time.time())}
    )

    # 5️⃣ Signed links
    qtok = urllib.parse.quote_plus(token)
    approve = f"{API_BASE}?token={qtok}&status=approved"
    reject  = f"{API_BASE}?token={qtok}&status=rejected"

    # 6️⃣ Fetch PDF
    try:
        pdf_bytes = s3.get_object(Bucket=BUCKET_NAME, Key=pdf_key)["Body"].read()
    except ClientError as e:
        return {"statusCode":500, "body":f"S3 error: {e.response['Error']['Message']}"}

    maintype, subtype = mimetypes.guess_type(pdf_key)[0].split("/")

    # 7️⃣ Compose e-mail
    msg = EmailMessage()
    msg["Subject"] = f"Action required – Acta for project {project_id}"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = recipient
    msg.set_content("Please view this e-mail in HTML.")
    msg.add_alternative(build_html(None, approve, reject, last_comment), subtype="html")
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
            "body": json.dumps({"MessageId": res["MessageId"], "pdf_key": pdf_key})}
