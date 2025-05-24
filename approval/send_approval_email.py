#!/usr/bin/env python3
"""
send_approval_email.py – v1.7.2  (2025-05-23)

• Generates a UUID-4 approval_token, persists status=pending
• Looks up *Client_Email* card for recipient + latest comment
• Auto-discovers the newest Acta PDF in S3 (actas/…_<project>.pdf)
• Sends branded HTML mail via SES with Approve / Reject links
"""

from __future__ import annotations

import os, re, json, time, uuid, mimetypes, urllib.parse
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key
from email.message import EmailMessage

# ── helpers ─────────────────────────────────────────────────────────
VALID_NAME = re.compile(r"^[a-zA-Z0-9_.\-]+$")

def env(key: str, required: bool = True) -> str | None:
    """Read env-var, strip whitespace, die early if missing/invalid."""
    val = os.getenv(key, "").strip()
    if required and not val:
        raise SystemExit(f"❌ Missing env var: {key}")
    if val and not VALID_NAME.fullmatch(val) and key.endswith("_TABLE"):
        raise SystemExit(f"❌ Env {key} contains illegal chars → {val!r}")
    return val or None

# ── ENV ─────────────────────────────────────────────────────────────
REGION       = env("AWS_REGION") or boto3.Session().region_name
TABLE_NAME   = env("DYNAMODB_ENRICHMENT_TABLE") or env("DYNAMODB_TABLE_NAME")
BUCKET_NAME  = env("S3_BUCKET_NAME")
EMAIL_SOURCE = env("EMAIL_SOURCE")
API_ID       = env("ACTA_API_ID")
API_STAGE    = env("API_STAGE") or "prod"

API_BASE  = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve"
BRAND_CLR = "#1b998b"

# ── AWS clients ─────────────────────────────────────────────────────
ses = boto3.client("ses",  region_name=REGION)
s3  = boto3.client("s3",   region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

# ── util ------------------------------------------------------------
def latest_pdf_key(project_id: str) -> Optional[str]:
    """Return newest *.pdf under actas/ whose name ends with _<project_id>.pdf"""
    paginator = s3.get_paginator("list_objects_v2")
    newest_key, newest_ts = None, 0
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix="actas/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.lower().endswith(f"_{project_id}.pdf") \
               and obj["LastModified"].timestamp() > newest_ts:
                newest_key, newest_ts = key, obj["LastModified"].timestamp()
    return newest_key

def build_html(project: str, approve: str, reject: str, comment: str | None) -> str:
    btn = ("display:inline-block;padding:12px 28px;margin:0 6px;border-radius:4px;"
           "font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#fff;text-decoration:none;")
    approve_btn = f'<a href="{approve}" style="{btn}background:{BRAND_CLR};">Approve</a>'
    reject_btn  = f'<a href="{reject}"  style="{btn}background:#d9534f;">Reject</a>'

    comment_html = (f'''
      <tr><td style="padding-top:22px">
        <div style="border:1px solid #e0e0e0;border-left:4px solid {BRAND_CLR};
                    background:#222;color:#f1f1f1;padding:14px;font-size:14px;">
          <strong>Last comment</strong><br>{comment}
        </div>
      </td></tr>''' if comment else "")

    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#f5f5f5">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr><td align="center" style="padding:28px 0">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0"
               style="border:1px solid #ddd;border-radius:6px;background:#000">
          <tr><td style="background:{BRAND_CLR};padding:24px;
                         font-family:Arial,Helvetica,sans-serif;font-size:22px;
                         color:#fff;border-top-left-radius:6px;border-top-right-radius:6px">
              Project Acta ready for review
          </td></tr>

          <tr><td style="padding:24px;font-family:Arial,Helvetica,sans-serif;
                         font-size:15px;line-height:22px;color:#f1f1f1">
              Hi there,<br><br>
              Please review the attached Acta for<br>
              <strong>{project}</strong> and choose an option:
          </td></tr>

          <tr><td align="center" style="padding:12px 0 24px 0">
              {approve_btn}{reject_btn}
          </td></tr>

          {comment_html}

          <tr><td style="padding:24px;font-family:Arial,Helvetica,sans-serif;
                         font-size:12px;color:#888;border-top:1px solid #444">
              CVDex Tech Solutions — empowering excellence through automation
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""


# ── Lambda handler ─────────────────────────────────────────────────
def lambda_handler(event: Dict[str, Any], _ctx):
    # ── 1. Parse request ───────────────────────────────────────────
    try:
        payload = json.loads(event["body"]) if isinstance(event.get("body"), str) else event
        project_id = payload["project_id"]
        recipient  = payload["recipient"]
    except Exception as e:
        return {"statusCode": 400, "body": f"Missing / malformed body: {e}"}

    # ── 2. Query the project’s latest rows ─────────────────────────
    resp  = ddb.query(KeyConditionExpression=Key("project_id").eq(project_id),
                      ScanIndexForward=False, Limit=25)
    items = resp.get("Items", [])
    if not items:
        return {"statusCode": 404, "body": "Project not found in DynamoDB"}

    client_rows = [i for i in items if i.get("title") == "Client_Email"]
    card_row    = client_rows[0] if client_rows else items[0]

    # comment preview
    comment_raw = card_row.get("comments", [])
    if isinstance(comment_raw, list) and comment_raw:
        first = comment_raw[0]
        last_comment = first.get("text", "")[:250] if isinstance(first, dict) else str(first)[:250]
    elif isinstance(comment_raw, str):
        last_comment = comment_raw[:250]
    else:
        last_comment = None

    # ── 3. Locate PDF ──────────────────────────────────────────────
    pdf_key = card_row.get("s3_pdf_path") or latest_pdf_key(project_id)
    if not pdf_key:
        return {"statusCode": 500, "body": "Could not locate Acta PDF"}

    try:
        pdf_bytes = s3.get_object(Bucket=BUCKET_NAME, Key=pdf_key)["Body"].read()
    except ClientError as e:
        return {"statusCode": 500,
                "body": f"S3 fetch failed: {e.response['Error']['Message']}"}

    mime_type = (mimetypes.guess_type(pdf_key)[0] or "application/pdf").split("/")
    maintype, subtype = mime_type[0], mime_type[1]

    # ── 4. Persist approval token ─────────────────────────────────
    token = str(uuid.uuid4())
    ddb.update_item(
        Key={"project_id": project_id, "card_id": card_row["card_id"]},
        UpdateExpression="SET approval_token=:t, approval_status=:s, sent_timestamp=:ts",
        ExpressionAttributeValues={":t": token, ":s": "pending", ":ts": int(time.time())}
    )

    # ── 5. Build URLs ─────────────────────────────────────────────
    q = urllib.parse.quote_plus(token)
    approve_url = f"{API_BASE}?token={q}&status=approved"
    reject_url  = f"{API_BASE}?token={q}&status=rejected"

    # ── 6. Compose & send email ───────────────────────────────────
    msg = EmailMessage()
    msg["Subject"] = f"Action required – Acta {project_id}"
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

    return {"statusCode": 200, "body": "Approval email sent."}
