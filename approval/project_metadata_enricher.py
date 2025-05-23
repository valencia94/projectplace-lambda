#!/usr/bin/env python3
"""
ProjectPlace → DynamoDB enrichment Lambda   v3.0  (2025-05-11)

• Pulls every card for every project present in table v2
• Stores extended card fields + full comments array
• Converts floats → Decimal, or string if > 1e38 / ±Inf / NaN
• Aliases reserved word  #project
• Uses paginated scan so no project is skipped
• No external libraries (Python 3.9 stock)
"""


import json, os, re, time, random, math, urllib.error
from decimal import Decimal, InvalidOperation
from typing  import Any, Dict, Optional
from urllib  import request, parse
import boto3
from boto3.dynamodb.conditions import Key           # ← **ADD THIS**

# ─────────── ENV / CLIENTS ──────────────────────────────────────────
REGION      = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME  = os.environ["DYNAMODB_ENRICHMENT_TABLE"].strip()
API_BASE    = "https://api.projectplace.com"
SECRET_NAME = os.getenv("PROJECTPLACE_SECRET_NAME",
                         "ProjectPlaceAPICredentials")
DRY_RUN     = os.getenv("DRY_RUN", "0") == "1"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table    = dynamodb.Table(TABLE_NAME)
secrets  = boto3.client("secretsmanager", region_name=REGION)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)

# ─────────── helpers ────────────────────────────────────────────────
def _http(url: str, token: Optional[str] = None) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    delay = 0.8
    for _ in range(5):
        try:
            with request.urlopen(request.Request(url, headers=headers), timeout=10) as r:
                return json.loads(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429:
                raise
            time.sleep(delay + random.random() * 0.3)
            delay *= 1.8
    raise RuntimeError("HTTP retries exhausted")

def _token() -> str:
    sec = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    body = parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     sec["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": sec["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
    }).encode()
    with request.urlopen(request.Request(f"{API_BASE}/oauth2/access_token", data=body,
                                         headers={"Content-Type":"application/x-www-form-urlencoded"})) as r:
        return json.loads(r.read())["access_token"]

# ProjectPlace one-liners
g_cards    = lambda pid,tok: _http(f"{API_BASE}/1/projects/{pid}/cards", tok)
g_comments = lambda cid,tok: _http(f"{API_BASE}/1/cards/{cid}/comments", tok)
g_members  = lambda pid,tok: _http(f"{API_BASE}/1/projects/{pid}/members", tok)

def _pm_email(pid: str, tok: str, creator: Any) -> tuple[str,str]:
    for m in g_members(pid, tok):
        if str(m.get("id")) == str(creator):
            return m.get("email",""), m.get("name","")
    return "",""

# Decimal-safe conversion
def _d(val: Any) -> Any:
    if isinstance(val, float):
        if math.isfinite(val) and abs(val) < 1e38:
            try:
                return Decimal(str(val))
            except (InvalidOperation, OverflowError):
                return str(val)
        return str(val)                     # huge / inf / nan
    if isinstance(val, list):
        return [_d(v) for v in val]
    if isinstance(val, dict):
        return {k: _d(v) for k, v in val.items()}
    return val                              # int, str, bool, None

# ─────────── Lambda handler ────────────────────────────────────────
def lambda_handler(event=None, context=None):
    token = _token()
    start = time.time()

    # full-table project list (paginated)
    projects: set[str] = set()
    resp = table.scan(ProjectionExpression="project_id")
    projects.update(item["project_id"] for item in resp["Items"])
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"],
                          ProjectionExpression="project_id")
        projects.update(item["project_id"] for item in resp["Items"])

    rows = 0
    for pid in projects:
        for card in g_cards(pid, token):
            cid   = str(card["id"])
            title = card.get("title","")
            comments = g_comments(cid, token) if card.get("comment_count") else []

            # client_email logic
            if title == "Client_Email" and comments:
                client_email = comments[0].get("text","")
            else:
                client_email = next(
                    (m.group(0) for m in
                     (EMAIL_RE.search(c.get("text","")) for c in comments) if m),
                    ""
                )

            pm_email, pm_name = _pm_email(pid, token, card.get("creator",{}).get("id"))

            attr: Dict[str,Any] = {
                ":title":          title,
                ":description":    card.get("description"),
                ":client_email":   client_email,
                ":pm_email":       pm_email,
                ":pm_name":        pm_name,
                ":assignee":       card.get("assignee"),
                ":assignee_id":    card.get("assignee_id"),
                ":board_id":       str(card.get("board_id")),
                ":board_name":     card.get("board_name"),
                ":connected_issues": _d(card.get("connected_issues")),
                ":connected_risks":  _d(card.get("connected_risks")),
                ":contributors":     _d(card.get("contributors")),
                ":created_time":     card.get("created_time"),
                ":creator":          _d(card.get("creator")),
                ":dependencies":     _d(card.get("dependencies")),
                ":planlet":          _d(card.get("planlet")),
                ":planlet_id":       card.get("planlet_id"),
                ":progress":         _d(card.get("progress")),
                ":project_val":      _d(card.get("project")),  # alias
                ":reported_time":    card.get("reported_time"),
                ":comments":         _d(comments),             # full comments stored
                ":direct_url":       card.get("direct_url"),
                ":is_done":          card.get("is_done"),
                ":is_blocked":       card.get("is_blocked"),
                ":blocked_reason":   card.get("is_blocked_reason"),
                ":checklist":        _d(card.get("checklist", [])),
                ":column_id":        card.get("column_id"),
                ":board_display_order": _d(card.get("display_order")),
                ":now": int(time.time()),
            }

            if not DRY_RUN:
                table.update_item(
                    Key={"project_id": pid, "card_id": cid},
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
                            #project            = :project_val,
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
                    ExpressionAttributeNames={"#project":"project"},
                    ExpressionAttributeValues=attr
                )

            rows += 1
            time.sleep(0.05)      # friendly to ProjectPlace API

    print(f"✅ Enriched {rows} cards in {int(time.time()-start)} s")
    return {"statusCode":200,
            "body": f"Enriched {rows} cards in {int(time.time()-start)} s"}
