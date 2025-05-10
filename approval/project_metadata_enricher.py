#!/usr/bin/env python3
"""
ProjectPlace → DynamoDB enrichment Lambda   v2.8  (2025-05-11)

* Pulls every card for every project in table_v2 (paginated)
* Writes extended fields + comments
* Converts floats → Decimal
* Aliases reserved word  #project  in UpdateExpression
* Zero external libraries – runs on stock Python 3.9
"""

import json, os, re, time, random, decimal, boto3, urllib.error
from typing import Any, Dict, Optional
from urllib import request, parse
from decimal import Decimal

# ─── ENV ────────────────────────────────────────────────────────────
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

# ─── helpers ────────────────────────────────────────────────────────
def _http(url: str, token: Optional[str] = None) -> Any:
    hdr = {"Authorization": f"Bearer {token}"} if token else {}
    delay, last = 0.8, None
    for _ in range(5):
        try:
            with request.urlopen(request.Request(url, headers=hdr), timeout=10) as r:
                return json.loads(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429:
                raise
            last = e
            time.sleep(delay + random.random()*0.3)
            delay *= 1.8
    raise last

def _token() -> str:
    cred = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    data = parse.urlencode({
        "grant_type": "client_credentials",
        "client_id":  cred["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": cred["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
    }).encode()
    with request.urlopen(request.Request(f"{API_BASE}/oauth2/access_token", data=data,
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})) as r:
        return json.loads(r.read())["access_token"]

get_cards    = lambda pid, tok: _http(f"{API_BASE}/1/projects/{pid}/cards", tok)
get_comments = lambda cid, tok: _http(f"{API_BASE}/1/cards/{cid}/comments", tok)
get_members  = lambda pid, tok: _http(f"{API_BASE}/1/projects/{pid}/members", tok)

def _pm_email(pid: str, tok: str, creator_id: Any) -> tuple[str, str]:
    for m in get_members(pid, tok):
        if str(m.get("id")) == str(creator_id):
            return m.get("email", ""), m.get("name", "")
    return "", ""

def _to_dynamo(val: Any) -> Any:
    if isinstance(val, float):
        return Decimal(str(val))
    if isinstance(val, list):
        return [_to_dynamo(v) for v in val]
    if isinstance(val, dict):
        return {k: _to_dynamo(v) for k, v in val.items()}
    return val  # int, str, bool, None

# ─── Lambda handler ────────────────────────────────────────────────
def lambda_handler(event=None, context=None):
    start = time.time()
    token = _token()

    # gather every project_id via paginated scan
    project_ids = set()
    scan_kwargs = {"ProjectionExpression": "project_id"}
    resp = table.scan(**scan_kwargs)
    project_ids.update(item["project_id"] for item in resp["Items"])
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **scan_kwargs)
        project_ids.update(item["project_id"] for item in resp["Items"])

    rows = 0
    for pid in project_ids:
        for card in get_cards(pid, token):
            cid   = str(card["id"])
            title = card.get("title", "")
            comments = get_comments(cid, token) if card.get("comment_count") else []

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
                ":title":               title,
                ":description":         card.get("description"),
                ":client_email":        client_email,
                ":pm_email":            pm_email,
                ":pm_name":             pm_name,
                ":assignee":            card.get("assignee"),
                ":assignee_id":         card.get("assignee_id"),
                ":board_id":            str(card.get("board_id")),
                ":board_name":          card.get("board_name"),
                ":connected_issues":    _to_dynamo(card.get("connected_issues")),
                ":connected_risks":     _to_dynamo(card.get("connected_risks")),
                ":contributors":        _to_dynamo(card.get("contributors")),
                ":created_time":        card.get("created_time"),
                ":creator":             _to_dynamo(card.get("creator")),
                ":dependencies":        _to_dynamo(card.get("dependencies")),
                ":planlet":             _to_dynamo(card.get("planlet")),
                ":planlet_id":          card.get("planlet_id"),
                ":progress":            _to_dynamo(card.get("progress")),
                ":project_val":         _to_dynamo(card.get("project")),  # rename in attr
                ":reported_time":       card.get("reported_time"),
                ":comments":            _to_dynamo(comments),
                ":direct_url":          card.get("direct_url"),
                ":is_done":             card.get("is_done"),
                ":is_blocked":          card.get("is_blocked"),
                ":blocked_reason":      card.get("is_blocked_reason"),
                ":checklist":           _to_dynamo(card.get("checklist", [])),
                ":column_id":           card.get("column_id"),
                ":board_display_order": _to_dynamo(card.get("display_order")),
                ":now":                 int(time.time()),
            }

            expr_names = {"#project": "project"}  # alias reserved word

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
                    ExpressionAttributeValues=attr,
                    ExpressionAttributeNames=expr_names,
                )

            rows += 1
            time.sleep(0.05)

    msg = f"Enriched {rows} cards in {int(time.time()-start)} s"
    print(f"✅ {msg}")
    return {"statusCode": 200, "body": msg}
