import boto3, os, uuid, json
from email.message import EmailMessage
from boto3.dynamodb.conditions import Key

REGION          = os.environ["AWS_REGION"]
API_ID          = os.environ["ACTA_API_ID"]            # <- new
EMAIL_SOURCE    = os.environ["EMAIL_SOURCE"]
TABLE_NAME      = os.environ["DYNAMODB_TABLE_NAME"]
S3_BUCKET_NAME  = os.environ["S3_BUCKET_NAME"]

dynamodb = boto3.resource("dynamodb", region_name=REGION)
ses       = boto3.client("ses",        region_name=REGION)
s3        = boto3.client("s3",         region_name=REGION)

def lambda_handler(event, _):
    acta_id = event.get("acta_id")
    if not acta_id:
        return {"statusCode":400,"body":"Missing acta_id"}

    table = dynamodb.Table(TABLE_NAME)
    items = table.query(KeyConditionExpression=Key("project_id").eq(acta_id))["Items"]
    if not items: return {"statusCode":404,"body":"Acta not found"}

    email = next((i["comments"][0] for i in items
                  if i.get("title")=="Client_Email" and i.get("comments")), None)
    if not email: return {"statusCode":404,"body":"Client email missing"}

    pdf_key = next((i["s3_pdf_path"] for i in items if i.get("s3_pdf_path")), None)
    if not pdf_key: return {"statusCode":404,"body":"PDF missing"}

    pdf_stream = s3.get_object(Bucket=S3_BUCKET_NAME, Key=pdf_key)["Body"].read()

    # ---- approval token ----
    token = str(uuid.uuid4())
    table.put_item(Item={
        "approval_token": token,
        "project_id": acta_id,
        "approval_status": "pending"
    })

    approve = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/prod/approve?token={token}&status=approved"
    reject  = approve.replace("approved","rejected")

    html = f"""
    <html><body style="font-family:Verdana">
      <h3>Acta Approval Request – {acta_id}</h3>
      <p>Please review and click:</p>
      <a href="{approve}" style="padding:10px 18px;background:#4AC795;color:#fff;text-decoration:none;border-radius:4px">✔ Approve</a>
      &nbsp;
      <a href="{reject}" style="padding:10px 18px;background:#E74C3C;color:#fff;text-decoration:none;border-radius:4px">✖ Reject</a>
    </body></html>
    """

    msg = EmailMessage()
    msg["Subject"] = f"Acta Approval • {acta_id}"
    msg["From"]    = EMAIL_SOURCE
    msg["To"]      = email
    msg.set_content("HTML required")
    msg.add_alternative(html, subtype="html")
    msg.add_attachment(pdf_stream, maintype="application", subtype="pdf", filename="Acta.pdf")

    res = ses.send_raw_email(Source=EMAIL_SOURCE, Destinations=[email], RawMessage={"Data":msg.as_bytes()})
    assert res["ResponseMetadata"]["HTTPStatusCode"]==200
    return {"statusCode":200,"body":"Email sent"}
