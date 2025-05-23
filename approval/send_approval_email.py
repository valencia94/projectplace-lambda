#!/usr/bin/env python3
"""
send_approval_email.py  – v1.7
"""

import os, uuid, json, time, mimetypes, urllib.parse
from typing import Any, Dict, List
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from email.message import EmailMessage

# ── ENV ────────────────────────────────────────────────
REGION        = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME    = os.getenv("DYNAMODB_ENRICHMENT_TABLE") or os.getenv("DYNAMODB_TABLE_NAME")
API_ID        = os.environ["ACTA_API_ID"]
API_STAGE     = os.getenv("API_STAGE", "prod")
EMAIL_SOURCE  = os.environ["EMAIL_SOURCE"]
BUCKET        = os.environ["S3_BUCKET_NAME"]

API_BASE   = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve"
BRAND_COLO = "#1b998b"

# ── AWS clients ────────────────────────────────────────
ses  = boto3.client("ses", region_name=REGION)
s3   = boto3.client("s3",  region_name=REGION)
ddb  = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

# ── helpers ────────────────────────────────────────────
def latest_pdf_key(project_id: str) -> str | None:
    """Return newest *.pdf key whose basename starts with project_id."""
    obj_list = s3.list_objects_v2(Bucket=BUCKET, Prefix=str(project_id))
    pdfs: List[Dict[str,Any]] = [
        o for o in obj_list.get("Contents", [])
        if o["Key"].lower().endswith(".pdf")
    ]
    if not pdfs:
        return None
    return max(pdfs, key=lambda o: o["LastModified"])["Key"]

def build_html(project: str, approve: str, reject: str, comment: str | None) -> str:
    btn = ("display:inline-block;padding:10px 20px;margin:4px 6px;border-radius:4px;"
           "font-family:Arial;font-size:15px;color:#fff;text-decoration:none;")
    approve_btn = f'<a href="{approve}" style="{btn}background:{BRAND_COLO};">Approve</a>'
    reject_btn  = f'<a href="{reject}"  style="{btn}background:#d9534f;">Reject</a>'
    comment_html = (f"""<p style="border-left:4px solid {BRAND_COLO};
                          padding:8px 12px;background:#f8f8f8;">{comment}</p>"""
                    if comment else "")
    return f"""<!DOCTYPE html><html><body>
      <h2 style="color:{BRAND_COLO};font-family:Arial">Project Acta – action required</h2>
      <p>Please review the attached Acta for <strong>{project}</strong> and choose an option.</p>
      {approve_btn}&nbsp;{reject_btn}
      {comment_html}
      <p style="font-size:12px;color:#888">CVDex Tech Solutions</p>
    </body></html>"""

# ── handler ────────────────────────────────────────────
def lambda_handler(event, _ctx):
    try:
        body = json.loads(event.get("body", "{}"))
        project_id = body["project_id"]
        recipient  = body["recipient"]
    except (KeyError, json.JSONDecodeError):
        return {"statusCode":400,"body":"Missing project_id or recipient"}

    # 1️⃣ find Client_Email card
    rows = ddb.query(KeyConditionExpression=Key("project_id").eq(project_id),
                     ScanIndexForward=False).get("Items", [])
    card_row = next((r for r in rows if r.get("title") == "Client_Email"), None)
    if not card_row:
        return {"statusCode":404,"body":"No Client_Email row found"}

    card_id = card_row["card_id"]
    comments_field = card_row.get("comments") or []
    # comment element can be str OR {"text": "..."}
    raw = comments_field[0] if comments_field else ""
    last_comment = (raw["text"] if isinstance(raw, dict) else str(raw))[:250] or None

    # 2️⃣ pick latest PDF
    pdf_key = latest_pdf_key(project_id)
    if not pdf_key:
        return {"statusCode":404,"body":"Could not locate Acta PDF"}

    # 3️⃣ persist token
    token = str(uuid.uuid4())
    ddb.update_item(Key={"project_id":project_id,"card_id":card_id},
                    UpdateExpression="SET approval_token=:t, approval_status=:s, sent_timestamp=:ts",
                    ExpressionAttributeValues={":t":token,":s":"pending",":ts":int(time.time())})

    # 4️⃣ URLs
    safe = urllib.parse.quote_plus(token)
    approve = f"{API_BASE}?token={safe}&status=approved"
    reject  = f"{API_BASE}?token={safe}&status=rejected"

    # 5️⃣ fetch PDF
    pdf_bytes = s3.get_object(Bucket=BUCKET, Key=pdf_key)["Body"].read()
    maintype, subtype = mimetypes.guess_type(pdf_key)[0].split("/")

    # 6️⃣ email
    msg = EmailMessage()
    msg["Subject"] = "Action required – Project Acta approval"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = recipient
    msg.set_content("Please view this e-mail in HTML.")
    msg.add_alternative(build_html(project_id, approve, reject, last_comment), subtype="html")
    msg.add_attachment(pdf_bytes, maintype=maintype, subtype=subtype,
                       filename=os.path.basename(pdf_key))

    try:
        resp = ses.send_raw_email(Source=EMAIL_SOURCE,
                                  Destinations=[recipient],
                                  RawMessage={"Data": msg.as_bytes()})
    except ClientError as e:
        return {"statusCode":500,"body":f"SES send failed: {e.response['Error']['Message']}"}

    return {"statusCode":200,"body":json.dumps({"MessageId":resp['MessageId']})}
