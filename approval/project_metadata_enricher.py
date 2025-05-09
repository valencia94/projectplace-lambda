#!/usr/bin/env python3
import os, json, time, uuid, boto3
from urllib import request, parse, error

REGION       = os.environ["AWS_REGION"]
TABLE_NAME   = os.environ["DYNAMODB_ENRICHMENT_TABLE"]
SECRET_NAME  = os.environ.get("PROJECTPLACE_SECRET_NAME", "ProjectPlaceAPICredentials")
API_BASE_URL = "https://api.projectplace.com"

ddb     = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
secrets = boto3.client("secretsmanager", region_name=REGION)

def get_projectplace_token():
    creds = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    data = parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": creds["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": creds["PROJECTPLACE_ROBOT_CLIENT_SECRET"]
    }).encode()
    req = request.Request(f"{API_BASE_URL}/oauth2/access_token", data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]

def get_pm_email(project_id, token, creator_id):
    url = f"{API_BASE_URL}/1/projects/{project_id}/members"
    req = request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with request.urlopen(req) as resp:
            members = json.loads(resp.read())
            for m in members:
                if str(m.get("id")) == str(creator_id):
                    return m.get("email", ""), m.get("name", "")
    except error.HTTPError as e:
        print("⚠️ Member fetch failed:", e.read().decode())
    return "", ""

def get_all_cards(project_id, token):
    url = f"{API_BASE_URL}/1/projects/{project_id}/cards"
    req = request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    with request.urlopen(req) as resp:
        return json.loads(resp.read())

def lambda_handler(event=None, context=None):
    start_time = time.time()
    print("🚀 Starting full enrichment...")

    try:
        token = get_projectplace_token()
    except Exception as e:
        print("❌ Auth failed:", e)
        return {"statusCode": 500, "body": "Auth failure"}

    try:
        resp = ddb.scan()
        items = resp.get("Items", [])
        project_ids = list(set(i["project_id"] for i in items if "project_id" in i))
    except Exception as e:
        print("❌ DynamoDB scan failed:", e)
        return {"statusCode": 500, "body": "Dynamo scan failure"}

    if not project_ids:
        return {"statusCode": 404, "body": "No projects found"}

    for project_id in project_ids:
        if time.time() - start_time > 540:
            print("⏰ Timeout limit reached. Ending early.")
            break

        print(f"🔄 Enriching project: {project_id}")
        try:
            cards = get_all_cards(project_id, token)
            for card in cards:
                card_id = card.get("id")
                title = card.get("title", "")
                comments = card.get("comments", [])
                creator = card.get("creator", {})
                creator_id = creator.get("id")

                client_email = comments[0] if title == "Client_Email" and isinstance(comments, list) and comments else ""
                pm_email, pm_name = get_pm_email(project_id, token, creator_id)

                enriched_item = {
                    "project_id": str(project_id),
                    "card_id": str(card_id),
                    "title": title,
                    "description": card.get("description"),
                    "creator_id": str(creator_id),
                    "created_time": card.get("created_time"),
                    "client_email": client_email,
                    "pm_email": pm_email,
                    "pm_name": pm_name,
                    "board_id": card.get("board_id"),
                    "board_name": card.get("board_name"),
                    "column_id": card.get("column_id"),
                    "is_done": card.get("is_done"),
                    "is_blocked": card.get("is_blocked"),
                    "is_blocked_reason": card.get("is_blocked_reason"),
                    "checklist": card.get("checklist", []),
                    "comments": comments,
                    "progress": card.get("progress"),
                    "direct_url": card.get("direct_url"),
                    "approval_token": str(uuid.uuid4()),
                    "sent_timestamp": int(time.time()),
                    "status": "pending"
                }

                ddb.put_item(Item=enriched_item)
                print(f"✅ Updated card: {card_id}")
                time.sleep(0.05)  # ⚡ Optimized for throughput
        except Exception as e:
            print(f"❌ Failed enrichment for project {project_id}: {e}")
            continue

    print("✅ Enrichment complete for all projects.")
    return {"statusCode": 200, "body": "Enrichment complete."}
