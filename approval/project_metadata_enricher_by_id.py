#!/usr/bin/env python3
"""
One-project metadata enricher  –  Upsert-safe (v2.0, 2025-05-10)

Key points
----------
✔ Uses  UpdateItem  so card metadata is refreshed
  but approval fields *status, sent_timestamp, approver* are never overwritten.
✔ No ConditionExpression  → never throws ConditionalCheckFailedException.
✔ Logs the write-mode (INSERT vs UPDATE) for every card.
"""

import os, json, time, uuid, boto3
from typing import Optional
from urllib import request, parse, error

REGION       = os.environ["AWS_REGION"]
TABLE_NAME   = os.environ["DYNAMODB_ENRICHMENT_TABLE"]
SECRET_NAME  = os.environ.get("SECRET_NAME", "ProjectPlaceAPICredentials")
API_BASE_URL = "https://api.projectplace.com"

ddb     = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
secrets = boto3.client("secretsmanager", region_name=REGION)


# ─── helper functions ────────────────────────────────────────────────────────
def get_projectplace_token() -> str:
    creds = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    data  = parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     creds["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": creds["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
    }).encode()
    req = request.Request(f"{API_BASE_URL}/oauth2/access_token", data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


def get_pm_email(project_id: str, token: str, creator_id: str) -> tuple[str, str]:
    url = f"{API_BASE_URL}/1/projects/{project_id}/members"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with request.urlopen(req) as resp:
            for m in json.loads(resp.read()):
                if str(m.get("id")) == str(creator_id):
                    return m.get("email", ""), m.get("name", "")
    except error.HTTPError as e:
        print("⚠️ member fetch failed:", e.read().decode())
    return "", ""


def get_all_cards(project_id: str, token: str) -> list[dict]:
    url = f"{API_BASE_URL}/1/projects/{project_id}/cards"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with request.urlopen(req) as resp:
        return json.loads(resp.read())


# ─── Lambda handler ──────────────────────────────────────────────────────────
def lambda_handler(event: Optional[dict] = None, context=None):
    event = event or {}
    project_id = event.get("project_id")

    if not project_id:
        return {"statusCode": 400, "body": "Missing project_id – nothing to enrich"}

    print(f"🚀 Enriching project {project_id} → {TABLE_NAME}")
    token  = get_projectplace_token()
    writes = 0

    try:
        for card in get_all_cards(project_id, token):
            cid        = str(card.get("id"))
            title      = card.get("title", "")
            comments   = card.get("comments", [])
            creator_id = str(card.get("creator", {}).get("id"))

            client_email = (
                comments[0]
                if title == "Client_Email" and comments else ""
            )
            pm_email, pm_name = get_pm_email(project_id, token, creator_id)

            # attributes that are safe to overwrite every run
            attr = {
                ":title":        title,
                ":description":  card.get("description"),
                ":client_email": client_email,
                ":pm_email":     pm_email,
                ":pm_name":      pm_name,
                ":board_id":     str(card.get("board_id")),
                ":board_name":   card.get("board_name"),
                ":column_id":    card.get("column_id"),
                ":is_done":      card.get("is_done"),
                ":is_blocked":   card.get("is_blocked"),
                ":blocked_reason": card.get("is_blocked_reason"),
                ":checklist":    card.get("checklist", []),
                ":comments":     comments,
                ":progress":     card.get("progress"),
                ":direct_url":   card.get("direct_url"),
            }

            resp = ddb.update_item(
                Key={"project_id": str(project_id), "card_id": cid},
                UpdateExpression="""
                    SET title = :title,
                        description = :description,
                        client_email = :client_email,
                        pm_email = :pm_email,
                        pm_name  = :pm_name,
                        board_id = :board_id,
                        board_name = :board_name,
                        column_id = :column_id,
                        is_done   = :is_done,
                        is_blocked = :is_blocked,
                        is_blocked_reason = :blocked_reason,
                        checklist = :checklist,
                        comments  = :comments,
                        progress  = :progress,
                        direct_url = :direct_url,
                        last_refreshed = :now
                """,
                ExpressionAttributeValues={**attr, ":now": int(time.time())},
                ReturnValues="UPDATED_NEW"
            )

            op = "INSERT" if not resp.get("Attributes") else "UPDATE"
            writes += 1
            print(f"✅ {op} {cid}")

            time.sleep(0.05)  # API-friendly pause

    except Exception as e:
        print("❌ Enrichment error:", e)
        return {"statusCode": 500, "body": f"Enrichment error: {e}"}

    return {
        "statusCode": 200,
        "body": f"Enrichment complete for {project_id} (rows_processed={writes})"
    }
