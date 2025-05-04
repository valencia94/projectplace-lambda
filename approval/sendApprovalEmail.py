#!/usr/bin/env python3
"""
sendApprovalEmail.py
---------------------------------
Sends branded Acta-approval emails with Approve / Reject links.
• Builds HTML with dynamic token + API Gateway URL.
• Attaches the generated PDF from S3.
• Stores approval_token state in DynamoDB.

ENV VARS REQUIRED
-----------------
AWS_REGION               e.g. us-east-2
ACTA_API_ID              e.g. 4r0pt34gx4   (REST API id)
EMAIL_SOURCE             AutomationSolutionsCenter@cvdexinfo.com  (verified)
DYNAMODB_TABLE_NAME      ProjectPlace_DataExtrator_landing_table_v3
S3_BUCKET_NAME           projectplace-dv-2025-x9a7b
"""

import os, uuid, boto3, json
from email.message import EmailMessage
from boto3.dynamodb.conditions import Key

REGION          = os.environ["AWS_REGION"]
API_ID          = os.environ["ACTA_API_ID"]
EMAIL_SOURCE    = os.environ["EMAIL_SOURCE"]
TABLE_NAME      = os.environ["DYNAMODB_TABLE_NAME"]
S3_BUCKET_NAME  = os.environ["S3_BUCKET_NAME"]

dynamodb = boto3.resource("dynamodb", region_name=REGION)
ses      = boto3.client("ses",        region_name=REGION)
s3       = boto3.client("s3",         region_name=REGION)

def lambda_handler(event, _ctx):
    """
    Expected event:
    {
      "acta_id": "100000064035182"
    }
    """
    acta_id = event.get("acta_id")
    if not acta_id:
        return {"statusCode": 400, "body": "Missing acta_id"}

    table   = dynamodb.Table(TABLE_NAME)
    items   = table.query(KeyConditionExpression=Key("project_id").eq(acta_id))["Items"]
    if not items:
        return {"statusCode": 404, "body": "Acta not found"}

    # --- derive client email from metadata row titled "Client_Email"
    email_item = next((i for i in items if i.get("title") == "Client_Email" and i.get("comments")), None)
    if not email_item:
        return {"statusCode": 404, "body": "Client email missing"}
    recipient = email_item["comments"][0]

    # --- locate PDF path
    pdf_key = next((i.get("s3_pdf_path") for i in items if i.get("s3_pdf_path")), None)
    if not pdf_key:
        return {"statusCode": 404, "body": "PDF missing in S3 key"}

    pdf_stream = s3.get_object(Bucket=S3_BUCKET_NAME, Key=pdf_key)["Body"].read()

    # --- create approval token & persist
    token = str(uuid.uuid4())
    table.put_item(Item={
        "approval_token": token,
        "project_id":     acta_id,
        "approval_status": "pending"
    })

    base_url = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/prod/approve?token={token}"
    approve  = f"{base_url}&status=approved"
    reject   = f"{base_url}&status=rejected"

    html_body = f"""
    <html><body style="font-family:Verdana,Arial">
      <h3>Acta Approval Request – {acta_id}</h3>
      <p>Please review the attached Acta and click a response:</p>
      <p>
        <a href="{approve}" style="padding:10px 18px;background:#4AC795;color:#fff;text-decoration:none;border-radius:4px">✔ Approve</a>
        &nbsp;
        <a href="{reject}"  style="padding:10px 18px;background:#E74C3C;color:#fff;text-decoration:none;border-radius:4px">✖ Reject</a>
      </p>
      <p style="font-size:12px;color:#888">Automated message via CVDex Acta Automation Platform.</p>
    </body></html>
    """

    # build raw email (HTML + PDF attachment)
    msg = EmailMessage()
    msg["Subject"] = f"Acta Approval • {acta_id}"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = recipient
    msg.set_content("HTML required")
    msg.add_alternative(html_body, subtype="html")
    msg.add_attachment(pdf_stream,
                       maintype="application",
                       subtype="pdf",
                       filename="Acta.pdf")

    # send via SES
    res = ses.send_raw_email(
        Source=EMAIL_SOURCE,
        Destinations=[recipient],
        RawMessage={"Data": msg.as_bytes()}
    )
    assert res["ResponseMetadata"]["HTTPStatusCode"] == 200, "SES send failed"
    return {"statusCode":200, "body":json.dumps({"MessageId":res["MessageId"]})}
