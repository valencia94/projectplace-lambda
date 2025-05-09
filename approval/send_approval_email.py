#!/usr/bin/env python3
"""
sendApprovalEmail.py
---------------------------------
Sends branded Acta-approval emails with Approve / Reject links,
attaches the Acta PDF from S3, and writes the approval_token
+ status back into *your* (project_id,card_id) item.

ENV VARS REQUIRED
-----------------
AWS_REGION             e.g. us-east-2
ACTA_API_ID            e.g. 4r0pt34gx4    (REST API id, injected after API deploy)
API_STAGE              e.g. prod         (optional, defaults to “prod”)
EMAIL_SOURCE           AutomationSolutionsCenter@cvdexinfo.com
DYNAMODB_TABLE_NAME    ProjectPlace_DataExtrator_landing_table_v3
S3_BUCKET_NAME         projectplace-dv-2025-x9a7b
"""

import os, uuid, json
import boto3
from email.message import EmailMessage
from boto3.dynamodb.conditions import Key

# ─── ENV ────────────────────────────────────────────────────────────────
REGION         = os.environ["AWS_REGION"]
API_ID         = os.environ.get("ACTA_API_ID")      # not fatal if missing
API_STAGE      = os.environ.get("API_STAGE", "prod")
EMAIL_SOURCE   = os.environ["EMAIL_SOURCE"]
TABLE_NAME     = os.environ["DYNAMODB_TABLE_NAME"]
S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]

# ─── CLIENTS ──────────────────────────────────────────────────────────
ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
ses = boto3.client("ses", region_name=REGION)
s3  = boto3.client("s3",  region_name=REGION)


def lambda_handler(event, _ctx):
    # ─── parse input ────────────────────────────────────────────────
    # we expect { "acta_id": "<PROJECT_ID>" }
    acta_id = event.get("acta_id")
    if not acta_id:
        return {"statusCode":400, "body":"Missing acta_id"}

    # ─── fetch all metadata rows for that project ───────────────────
    resp = ddb.query(
        KeyConditionExpression=Key("project_id").eq(acta_id)
    )
    items = resp.get("Items", [])
    if not items:
        return {"statusCode":404, "body":"Acta not found"}

    # ─── pull the client email row ─────────────────────────────────
    email_item = next(
      (i for i in items if i.get("title")=="Client_Email" and i.get("comments")),
      None
    )
    if not email_item:
        return {"statusCode":404, "body":"Client email missing"}
    recipient = email_item["comments"][0]

    # ─── pull the PDF S3 key ────────────────────────────────────────
    pdf_item = next((i for i in items if i.get("s3_pdf_path")), None)
    if not pdf_item:
        return {"statusCode":404, "body":"PDF missing in S3"}
    pdf_key = pdf_item["s3_pdf_path"]
    card_id  = pdf_item["card_id"]

    # read the PDF bytes
    pdf_stream = s3.get_object(Bucket=S3_BUCKET_NAME, Key=pdf_key)["Body"].read()

    # ─── generate & persist a new token ─────────────────────────────
    token = str(uuid.uuid4())
    ddb.update_item(
      Key={"project_id":acta_id, "card_id":card_id},
      UpdateExpression=(
        "SET approval_token = :t, approval_status = :p, sent_timestamp = :ts"
      ),
      ExpressionAttributeValues={
        ":t": token,
        ":p": "pending",
        ":ts": int(__import__("time").time())
      }
    )

    # ─── build your Approve / Reject URLs ───────────────────────────
    if API_ID:
        base = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}/approve?token={token}"
        approve = base + "&status=approved"
        reject  = base + "&status=rejected"
    else:
        # if someone forgot to inject ACTA_API_ID, at least let you test locally
        approve = reject = "#missing-api-id"

    # ─── craft the HTML body ────────────────────────────────────────
    html = f"""
    <html><body style="font-family:Verdana,Arial">
      <h3>Acta Approval Request – {acta_id}</h3>
      <p>Please review the attached Acta and click:</p>
      <p>
        <a href="{approve}" style="
            padding:10px 18px;
            background:#4AC795;
            color:#fff;
            text-decoration:none;
            border-radius:4px">✔ Approve</a>
        &nbsp;
        <a href="{reject}" style="
            padding:10px 18px;
            background:#E74C3C;
            color:#fff;
            text-decoration:none;
            border-radius:4px">✖ Reject</a>
      </p>
      <p style="font-size:12px;color:#888">
        Automated via CVDex Acta Automation Platform.
      </p>
    </body></html>
    """

    # ─── assemble raw email + attachment ────────────────────────────
    msg = EmailMessage()
    msg["Subject"] = f"Acta Approval • {acta_id}"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = recipient
    msg.set_content("This email requires HTML support.")
    msg.add_alternative(html, subtype="html")
    msg.add_attachment(
      pdf_stream,
      maintype="application",
      subtype="pdf",
      filename="Acta.pdf"
    )

    # ─── send via SES ───────────────────────────────────────────────
    res = ses.send_raw_email(
      Source=EMAIL_SOURCE,
      Destinations=[recipient],
      RawMessage={"Data": msg.as_bytes()}
    )
    if res["ResponseMetadata"]["HTTPStatusCode"] != 200:
        return {"statusCode":500, "body":"SES send failed"}

    return {
      "statusCode":200,
      "body": json.dumps({"MessageId":res["MessageId"]})
    }
