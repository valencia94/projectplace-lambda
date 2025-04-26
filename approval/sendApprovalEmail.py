import boto3, os, json, base64
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

    result = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("project_id").eq(acta_id)
    )

    if not result["Items"]:
        return {"statusCode": 404, "body": "Acta not found"}

    item = result["Items"][0]
    leader = item.get("creator_name", "Default Leader")
    email = resolve_email(leader)
    pdf_key = item.get("s3_pdf_path")

    obj = s3.get_object(Bucket=os.environ["S3_BUCKET_NAME"], Key=pdf_key)
    pdf_content = obj["Body"].read()

    msg = EmailMessage()
    msg["Subject"] = "Approval Required: Acta Document"
    msg["From"] = os.environ["EMAIL_SOURCE"]
    msg["To"] = email
    msg.set_content("Please review the attached Acta and approve/reject using the link below.")

    token = "secure-token-1234"  # TODO: Replace with UUID and update DynamoDB
    domain = os.environ["DOMAIN"]
    approve_url = f"https://{domain}/approve?token={token}&status=approved"
    reject_url = f"https://{domain}/approve?token={token}&status=rejected"
    msg.add_alternative(f"""
    <html>
      <body>
        <p>Review and respond to the Acta:</p>
        <a href='{approve_url}'>Approve</a> | <a href='{reject_url}'>Reject</a>
      </body>
    </html>
    """, subtype='html')

    msg.add_attachment(pdf_content, maintype='application', subtype='pdf', filename="Acta.pdf")

    ses.send_raw_email(RawMessage={"Data": msg.as_bytes()})
    return {"statusCode": 200, "body": "Email sent"}
