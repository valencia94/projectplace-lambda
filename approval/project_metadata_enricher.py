#!/usr/bin/env python3
"""
ALL-projects enricher – stores full card JSON as string  (v3.1, 2025-05-10)
"""

import os, json, time, boto3
from decimal import Decimal
from urllib import request, parse, error

REGION   = os.environ["AWS_REGION"]
TABLE_V2 = os.environ["DYNAMODB_ENRICHMENT_TABLE"]
TABLE_V3 = os.environ.get("RAW_TABLE",
                          "ProjectPlace_DataExtrator_landing_table_v3")
SECRET   = os.environ.get("PROJECTPLACE_SECRET_NAME",
                          "ProjectPlaceAPICredentials")
API      = "https://api.projectplace.com"
DRY_RUN  = os.environ.get("DRY_RUN", "0") == "1"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
t2 = dynamodb.Table(TABLE_V2)
t3 = dynamodb.Table(TABLE_V3)
secrets = boto3.client("secretsmanager", region_name=REGION)


def get_token() -> str:
    c = json.loads(secrets.get_secret_value(SecretId=SECRET)["SecretString"])
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


def get_cards(pid: str, tok: str) -> list[dict]:
    req = request.Request(f"{API}/1/projects/{pid}/cards",
                          headers={"Authorization": f"Bearer {tok}"})
    with request.urlopen(req) as r:
        return json.loads(r.read())


def to_dec(x):
    """helper: Decimal-ise numeric fields safely"""
    return None if x is None else Decimal(str(x))


def lambda_handler(event=None, context=None):
    start = time.time()
    print(f"🚀 Refresh → {TABLE_V2} (dry_run={DRY_RUN})")

    proj_ids = list({row["project_id"] for row in t3.scan()["Items"]})
    if not proj_ids:
        return {"statusCode": 404, "body": "No projects in table_v3"}

    try:
        token = get_token()
    except Exception as e:
        print("❌ Auth failed:", e)
        return {"statusCode": 500, "body": "Auth failure"}

    writes = 0
    for pid in proj_ids:
        if time.time() - start > 540:
            print("⏰ Cut-off hit"); break
        print(f"🔄 {pid}")

        try:
            for card in get_cards(pid, token):
                cid = str(card["id"])            # STRING sort key

                attr = {
                    ":title":       card.get("title"),
                    ":board_id":    str(card.get("board_id")),
                    ":board_name":  card.get("board_name"),
                    ":column_id":   card.get("column_id"),
                    ":progress":    to_dec(card.get("progress")),
                    ":is_done":     card.get("is_done"),
                    ":raw":         json.dumps(card),     # store full JSON as string
                    ":ts":          int(time.time())
                }

                if DRY_RUN:
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
