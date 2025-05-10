#!/usr/bin/env python3
"""
ProjectPlace → DynamoDB enrichment Lambda   v2.6 (2025-05-10)

• Writes extended card fields + comments to DynamoDB v2
• No external dependencies (stock Python 3.9)
• Retries ProjectPlace calls (HTTP 5xx / 429) with back-off
"""

import json
import os
import random
import re
import time
import urllib.error
from typing import Any, Dict, List, Optional

import boto3
from urllib import parse, request

REGION = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE_NAME = os.environ["DYNAMODB_ENRICHMENT_TABLE"].strip()
API_BASE = "https://api.projectplace.com"
SECRET_NAME = os.getenv("PROJECTPLACE_SECRET_NAME",
                        "ProjectPlaceAPICredentials")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

ddb = boto3.resource("dynamodb", region_name=REGION)
table = ddb.Table(TABLE_NAME)
secrets = boto3.client("secretsmanager", region_name=REGION)

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


# ───────────────────────── helpers ─────────────────────────
def _http(url: str, token: Optional[str] = None) -> Any:
    """GET with up-to-5 retries on 5xx / 429."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    delay = 0.8
    last_exc: Exception | None = None
    for _ in range(5):
        try:
            req = request.Request(url, headers=headers)
            with request.urlopen(req, timeout=8) as resp:
                return json.loads(resp.read())
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code != 429:
                raise
            last_exc = exc
            time.sleep(delay + random.random() * 0.2)
            delay *= 1.7
    raise last_exc or RuntimeError("HTTP retries exhausted")


def _get_token() -> str:
    sec = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)
                     ["SecretString"])
    data = parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": sec["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": sec["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
    }).encode()
    req = request.Request(f"{API_BASE}/oauth2/access_token", data=data,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    return json.loads(request.urlopen(req).read())["access_token"]


_cards = lambda pid, tok: _http(f"{API_BASE}/1/projects/{pid}/cards", tok)
_comments = lambda cid, tok: _http(f"{API_BASE}/1/cards/{cid}/comments", tok)
_members = lambda pid, tok: _http(f"{API_BASE}/1/projects/{pid}/members", tok)


def _pm_email(pid: str, tok: str, creator_id: str) -> tuple[str, str]:
    for m in _members(pid, tok):
        if str(m.get("id")) == str(creator_id):
            return m.get("email", ""), m.get("name", "")
    return "", ""


def _all_project_ids() -> set[str]:
    """Scan Dynamo table fully and return distinct project_ids (as strings)."""
    proj_ids: set[str] = set()
    scan_kwargs = {"ProjectionExpression": "project_id"}
    resp = table.scan(**scan_kwargs)
    proj_ids.update(item["project_id"] for item in resp["Items"])
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"],
                          **scan_kwargs)
        proj_ids.update(item["project_id"] for item in resp["Items"])
    return proj_ids


# ───────────────────────── Lambda handler ─────────────────────────
def lambda_handler(event=None, context=None):
    start = time.time()
    token = _get_token()
    projects = _all_project_ids()

    row_count = 0
    for pid in projects:
        for card in _cards(pid, token):
            cid = str(card["id"])
            title = card.get("title", "")

            # fetch comments only if there are any
            comments = _comments(cid, token) if card.get("comment_count", 0) else []

            # client_email extraction
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
                # core fields
                ":title": title,
                ":description": card.get("description"),
                ":client_email": client_email,
                ":pm_email": pm_email,
                ":pm_name": pm_name,
                ":now": int(time.time()),
                # extended fields
                ":assignee": card.get("assignee"),
                ":assignee_id": card.get("assignee_id"),
                ":board_id": str(card.get("board_id")),
                ":board_name": card.get("board_name"),
                ":connected_issues": card.get("connected_issues"),
                ":connected_risks": card.get("connected_risks"),
                ":contributors": card.get("contributors"),
                ":created_time": card.get("created_time"),
                ":creator": card.get("creator"),
                ":dependencies": card.get("dependencies"),
                ":planlet": card.get("planlet"),
                ":planlet_id": card.get("planlet_id"),
                ":progress": card.get("progress"),
                ":project": card.get("project"),
                ":reported_time": card.get("reported_time"),
                ":comments": comments,
                ":direct_url": card.get("direct_url"),
                ":is_done": card.get("is_done"),
                ":is_blocked": card.get("is_blocked"),
                ":blocked_reason": card.get("is_blocked_reason"),
                ":checklist": card.get("checklist", []),
                ":column_id": card.get("column_id"),
                ":board_display_order": card.get("display_order"),
            }

            if not DRY_RUN:
                table.update_item(
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
            time.sleep(0.05)  # API-friendly pause

    elapsed = int(time.time() - start)
    msg = f"Enriched {row_count} cards in {elapsed}s"
    print(f"✅ {msg}")
    return {"statusCode": 200, "body": msg}
