#!/usr/bin/env python3
"""
ProjectPlace → DynamoDB enrichment Lambda   v2.1  (2025-05-10)
"""

import os, json, time, uuid, boto3
from urllib import request, parse, error

REGION       = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME   = os.environ["DYNAMODB_ENRICHMENT_TABLE"]      # table v2
SECRET_NAME  = os.getenv("PROJECTPLACE_SECRET_NAME",
                          "ProjectPlaceAPICredentials")
API_BASE     = "https://api.projectplace.com"
DRY_RUN      = os.getenv("DRY_RUN", "0") == "1"

ddb     = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
sm      = boto3.client("secretsmanager", region_name=REGION)

# ───────────────────────── helpers ─────────────────────────
def _token():
    creds = json.loads(sm.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    data = parse.urlencode({"grant_type":"client_credentials",
                            "client_id":creds["PROJECTPLACE_ROBOT_CLIENT_ID"],
                            "client_secret":creds["PROJECTPLACE_ROBOT_CLIENT_SECRET"]
                           }).encode()
    req  = request.Request(f"{API_BASE}/oauth2/access_token", data=data,
                           headers={"Content-Type":"application/x-www-form-urlencoded"})
    with request.urlopen(req) as r:
        return json.loads(r.read())["access_token"]

def _pm_email(pid, token, creator):
    url = f"{API_BASE}/1/projects/{pid}/members"
    try:
        with request.urlopen(request.Request(url,
                   headers={"Authorization":f"Bearer {token}"})) as r:
            for m in json.loads(r.read()):
                if str(m.get("id")) == str(creator):
                    return m.get("email",""), m.get("name","")
    except error.HTTPError as e:
        print("⚠️ member fetch failed:", e.read().decode())
    return "", ""

def _cards(pid, token):
    url = f"{API_BASE}/1/projects/{pid}/cards"
    with request.urlopen(request.Request(url,
            headers={"Authorization":f"Bearer {token}"})) as r:
        return json.loads(r.read())

# ───────────────────────── handler ────────────────────────
def lambda_handler(event=None, context=None):
    start = time.time()
    token = _token()
    projects = {i["project_id"] for i in ddb.scan()["Items"]}

    wrote = 0
    for pid in projects:
        for card in _cards(pid, token):
            cid   = str(card["id"])
            title = card.get("title","")
            comments   = card.get("comments",[])
            creator_id = card.get("creator",{}).get("id")

            client_email = comments[0] if title=="Client_Email" and comments else ""
            pm_email, pm_name = _pm_email(pid, token, creator_id)

            attr = {":title":title,
                    ":description": card.get("description"),
                    ":client_email": client_email,
                    ":pm_email": pm_email,
                    ":pm_name": pm_name,
                    ":board_id": str(card.get("board_id")),
                    ":board_name": card.get("board_name"),
                    ":column_id": card.get("column_id"),
                    ":is_done": card.get("is_done"),
                    ":is_blocked": card.get("is_blocked"),
                    ":blocked_reason": card.get("is_blocked_reason"),
                    ":checklist": card.get("checklist",[]),
                    ":comments": comments,
                    ":progress": card.get("progress"),
                    ":direct_url": card.get("direct_url"),
                    ":now": int(time.time())}

            if DRY_RUN:
                continue

            ddb.update_item(
                Key={"project_id": str(pid), "card_id": cid},
                UpdateExpression="""SET title=:title, description=:description,
                    client_email=:client_email, pm_email=:pm_email, pm_name=:pm_name,
                    board_id=:board_id, board_name=:board_name, column_id=:column_id,
                    is_done=:is_done, is_blocked=:is_blocked,
                    is_blocked_reason=:blocked_reason, checklist=:checklist,
                    comments=:comments, progress=:progress, direct_url=:direct_url,
                    last_refreshed=:now""",
                ExpressionAttributeValues=attr)

            wrote += 1
            time.sleep(0.05)

    return {"statusCode": 200,
            "body": f"Enriched {wrote} cards in {int(time.time()-start)} s"}
