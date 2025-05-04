#!/usr/bin/env python3
"""
Fetch your ProjectPlace cards from DynamoDB,
enrich them with client_email, pm_email + approval_token,
and write them back—with only the correct key fields.
"""

import os, json, time, urllib.request
from uuid import uuid4
from collections import defaultdict
import boto3

# ─── ENV & CLIENTS ──────────────────────────────────────────────────────────
REGION       = os.getenv("AWS_REGION") or exit("❌ Missing AWS_REGION")
TABLE_NAME   = os.getenv("DYNAMODB_TABLE_NAME") or exit("❌ Missing DYNAMODB_TABLE_NAME")
SECRET_NAME  = os.getenv("PROJECTPLACE_SECRET_NAME", "ProjectPlaceAPICredentials")
API_BASE_URL = "https://api.projectplace.com"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table     = dynamodb.Table(TABLE_NAME)
secrets   = boto3.client("secretsmanager", region_name=REGION)


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def get_pp_token() -> str:
    """Fetch client credentials from SecretsManager and
       exchange them for an OAuth2 token via urllib."""
    sec = secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"]
    creds = json.loads(sec)
    data = (
        f"grant_type=client_credentials"
        f"&client_id={creds['PROJECTPLACE_ROBOT_CLIENT_ID']}"
        f"&client_secret={creds['PROJECTPLACE_ROBOT_CLIENT_SECRET']}"
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE_URL}/oauth2/access_token",
        data=data,
        method="POST",
        headers={"Content-Type":"application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


def get_pm_email(project_id: str, token: str, creator_id: str) -> str:
    """Hit /1/projects/{project_id}/members and find the matching creator_id."""
    req = urllib.request.Request(
        f"{API_BASE_URL}/1/projects/{project_id}/members",
        headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req) as resp:
        members = json.loads(resp.read())
    for m in members:
        if str(m.get("id")) == str(creator_id):
            return m.get("email", "")
    return ""


# ─── LAMBDA HANDLER ─────────────────────────────────────────────────────────

def lambda_handler(event, context):
    print("🚀 Starting full metadata enrichment…")

    # 1️⃣ get token
    try:
        token = get_pp_token()
    except Exception as e:
        print("❌ Failed to get ProjectPlace token:", e)
        return {"statusCode": 500, "body": "Auth failure"}

    # 2️⃣ scan table
    try:
        items = table.scan().get("Items", [])
    except Exception as e:
        print("❌ DynamoDB scan failed:", e)
        return {"statusCode": 500, "body": "DB scan failure"}

    if not items:
        return {"statusCode": 404, "body": "No records to enrich"}

    # 3️⃣ group by project_id (single-key table)
    grouped = defaultdict(list)
    for it in items:
        pid = it.get("project_id")
        if pid:
            grouped[pid].append(it)

    # 4️⃣ enrich each group
    for project_id, cards in grouped.items():
        print(f"🔄 Enriching project: {project_id}")
        for it in cards:
            card_id   = it.get("card_id")
            raw_creator = it.get("creator")
            client_email = ""
            pm_email     = ""
            # extract client email from a “Client_Email” card’s comments[]
            if it.get("title") == "Client_Email" and isinstance(it.get("comments"), list):
                client_email = it["comments"][0]

            # parse creator JSON → id
            creator_id = None
            try:
                creator_dict = json.loads(raw_creator.replace("'", '"'))
                creator_id   = creator_dict.get("id")
            except:
                pass

            if creator_id:
                pm_email = get_pm_email(project_id, token, creator_id)

            new_token = str(uuid4())
            timestamp = int(time.time())

            # 5️⃣ write back — **ONLY** project_id is your table’s PK
            try:
                table.update_item(
                    Key={"project_id": project_id},
                    UpdateExpression=(
                        "SET client_email=:ce, pm_email=:pe, "
                        "approval_token=:at, sent_timestamp=:ts, #s=:st"
                    ),
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":ce": client_email,
                        ":pe": pm_email,
                        ":at": new_token,
                        ":ts": timestamp,
                        ":st": "pending"
                    }
                )
                print(f"✅ Updated project {project_id} (card {card_id})")
            except Exception as e:
                print(f"❌ Error updating {card_id}:", e)

    print("✅ Enrichment complete for all projects.")
    return {"statusCode": 200, "body": "OK"}
