#!/usr/bin/env python3
"""
ProjectPlace → DynamoDB enrichment Lambda   v2.2  (2025‑05‑10)

✓ Pulls every card for every project already in table_v2
✓ Fetches comments only when comment_count > 0  (extra call)
✓ Extracts first e‑mail‑looking text as client_email fallback
✓ Upserts rows (UpdateItem) so approval_status & tokens stay intact
"""

import os, json, time, re, boto3
from urllib import request, parse, error

# ─────────── ENV / CLIENTS ───────────
REGION       = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME   = os.environ["DYNAMODB_ENRICHMENT_TABLE"]          # table v2
SECRET_NAME  = os.getenv("PROJECTPLACE_SECRET_NAME",
                          "ProjectPlaceAPICredentials")
API_BASE     = "https://api.projectplace.com"
DRY_RUN      = os.getenv("DRY_RUN", "0") == "1"

ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
sm  = boto3.client("secretsmanager", region_name=REGION)

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
                      re.I)

# ─────────── helpers ───────────
def _token() -> str:
    creds = json.loads(sm.get_secret_value(SecretId=SECRET_NAME)
                       ["SecretString"])
    data = parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     creds["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": creds["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
    }).encode()
    req = request.Request(f"{API_BASE}/oauth2/access_token",
                          data=data,
                          headers={"Content-Type":
                                   "application/x-www-form-urlencoded"})
    with request.urlopen(req) as r:
        return json.loads(r.read())["access_token"]

def _cards(pid: str, tok: str) -> list[dict]:
    url = f"{API_BASE}/1/projects/{pid}/cards"
    with request.urlopen(request.Request(url,
            headers={"Authorization": f"Bearer {tok}"})) as r:
        return json.loads(r.read())

def _comments(cid: str, tok: str) -> list[dict]:
    url = f"{API_BASE}/1/cards/{cid}/comments"
    with request.urlopen(request.Request(url,
            headers={"Authorization": f"Bearer {tok}"})) as r:
        return json.loads(r.read())

def _pm_email(pid: str, tok: str, creator_id: str) -> tuple[str, str]:
    url = f"{API_BASE}/1/projects/{pid}/members"
    try:
        with request.urlopen(request.Request(url,
                 headers={"Authorization": f"Bearer {tok}"})) as r:
            for m in json.loads(r.read()):
                if str(m.get("id")) == str(creator_id):
                    return m.get("email", ""), m.get("name", "")
    except error.HTTPError as e:
        print("⚠️ member fetch failed:", e.read().decode())
    return "", ""

# ─────────── Lambda handler ───────────
def lambda_handler(event=None, context=None):
    start = time.time()
    tok = _token()

    # projects present in table_v2
    try:
        projects = {i["project_id"] for i in ddb.scan()["Items"]}
    except Exception as e:
        print("❌ Dynamo scan failed:", e)
        return {"statusCode": 500, "body": "Dynamo scan failure"}

    total_rows = 0
    for pid in projects:
        for card in _cards(pid, tok):
            cid   = str(card["id"])
            title = card.get("title", "")
            creator_id = card.get("creator", {}).get("id")

            # fetch comments only when needed
            c_cnt = card.get("comment_count", 0) or 0
            comments = _comments(cid, tok) if c_cnt else []

            # derive client_email: (a) special Client_Email card, OR
            # (b) first comment containing an e‑mail address
            client_email = ""
            if title == "Client_Email" and comments:
                client_email = comments[0].get("text", "")
            else:
                for c in comments:
                    match = EMAIL_RE.search(c.get("text", ""))
                    if match:
                        client_email = match.group(0)
                        break

            pm_email, pm_name = _pm_email(pid, tok, creator_id)

            attr = {
                ":title": title,
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
                ":checklist": card.get("checklist", []),
                ":comments": comments,
                ":progress": card.get("progress"),
                ":direct_url": card.get("direct_url"),
                ":now": int(time.time()),
            }

            if DRY_RUN:
                print(f"(dry) {pid}/{cid}")
                continue

            ddb.update_item(
                Key={"project_id": str(pid), "card_id": cid},
                UpdateExpression="""
                    SET title=:title, description=:description,
                        client_email=:client_email, pm_email=:pm_email,
                        pm_name=:pm_name, board_id=:board_id,
                        board_name=:board_name, column_id=:column_id,
                        is_done=:is_done, is_blocked=:is_blocked,
                        is_blocked_reason=:blocked_reason,
                        checklist=:checklist, comments=:comments,
                        progress=:progress, direct_url=:direct_url,
                        last_refreshed=:now
                """,
                ExpressionAttributeValues=attr,
            )

            total_rows += 1
            time.sleep(0.05)   # gentle on the API

    elapsed = int(time.time() - start)
    msg = f"Enriched {total_rows} cards in {elapsed}s"
    print(f"✅ {msg}")
    return {"statusCode": 200, "body": msg}
