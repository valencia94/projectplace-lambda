
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
    secret = secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"]
    creds = json.loads(secret)
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

def lambda_handler(event, context):
    project_id = event.get("project_id")
    if not project_id:
        return {"statusCode": 400, "body": "Missing project_id"}

    print(f"🚀 Enriching ProjectPlace project: {project_id}")
    start_time = time.time()

    try:
        token = get_projectplace_token()
    except Exception as e:
        print("❌ Token retrieval failed:", e)
        return {"statusCode": 500, "body": "Token error"}

    try:
        cards = get_all_cards(project_id, token)
    except Exception as e:
        print(f"❌ Card fetch failed for {project_id}:", e)
        return {"statusCode": 500, "body": "Card fetch error"}

    updated = 0
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
        updated += 1
        print(f"✅ Updated card: {card_id}")
        time.sleep(0.5)

    elapsed = time.time() - start_time
    print(f"✅ Enrichment complete: {updated} cards in {elapsed:.1f}s")
    return {
        "statusCode": 200,
        "body": json.dumps({"project_id": project_id, "cards_updated": updated, "duration": elapsed})
    }
