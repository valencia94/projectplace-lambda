import os, boto3

dynamodb = boto3.resource("dynamodb")
def lambda_handler(event, context):
    token = event["queryStringParameters"]["token"]
    status = event["queryStringParameters"]["status"]
    table = dynamodb.Table(os.environ["DYNAMODB_TABLE_NAME"])

    result = table.scan(FilterExpression=boto3.dynamodb.conditions.Attr("approval_token").eq(token))
    if not result["Items"]:
        return {"statusCode": 404, "body": "Invalid token"}

    acta_id = result["Items"][0]["project_id"]
    table.update_item(
        Key={"project_id": acta_id},
        UpdateExpression="SET approval_status = :s",
        ExpressionAttributeValues={":s": status}
    )
    return {"statusCode": 200, "body": f"Acta {status.capitalize()}"}
