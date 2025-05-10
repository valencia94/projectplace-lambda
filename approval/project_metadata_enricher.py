#!/usr/bin/env python3
"""
ALL-projects enricher – stores full card JSON + key filter columns
(v3.2, 2025-05-10)
"""

import os, json, time, boto3
from urllib import request, parse, error

REGION   = os.environ["AWS_REGION"]
TABLE_V2 = os.environ["DYNAMODB_ENRICHMENT_TABLE"]
TABLE_V3 = os.environ.get(
    "RAW_TABLE", "ProjectPlace_DataExtrator_landing_table_v3"
)
SECRET = os.environ.get("PROJECTPLACE_SECRET_NAME",
                        "ProjectPlaceAPICredentials")
API   = "https://api.projectplace.com"
DRY   = os.environ.get("DRY_RUN", "0") == "1"

dy    = boto3.resource("dynamodb", region_name=REGION)
t2    = dy.Table(TABLE_V2)
t3    = dy.Table(TABLE_V3)
sm    = boto3.client("secretsmanager", region_name=REGION)

# ── helpers ──────────────────────────────────────────────────────────────
def token() -> str:
    c = json.loads(sm.get_secret_value(SecretId=SECRET)["SecretString"])
    body = parse.urlencode({
        "grant_type": "client_credentials",
        "client_id":  c["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": c["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
    }).encode()
    req = request.Request(f"{API}/oauth2/access_token", data=body,
                          headers={"Content-Type":
                                   "application/x-www-form-urlencoded"})
    with request.urlopen(req) as r:
        return json.loads(r.read())["access_token"]

def cards(pid: str, tok: str) -> list[dict]:
    req = request.Request(f"{API}/1/projects/{pid}/cards",
                          headers={"Authorization": f"Bearer {tok}"})
    with request.urlopen(req) as r:
        return json.loads(r.read())

# ── handler ──────────────────────────────────────────────────────────────
def lambda_handler(event=None, context=None):
    start = time.time()
    print(f"🚀 Refresh → {TABLE_V2} (dry_run={DRY})")

    pids = list({row["project_id"] for row in t3.scan()["Items"]})
    if not pids:
        return {"statusCode": 404, "body": "No projects in v3"}

    try:
        tok = token()
    except Exception as e:
        print("❌ Auth failed:", e)
        return {"statusCode": 500, "body": "Auth failure"}

    writes = 0
    for pid in pids:
        if time.time() - start > 540:  # safety
            print("⏰ Time limit"); break
        print("🔄", pid)

        try:
            for c in cards(pid, tok):
                cid = str(c["id"])          # sort key as STRING

                attr = {
                    ":title":       c.get("title"),
                    ":board_id":    str(c.get("board_id")),
                    ":board_name":  c.get("board_name"),
                    ":column_id":   c.get("column_id"),
                    ":progress":    str(c.get("progress")),   # ← string
                    ":is_done":     c.get("is_done"),
                    ":raw":         json.dumps(c),            # full JSON
                    ":ts":          int(time.time())
                }

                if DRY:
                    continue

                t2.update_item(
                    Key={"project_id": str(pid), "card_id": cid},
                    UpdateExpression="""
                      SET title = :title,
                          board_id = :board_id,
                          board_name = :board_name,
                          column_id = :column_id,
                          progress  = :progress,
                          is_done   = :is_done,
                          raw_card  = :raw,
                          last_refreshed = :ts
                    """,
                    ExpressionAttributeValues=attr
                )
                writes += 1
        except Exception as e:
            print(f"❌ {pid}: {e}")

    span = int(time.time() - start)
    print(f"✅ Completed – {writes} rows in {span}s")
    return {"statusCode": 200,
            "body": f"Enriched {writes} cards in {span}s"}
