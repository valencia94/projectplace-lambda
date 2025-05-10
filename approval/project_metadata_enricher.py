#!/usr/bin/env python3
"""
ProjectPlace → DynamoDB enrichment Lambda   v2.3  (2025-05-10)

ENV VARS
────────────────────────────────────────────────────────────────────────────
AWS_REGION                (auto from Lambda)
DYNAMODB_ENRICHMENT_TABLE ProjectPlace_DataEnrichment_table_v2
PROJECTPLACE_SECRET_NAME  ProjectPlaceAPICredentials   # optional override
DRY_RUN                   "1" skips writes             # optional
"""

import os, json, time, re, backoff, boto3, urllib.error
from urllib import request, parse

# ─────────── ENV / CLIENTS ───────────
REGION      = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME  = os.environ["DYNAMODB_ENRICHMENT_TABLE"].strip()
SECRET_NAME = os.getenv("PROJECTPLACE_SECRET_NAME",
                         "ProjectPlaceAPICredentials")
API_BASE    = "https://api.projectplace.com"
DRY_RUN     = os.getenv("DRY_RUN", "0") == "1"

ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
sm  = boto3.client("secretsmanager", region_name=REGION)

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)

# ─────────── helpers ───────────
def _http(url: str, tok: str = None) -> dict | list:
    hdrs = {"Authorization": f"Bearer {tok}"} if tok else {}
    req  = request.Request(url, headers=hdrs)
    with request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

def _token() -> str:
    creds = json.loads(sm.get_secret_value(SecretId=SECRET_NAME)
                       ["SecretString"])
    data  = parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     creds["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": creds["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
    }).encode()
    req = request.Request(f"{API_BASE}/oauth2/access_token", data=data,
                          headers={"Content-Type":
                                   "application/x-www-form-urlencoded"})
    return _http(req.full_url) if False else json.loads(
        request.urlopen(req).read())["access_token"]

@backoff.on_exception(backoff.expo,
                      (urllib.error.HTTPError, urllib.error.URLError),
                      max_tries=5,
                      giveup=lambda e: getattr(e, 'code', 500) < 500)
def _cards(pid: str, tok: str):        # retry on 5xx / rate-limit
    return _http(f"{API_BASE}/1/projects/{pid}/cards", tok)

def _comments(cid: str, tok: str):
    return _http(f"{API_BASE}/1/cards/{cid}/comments", tok)

def _pm_email(pid: str, tok: str, creator_id: str) -> tuple[str, str]:
    for m in _http(f"{API_BASE}/1/projects/{pid}/members", tok):
        if str(m.get("id")) == str(creator_id):
            return m.get("email", ""), m.get("name", "")
    return "", ""

# ─────────── Lambda handler ───────────
def lambda_handler(event=None, context=None):
    start = time.time()
    tok   = _token()

    try:
        projects = {i["project_id"] for i in ddb.scan()["Items"]}
    except Exception as e:
        print("❌ Dynamo scan failed:", e)
        return {"statusCode": 500, "body": "Dynamo scan failure"}

    rows = 0
    for pid in projects:
        for card in _cards(pid, tok):
            cid        = str(card["id"])
            title      = card.get("title", "")
            creator_id = card.get("creator", {}).get("id")
            comment_cnt = card.get("comment_count") or 0
            comments   = _comments(cid, tok) if comment_cnt else []

            # client_email logic
            if title == "Client_Email" and comments:
                client_email = comments[0].get("text", "")
            else:
                client_email = next(
                    (EMAIL_RE.search(c.get("text", "")).group(0)
                     for c in comments if EMAIL_RE.search(c.get("text", ""))),
                    "")

            pm_email, pm_name = _pm_email(pid, tok, creator_id)

            attr = {
                ":title":       title,
                ":description": card.get("description"),
                ":client_email": client_email,
                ":pm_email":    pm_email,
                ":pm_name":     pm_name,
                ":board_id":    str(card.get("board_id")),
                ":board_name":  card.get("board_name"),
                ":column_id":   card.get("column_id"),
                ":is_done":     card.get("is_done"),
                ":is_blocked":  card.get("is_blocked"),
                ":blocked_reason": card.get("is_blocked_reason"),
                ":checklist":   card.get("checklist", []),
                ":comments":    comments,
                ":progress":    card.get("progress"),
                ":direct_url":  card.get("direct_url"),
                ":now":         int(time.time()),
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
                ExpressionAttributeValues=attr)

            rows += 1
            time.sleep(0.05)                 # API-friendly pause

    print(f"✅ Enriched {rows} cards in {int(time.time() - start)} s")
    return {"statusCode": 200,
            "body": f"Enriched {rows} cards in "
                    f"{int(time.time() - start)} s"}
