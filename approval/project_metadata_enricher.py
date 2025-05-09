#!/usr/bin/env python3
"""
ProjectPlace → DynamoDB all-projects enricher (v1.1 – 2025-05-09)

Upgrades
--------
✓ Logs table name & write-count
✓ Idempotent writes (ConditionExpression)
✓ Optional DRY_RUN mode for QA (env var DRY_RUN=1)
✓ Same 9-minute self-cutoff; prints total items written
"""
import os, json, time, uuid, boto3
from urllib import request, parse, error

REGION       = os.environ["AWS_REGION"]
TABLE_NAME   = os.environ["DYNAMODB_ENRICHMENT_TABLE"]
SECRET_NAME  = os.environ.get("PROJECTPLACE_SECRET_NAME", "ProjectPlaceAPICredentials")
API_BASE_URL = "https://api.projectplace.com"
DRY_RUN      = os.environ.get("DRY_RUN", "0") == "1"

ddb     = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
secrets = boto3.client("secretsmanager", region_name=REGION)

# ---------- helpers ---------------------------------------------------------

def get_projectplace_token() -> str:
    creds = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    data  = parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     creds["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": creds["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
    }).encode()
    req = request.Request(f"{API_BASE_URL}/oauth2/access_token", data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]

def get_pm_email(project_id: str, token: str, creator_id: str) -> tuple[str, str]:
    url = f"{API_BASE_URL}/1/projects/{project_id}/members"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with request.urlopen(req) as resp:
            for m in json.loads(resp.read()):
                if str(m.get("id")) == str(creator_id):
                    return m.get("email", ""), m.get("name", "")
    except error.HTTPError as e:
        print("⚠️ Member fetch failed:", e.read().decode())
    return "", ""

def get_all_cards(project_id: str, token: str) -> list[dict]:
    url = f"{API_BASE_URL}/1/projects/{project_id}/cards"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with request.urlopen(req) as resp:
        return json.loads(resp.read())

# ---------- Lambda handler --------------------------------------------------

def lambda_handler(event=None, context=None):
    start = time.time()
    print(f"🚀 Full enrichment run → table {TABLE_NAME} (dry_run={DRY_RUN})")

    try:
        token = get_projectplace_token()
    except Exception as e:
        print("❌ Auth failed:", e)
        return {"statusCode": 500, "body": "Auth failure"}

    try:
        project_ids = list({i["project_id"] for i in ddb.scan()["Items"]})
    except Exception as e:
        print("❌ Dynamo scan failed:", e)
        return {"statusCode": 500, "body": "Dynamo scan failure"}

    if not project_ids:
        return {"statusCode": 404, "body": "No projects found"}

    writes = 0
    for project_id in project_ids:
        if time.time() - start > 540:  # 9-minute safety cutoff
            print("⏰ Timeout limit reached – exiting loop")
            break

        print(f"🔄 Project {project_id}")
        try:
            for card in get_all_cards(project_id, token):
                cid        = card.get("id")
                title      = card.get("title", "")
                comments   = card.get("comments", [])
                creator_id = card.get("creator", {}).get("id")

                client_email = (
                    comments[0]
                    if title == "Client_Email" and isinstance(comments, list) and comments
                    else ""
                )
                pm_email, pm_name = get_pm_email(project_id, token, creator_id)

                item = {
                    "project_id":      str(project_id),
                    "card_id":         str(cid),
                    "title":           title,
                    "description":     card.get("description"),
                    "creator_id":      str(creator_id),
                    "created_time":    card.get("created_time"),
                    "client_email":    client_email,
                    "pm_email":        pm_email,
                    "pm_name":         pm_name,
                    "board_id":        card.get("board_id"),
                    "board_name":      card.get("board_name"),
                    "column_id":       card.get("column_id"),
                    "is_done":         card.get("is_done"),
                    "is_blocked":      card.get("is_blocked"),
                    "is_blocked_reason": card.get("is_blocked_reason"),
                    "checklist":       card.get("checklist", []),
                    "comments":        comments,
                    "progress":        card.get("progress"),
                    "direct_url":      card.get("direct_url"),
                    "approval_token":  str(uuid.uuid4()),
                    "sent_timestamp":  int(time.time()),
                    "status":          "pending",
                }

                if DRY_RUN:
                    print(f"(dry) Would upsert card {cid}")
                else:
                    ddb.put_item(
                        Item=item,
                        ConditionExpression="attribute_not_exists(card_id)"
                    )
                    writes += 1
                    print(f"✅ Upserted card {cid}")
                time.sleep(0.05)

        except Exception as e:
            print(f"❌ Failure for project {project_id}:", e)

    elapsed = int(time.time() - start)
    print(f"✅ Enrichment run complete – wrote {writes} items in {elapsed}s")
    return {"statusCode": 200, "body": f"Enriched {writes} cards in {elapsed}s"}
