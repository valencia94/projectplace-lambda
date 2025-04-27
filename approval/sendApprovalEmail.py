import boto3
import os
import json
import base64
from email.message import EmailMessage
from approval.email_utils import resolve_email

s3 = boto3.client("s3")
ses = boto3.client("ses")
dynamodb = boto3.resource("dynamodb")

def lambda_handler(event, context):
    table = dynamodb.Table(os.environ["DYNAMODB_TABLE_NAME"])
    acta_id = event.get("acta_id")

    if not acta_id:
        return {"statusCode": 400, "body": "Missing acta_id"}

    # Query DynamoDB for Acta
    result = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("project_id").eq(acta_id)
    )

    if not result["Items"]:
        return {"statusCode": 404, "body": "Acta not found"}

    # Search for the Client_Email title
    email = None
    for item in result["Items"]:
        if item.get("title") == "Client_Email":
            comments = item.get("comments", [])
            if comments and isinstance(comments, list):
                email = comments[0]  # First comment holds the client email
            break

    if not email:
        return {"statusCode": 404, "body": "Client email not found in Acta"}

    print(f"Resolved client email: {email}")

    # Lookup PDF file path in item
    pdf_key = None
    for item in result["Items"]:
        if item.get("s3_pdf_path"):
            pdf_key = item["s3_pdf_path"]
            break

    if not pdf_key:
        return {"statusCode": 404, "body": "PDF path not found for Acta"}

    # Fetch PDF file from S3
    try:
        obj = s3.get_object(Bucket=os.environ["S3_BUCKET_NAME"], Key=pdf_key)
        pdf_content = obj["Body"].read()
    except Exception as e:
        print(f"Error fetching PDF from S3: {str(e)}")
        return {"statusCode": 500, "body": "Failed to fetch Acta PDF"}

    # Build Email
    domain = os.environ.get("DOMAIN")
    token = "static-token-for-testing"  # TODO: Replace with dynamic token generation
    approve_url = f"https://{domain}/approve?token={token}&status=approved"
    reject_url = f"https://{domain}/approve?token={token}&status=rejected"

    msg = EmailMessage()
    msg["Subject"] = "Acta Approval Request"
    msg["From"] = os.environ["EMAIL_SOURCE"]
    msg["To"] = email
    msg.set_content(f"Please review and approve/reject the attached Acta.\n\nApprove: {approve_url}\nReject: {reject_url}")

    msg.add_alternative(f"""
    <html>
      <body>
        <p>Review the attached Acta document.</p>
        <p><a href="{approve_url}">Approve</a> | <a href="{reject_url}">Reject</a></p>
      </body>
    </html>
    """, subtype='html')

    msg.add_attachment(
        pdf_content,
        maintype='application',
        subtype='pdf',
        filename="ActaDocument.pdf"
    )

    # Send email via SES
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
