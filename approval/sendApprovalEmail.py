import boto3
import os
import json
import base64
from email.message import EmailMessage
from approval.email_utils import resolve_email

# AWS clients
s3 = boto3.client("s3")
ses = boto3.client("ses")
dynamodb = boto3.resource("dynamodb")

def lambda_handler(event, context):
    table = dynamodb.Table(os.environ["DYNAMODB_TABLE_NAME"])
    acta_id = event.get("acta_id")

    if not acta_id:
        return {"statusCode": 400, "body": "Missing acta_id"}

    # Query DynamoDB for Acta metadata
    result = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("project_id").eq(acta_id)
    )

    if not result["Items"]:
        return {"statusCode": 404, "body": "Acta not found"}

    # Look for client email from title='Client_Email'
    email = None
    for item in result["Items"]:
        if item.get("title") == "Client_Email":
            comments = item.get("comments", [])
            if comments and isinstance(comments, list):
                email = comments[0]
            break

    if not email:
        return {"statusCode": 404, "body": "Client email not found in Acta"}

    print(f"Resolved client email: {email}")

    # Find PDF path
    pdf_key = None
    for item in result["Items"]:
        if item.get("s3_pdf_path"):
            pdf_key = item["s3_pdf_path"]
            break

    if not pdf_key:
        return {"statusCode": 404, "body": "PDF path not found for Acta"}

    # Fetch PDF from S3
    try:
        obj = s3.get_object(Bucket=os.environ["S3_BUCKET_NAME"], Key=pdf_key)
        pdf_content = obj["Body"].read()
    except Exception as e:
        print(f"Error fetching PDF from S3: {str(e)}")
        return {"statusCode": 500, "body": "Failed to fetch Acta PDF"}

    # Build email with branding and approve/reject links
    domain = os.environ.get("DOMAIN")
    token = "static-token-for-testing"  # Replace with UUID/token generator in production
    approve_url = f"https://{domain}/approve?token={token}&status=approved"
    reject_url = f"https://{domain}/approve?token={token}&status=rejected"

    msg = EmailMessage()
    msg["Subject"] = f"Acta Approval Request: {acta_id}"
    msg["From"] = os.environ["EMAIL_SOURCE"]
    msg["To"] = email
    msg.set_content(f"Please review and approve/reject the attached Acta.\n\nApprove: {approve_url}\nReject: {reject_url}")

    # HTML branding and button layout
    html_content = f"""
    <html>
      <body style=\"font-family:Verdana, sans-serif; color:#333;\">
        <div style=\"padding:20px; border:1px solid #ccc; max-width:600px;\">
          <img src=\"https://ikusi.com/branding/logo.png\" alt=\"Ikusi\" style=\"max-width:150px; margin-bottom:10px;\">
          <h2 style=\"color:#4AC795;\">Acta Approval Request</h2>

          <p>
            Please review the attached Acta document for <strong>{acta_id}</strong>.
            You may approve or reject this Acta using the buttons below.
          </p>

          <div style=\"margin-top:20px;\">
            <a href=\"{approve_url}\" style=\"padding:10px 20px; background:#4AC795; color:#fff; text-decoration:none; border-radius:4px;\">✔️ Approve</a>
            <a href=\"{reject_url}\" style=\"padding:10px 20px; background:#E74C3C; color:#fff; text-decoration:none; border-radius:4px; margin-left:10px;\">✖️ Reject</a>
          </div>

          <p style=\"margin-top:30px; font-size:13px; color:#999;\">
            If you would like to provide additional comments or context, please include them via the approval interface after clicking.
          </p>
        </div>
      </body>
    </html>
    """

    msg.add_alternative(html_content, subtype='html')
    msg.add_attachment(
        pdf_content,
        maintype='application',
        subtype='pdf',
        filename="ActaDocument.pdf"
    )

    try:
        response = ses.send_raw_email(
            Source=os.environ["EMAIL_SOURCE"],
            Destinations=[email],
            RawMessage={"Data": msg.as_bytes()}
        )
        print(f"Email sent successfully! SES MessageId: {response['MessageId']}")
        return {"statusCode": 200, "body": "Approval email sent successfully."}
    except Exception as e:
        print(f"Error sending SES email: {str(e)}")
        return {"statusCode": 500, "body": "Failed to send email"}
