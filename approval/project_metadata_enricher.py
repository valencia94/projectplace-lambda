import os, json, boto3, time
from urllib import request, parse, error
from uuid import uuid4
from datetime import datetime

API_BASE_URL = "https://api.projectplace.com"

REGION      = os.environ["AWS_REGION"]
TABLE_NAME  = os.environ["DYNAMODB_ENRICHMENT_TABLE"]
SECRET_NAME = os.environ["SECRET_NAME"]

dynamodb = boto3.resource("dynamodb", region_name=REGION)
secrets  = boto3.client("secretsmanager", region_name=REGION)
table    = dynamodb.Table(TABLE_NAME)

def get_token():
    sec = secrets.get_secret_value(SecretId=SECRET_NAME)
    creds = json.loads(sec["SecretString"])
    data = parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     creds["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": creds["PROJECTPLACE_ROBOT_CLIENT_SECRET"]
    }).encode()
    req = request.Request(f"{API_BASE_URL}/oauth2/access_token", data=data)
    try:
        with request.urlopen(req) as resp:
            return json.loads(resp.read())["access_token"]
    except Exception as e:
        print(f"[ERROR] Token request failed: {e}")
        return None

def get_all_projects(token):
    url = f"{API_BASE_URL}/1/projects"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with request.urlopen(req) as resp:
        return json.loads(resp.read())

def get_all_cards(project_id, token):
    url = f"{API_BASE_URL}/1/projects/{project_id}/cards"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with request.urlopen(req) as resp:
        return json.loads(resp.read())

def get_pm_email(project_id, token, creator_id):
    url = f"{API_BASE_URL}/1/projects/{project_id}/members"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with request.urlopen(req) as resp:
            members = json.loads(resp.read())
            for m in members:
                if str(m.get("id")) == str(creator_id):
                    return m.get("email")
    except:
        return None

def lambda_handler(event=None, context=None):
    print("🚀 Starting full enrichment...")
    token = get_token()
    if not token:
        return {"statusCode": 500, "body": "Token request failed"}

    try:
        projects = get_all_projects(token)
    except error.HTTPError as e:
        print(f"[ERROR] Project fetch failed: {e}")
        return {"statusCode": 500, "body": f"Failed to fetch projects: {e}"}

    for p in projects:
        pid = str(p["id"])
        print(f"🔄 Enriching project: {pid}")
        try:
            cards = get_all_cards(pid, token)
            for c in cards:
                cid   = str(c.get("id"))
                title = c.get("title", "")
                comms = c.get("comments", [])
                creator = c.get("created_by", {})
                creator_id = creator.get("id")
                pm_email = get_pm_email(pid, token, creator_id) if creator_id else ""

                item = {
                    "project_id": pid,
                    "card_id":    cid,
                    "title":      title,
                    "client_email": comms[0] if title == "Client_Email" and comms else "",
                    "pm_email":   pm_email,
                    "approval_token": str(uuid4()),
                    "sent_timestamp": int(time.time()),
                    "status":     "pending"
                }
                table.put_item(Item=item)
                print(f"✅ Updated card: {cid}")
        except Exception as e:
            print(f"[ERROR] Project {pid} failed: {e}")
            continue

    print("✅ Enrichment complete.")
    return {"statusCode": 200, "body": "All projects enriched"}
