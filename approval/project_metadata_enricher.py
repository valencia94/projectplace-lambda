#!/usr/bin/env python3
"""
ProjectPlace → DynamoDB enrichment Lambda   v2.8  (2025-05-11)

• Pulls every card for every project already in table v2
• Stores extended fields + comments
• Converts float → Decimal (Dynamo requirement)
• Aliases reserved word  #project
• Uses paginated scan so no project gets skipped
• No external libraries (Python 3.9 stock)
"""

import json, os, re, time, random, urllib.error, boto3
from decimal import Decimal
from typing   import Any, Dict, Optional
from urllib   import request, parse

# ── ENV ────────────────────────────────────────────────────────────
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

# ── HTTP helper with retry ─────────────────────────────────────────
def _http(url: str, token: Optional[str] = None) -> Any:
    hdr, delay = ({"Authorization": f"Bearer {token}"} if token else {}), 0.8
    for _ in range(5):
        try:
            with request.urlopen(request.Request(url, headers=hdr), timeout=10) as r:
                return json.loads(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429:
                raise
            time.sleep(delay + random.random()*0.3)
            delay *= 1.8
    raise RuntimeError("HTTP retries exhausted")

# ── ProjectPlace helpers ───────────────────────────────────────────
def _token() -> str:
    cred = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    body = parse.urlencode({
        "grant_type": "client_credentials",
        "client_id":  cred["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": cred["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
    }).encode()
    with request.urlopen(request.Request(f"{API_BASE}/oauth2/access_token", data=body,
                                         headers={"Content-Type":"application/x-www-form-urlencoded"})) as r:
        return json.loads(r.read())["access_token"]

get_cards    = lambda pid,tok: _http(f"{API_BASE}/1/projects/{pid}/cards", tok)
get_comments = lambda cid,tok: _http(f"{API_BASE}/1/cards/{cid}/comments", tok)
get_members  = lambda pid,tok: _http(f"{API_BASE}/1/projects/{pid}/members", tok)

def _pm_email(pid: str, tok: str, creator: Any) -> tuple[str,str]:
    for m in get_members(pid, tok):
        if str(m.get("id")) == str(creator):             # creator match
            return m.get("email",""), m.get("name","")
    return "",""

# ── Dynamo type coercion ───────────────────────────────────────────
def _d(val: Any) -> Any:                 # Decimal-safe recursion
    if isinstance(val, float):            return Decimal(str(val))
    if isinstance(val, list):             return [_d(v) for v in val]
    if isinstance(val, dict):             return {k: _d(v) for k,v in val.items()}
    return val                            # int/str/bool/None

# ── Lambda entrypoint ──────────────────────────────────────────────
def lambda_handler(event=None, context=None):
    tok   = _token()
    start = time.time()

    # full scan (paginated) to collect *all* project_ids
    scan_kwargs = {"ProjectionExpression":"project_id"}
    resp = table.scan(**scan_kwargs)
    projects = {i["project_id"] for i in resp["Items"]}
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **scan_kwargs)
        projects.update(i["project_id"] for i in resp["Items"])

    rows = 0
    for pid in projects:
        for c in get_cards(pid, tok):
            cid, title = str(c["id"]), c.get("title","")
            comments   = get_comments(cid, tok) if c.get("comment_count") else []

            client_email = (comments[0].get("text","") if title=="Client_Email" and comments
                            else next((m.group(0) for m in
                                      (EMAIL_RE.search(cm.get("text","")) for cm in comments)
                                      if m), ""))

            pm_email, pm_name = _pm_email(pid, tok, c.get("creator",{}).get("id"))

            attr: Dict[str,Any] = {
                # always: --------------------------------------------------
                ":title": title, ":description": c.get("description"),
                ":client_email": client_email, ":pm_email": pm_email, ":pm_name": pm_name,
                ":now": int(time.time()),
                # extended numeric/string maps: ---------------------------
                ":assignee": c.get("assignee"), ":assignee_id": c.get("assignee_id"),
                ":board_id": str(c.get("board_id")), ":board_name": c.get("board_name"),
                ":connected_issues": _d(c.get("connected_issues")),
                ":connected_risks":  _d(c.get("connected_risks")),
                ":contributors":     _d(c.get("contributors")),
                ":created_time":     c.get("created_time"),
                ":creator":          _d(c.get("creator")),
                ":dependencies":     _d(c.get("dependencies")),
                ":planlet":          _d(c.get("planlet")), ":planlet_id": c.get("planlet_id"),
                ":progress":         _d(c.get("progress")),
                ":project_val":      _d(c.get("project")),   # alias value
                ":reported_time":    c.get("reported_time"),
                ":comments":         _d(comments),
                ":direct_url":       c.get("direct_url"),
                ":is_done":          c.get("is_done"),
                ":is_blocked":       c.get("is_blocked"),
                ":blocked_reason":   c.get("is_blocked_reason"),
                ":checklist":        _d(c.get("checklist", [])),
                ":column_id":        c.get("column_id"),
                ":board_display_order": _d(c.get("display_order")),
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
            time.sleep(0.05)

    print(f"✅ Enriched {rows} cards in {int(time.time()-start)} s")
    return {"statusCode":200,
            "body":f"Enriched {rows} cards in {int(time.time()-start)} s"}
