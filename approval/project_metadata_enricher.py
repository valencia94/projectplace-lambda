#!/usr/bin/env python3
"""
ProjectPlace → DynamoDB enrichment Lambda   v2.5  (2025-05-10)

• Writes all requested card fields + comments to table_v2
• No external dependencies (runs on stock Python 3.9 Lambda)
• Retries ProjectPlace requests up to 5× with exponential back-off
"""

import os, json, time, re, random, boto3, urllib.error
from typing import Any, Dict, List, Optional
from urllib import request, parse

# ─── ENV / CLIENTS ──────────────────────────────────────────────────
REGION      = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME  = os.environ["DYNAMODB_ENRICHMENT_TABLE"].strip()
SECRET_NAME = os.getenv("PROJECTPLACE_SECRET_NAME",
                         "ProjectPlaceAPICredentials")
API_BASE    = "https://api.projectplace.com"
DRY_RUN     = os.getenv("DRY_RUN", "0") == "1"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
ddb_tbl  = dynamodb.Table(TABLE_NAME)
secrets  = boto3.client("secretsmanager", region_name=REGION)

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)

# ─── tiny retry wrapper ─────────────────────────────────────────────
def _http(url: str, token: Optional[str] = None) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    delay   = 0.8
    last_exc: Exception | None = None
    for _ in range(5):
        try:
            req = request.Request(url, headers=headers)
            with request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code != 429:
                raise
            last_exc = exc
            time.sleep(delay + random.random() * 0.3)
            delay *= 1.8
    raise last_exc or RuntimeError("HTTP retries exhausted")

# ─── ProjectPlace helpers ───────────────────────────────────────────
def _token() -> str:
    sec = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    data = parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     sec["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": sec["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
    }).encode()
    req = request.Request(f"{API_BASE}/oauth2/access_token", data=data,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    return json.loads(request.urlopen(req).read())["access_token"]

_cards    = lambda pid, tok: _http(f"{API_BASE}/1/projects/{pid}/cards", tok)
_comments = lambda cid, tok: _http(f"{API_BASE}/1/cards/{cid}/comments", tok)
_members  = lambda pid, tok: _http(f"{API_BASE}/1/projects/{pid}/members", tok)

def _pm_email(pid: str, tok: str, creator_id: str) -> tuple[str, str]:
    for mem in _members(pid, tok):
        if str(mem.get("id")) == str(creator_id):
            return mem.get("email", ""), mem.get("name", "")
    return "", ""

# ─── Lambda handler ────────────────────────────────────────────────
def lambda_handler(event=None, context=None):
    start = time.time()
    token = _token()

# -- collect every project_id (resource paginator -> plain strings) --
project_ids = set()
scan_kwargs = {"ProjectionExpression": "project_id"}
resp = ddb_tbl.scan(**scan_kwargs)
project_ids.update(item["project_id"] for item in resp["Items"])

while "LastEvaluatedKey" in resp:
    resp = ddb_tbl.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **scan_kwargs)
    project_ids.update(item["project_id"] for item in resp["Items"])

    row_count = 0
    for pid in project_ids:
        for card in _cards(pid, token):
            cid   = str(card["id"])
            title = card.get("title", "")
            # Fetch comments only if count > 0
            comments = _comments(cid, token) if card.get("comment_count", 0) else []

            # client_email extraction logic
            if title == "Client_Email" and comments:
                client_email = comments[0].get("text", "")
            else:
                client_email = next(
                    (EMAIL_RE.search(c.get("text", "")).group(0)
                     for c in comments if EMAIL_RE.search(c.get("text", ""))),
                    ""
                )

            pm_email, pm_name = _pm_email(pid, token, card.get("creator", {}).get("id"))

            attr: Dict[str, Any] = {
                # always-needed fields
                ":title":               title,
                ":description":         card.get("description"),
                ":client_email":        client_email,
                ":pm_email":            pm_email,
                ":pm_name":             pm_name,
                ":now":                 int(time.time()),
                # new extended fields
                ":assignee":            card.get("assignee"),
                ":assignee_id":         card.get("assignee_id"),
                ":board_id":            str(card.get("board_id")),
                ":board_name":          card.get("board_name"),
                ":connected_issues":    card.get("connected_issues"),
                ":connected_risks":     card.get("connected_risks"),
                ":contributors":        card.get("contributors"),
                ":created_time":        card.get("created_time"),
                ":creator":             card.get("creator"),
                ":dependencies":        card.get("dependencies"),
                ":planlet":             card.get("planlet"),
                ":planlet_id":          card.get("planlet_id"),
                ":progress":            card.get("progress"),
                ":project":             card.get("project"),
                ":reported_time":       card.get("reported_time"),
                ":comments":            comments,
                ":direct_url":          card.get("direct_url"),
                ":is_done":             card.get("is_done"),
                ":is_blocked":          card.get("is_blocked"),
                ":blocked_reason":      card.get("is_blocked_reason"),
                ":checklist":           card.get("checklist", []),
                ":column_id":           card.get("column_id"),
                ":board_display_order": card.get("display_order"),
            }

            if not DRY_RUN:
                ddb_tbl.update_item(
                    Key={"project_id": str(pid), "card_id": cid},
                    UpdateExpression="""
                        SET title               = :title,
                            description         = :description,
                            client_email        = :client_email,
                            pm_email            = :pm_email,
                            pm_name             = :pm_name,
                            assignee            = :assignee,
                            assignee_id         = :assignee_id,
                            board_id            = :board_id,
                            board_name          = :board_name,
                            connected_issues    = :connected_issues,
                            connected_risks     = :connected_risks,
                            contributors        = :contributors,
                            created_time        = :created_time,
                            creator             = :creator,
                            dependencies        = :dependencies,
                            planlet             = :planlet,
                            planlet_id          = :planlet_id,
                            progress            = :progress,
                            project             = :project,
                            reported_time       = :reported_time,
                            comments            = :comments,
                            direct_url          = :direct_url,
                            is_done             = :is_done,
                            is_blocked          = :is_blocked,
                            is_blocked_reason   = :blocked_reason,
                            checklist           = :checklist,
                            column_id           = :column_id,
                            board_display_order = :board_display_order,
                            last_refreshed      = :now
                    """,
                    ExpressionAttributeValues=attr,
                )

            row_count += 1
            time.sleep(0.05)  # gentle on API

    elapsed = int(time.time() - start)
    msg = f"Enriched {row_count} cards in {elapsed} s"
    print(f"✅ {msg}")
    return {"statusCode": 200, "body": msg}
