import os, datetime, boto3
from boto3.dynamodb.conditions import Attr

REGION       = os.getenv("AWS_REGION")
TABLE_NAME   = os.getenv("DYNAMODB_ENRICHMENT_TABLE")
ddb          = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

TTL_SEC = 5 * 24 * 3600             # 5 days

def lambda_handler(_evt, _ctx):
    cutoff = (datetime.datetime.utcnow() -
              datetime.timedelta(seconds=TTL_SEC)).isoformat() + "Z"

    scan = ddb.scan(
        FilterExpression=Attr("approval_status").eq("pending") &
                         Attr("sent_timestamp").lt(cutoff),
        ProjectionExpression="project_id, card_id"
    )
    changed = 0
    for item in scan["Items"]:
        ddb.update_item(
            Key={"project_id": item["project_id"],
                 "card_id": item["card_id"]},
            UpdateExpression=("SET approval_status = :s, "
                              "approval_timestamp = :ts"),
            ExpressionAttributeValues={
                ":s": "approved-auto",
                ":ts": datetime.datetime.utcnow().isoformat() + "Z"
            }
        )
        changed += 1
    return {"updated": changed}
