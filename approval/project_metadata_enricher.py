import os
import boto3
import requests
import json
import time
from datetime import datetime
from uuid import uuid4
from collections import defaultdict

REGION = os.environ.get("AWS_REGION")
TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME")
SECRET_NAME = os.environ.get("PROJECTPLACE_SECRET_NAME", "ProjectPlaceAPICredentials")
PROJECTPLACE_API_URL = "https://api.projectplace.com"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)
secrets = boto3.client("secretsmanager", region_name=REGION)

def get_projectplace_token():
    try:
        secret_data = secrets.get_secret_value(SecretId=SECRET_NAME)
        credentials = json.loads(secret_data["SecretString"])
        resp = requests.post(f"{PROJECTPLACE_API_URL}/oauth2/access_token", data={
            "grant_type": "client_credentials",
            "client_id": credentials["PROJECTPLACE_ROBOT_CLIENT_ID"],
            "client_secret": credentials["PROJECTPLACE_ROBOT_CLIENT_SECRET"]
        })
        return resp.json().get("access_token")
    except Exception as e:
        print(f"[ERROR] Retrieving ProjectPlace token: {str(e)}")
        return None

def get_pm_email(project_id, token, creator_id):
    url = f"{PROJECTPLACE_API_URL}/1/projects/{project_id}/members"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(url, headers=headers)
        for member in response.json():
            if str(member.get("id")) == str(creator_id):
                return member.get("email")
    except Exception as e:
        print(f"[ERROR] Fetching PM email: {str(e)}")
    return None

def lambda_handler(event=None, context=None):
    print("🚀 Starting full metadata enrichment...")
    token = get_projectplace_token()
    if not token:
        return {"statusCode": 500, "body": "Failed to get API token"}

    try:
        response = table.scan()
        items = response.get("Items", [])
    except Exception as e:
        print(f"[ERROR] Scanning table: {str(e)}")
        return {"statusCode": 500, "body": "Failed to scan DynamoDB"}

    if not items:
        return {"statusCode": 404, "body": "No records to enrich"}

    # Group cards by project_id
    grouped = defaultdict(list)
    for item in items:
        if "project_id" in item:
            grouped[item["project_id"]].append(item)

    for project_id, cards in grouped.items():
        print(f"🔄 Enriching project: {project_id}")
        for item in cards:
            card_id = item.get("card_id")
            creator = item.get("creator")
            client_email = None

            if item.get("title") == "Client_Email":
                comments = item.get("comments")
                if comments and isinstance(comments, list):
                    client_email = comments[0]

            creator_id = None
            try:
                creator_data = json.loads(creator.replace("'", '"')) if creator else {}
                creator_id = creator_data.get("id")
            except:
                pass

            pm_email = get_pm_email(project_id, token, creator_id) if creator_id else None

            try:
                table.update_item(
                    Key={"project_id": project_id, "card_id": card_id},
                    UpdateExpression="SET client_email = :ce, pm_email = :pe, approval_token = :at, sent_timestamp = :ts, #s = :st",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":ce": client_email or "",
                        ":pe": pm_email or "",
                        ":at": str(uuid4()),
                        ":ts": int(time.time()),
                        ":st": "pending"
                    }
                )
                print(f"✅ Updated card_id: {card_id} in project {project_id}")
            except Exception as e:
                print(f"[ERROR] Updating card_id {card_id}: {str(e)}")

    print("✅ Enrichment complete for all projects.")
    return {"statusCode": 200, "body": "Enrichment completed for all projects."}
