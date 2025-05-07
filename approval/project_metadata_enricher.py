#!/usr/bin/env python3
"""
project_metadata_enricher.py
────────────────────────────
Fetches *all* cards for one or many ProjectPlace projects and enriches a
dedicated DynamoDB table (…_v2).

• If the Lambda is invoked with {"project_id":"123"}  → enrich just that project.
• If the payload is {} (or None)                     → enumerate *all* projects
  the robot account can access and enrich them all.

Environment variables
─────────────────────
AWS_REGION                 us-east-2
DYNAMODB_TABLE_NAME        ProjectPlace_DataExtractor_landing_table_v2
PROJECTPLACE_SECRET_NAME   ProjectPlaceAPICredentials   (Secrets Manager)
"""

import json, os, time, logging, requests, boto3
from typing import List, Dict, Any

# ─── ENV ──────────────────────────────────────────────────────────
REGION       = os.environ["AWS_REGION"]
TABLE_NAME   = os.environ["DYNAMODB_TABLE_NAME"]
SECRET_NAME  = os.environ["PROJECTPLACE_SECRET_NAME"]
PROJECTPLACE_API_URL = "https://api.projectplace.com"

# ─── LOGGING ──────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── HELPERS ──────────────────────────────────────────────────────
def get_projectplace_token() -> str | None:
    sm = boto3.client("secretsmanager", region_name=REGION)
    sec = json.loads(sm.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    resp = requests.post(
        f"{PROJECTPLACE_API_URL}/oauth2/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id":     sec["PROJECTPLACE_ROBOT_CLIENT_ID"],
            "client_secret": sec["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
        },
        timeout=15,
    )
    if resp.ok:
        return resp.json().get("access_token")
    logger.error("Failed to obtain ProjectPlace token – %s", resp.text)
    return None


def get_projects_to_enrich(event: dict | None, token: str) -> List[str]:
    """Return a list of project IDs to process."""
    if isinstance(event, dict) and event.get("project_id"):
        return [str(event["project_id"])]

    projects: List[str] = []
    headers = {"Authorization": f"Bearer {token}"}
    next_url = f"{PROJECTPLACE_API_URL}/1/projects"

    while next_url:
        r = requests.get(next_url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        projects.extend(str(p["id"]) for p in data.get("entities", []))
        next_url = (
            data.get("@odata.nextLink")
            or r.links.get("next", {}).get("url")
            or None
        )
    return projects


def fetch_cards_for_project(project_id: str, token: str) -> List[Dict[str, Any]]:
    """Download *all* cards for a single ProjectPlace project."""
    headers = {"Authorization": f"Bearer {token}"}
    cards: List[Dict[str, Any]] = []

    next_url = f"{PROJECTPLACE_API_URL}/1/projects/{project_id}/cards"
    while next_url:
        r = requests.get(next_url, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        cards.extend(data.get("entities", []))
        next_url = (
            data.get("@odata.nextLink")
            or r.links.get("next", {}).get("url")
            or None
        )
    return cards


# ─── WRITE TO DYNAMODB ────────────────────────────────────────────
def write_cards(project_id: str, cards: List[Dict[str, Any]], table) -> None:
    now = int(time.time())
    with table.batch_writer(overwrite_by_pkeys=["project_id", "card_id"]) as batch:
        for c in cards:
            item = {
                # composite primary key
                "project_id": str(project_id),
                "card_id":    str(c["id"]),
                # core timestamps
                "ingested_ts":   now,
                "created_time":  c.get("created_time"),
                # frequently-used fields
                "title":        c.get("title"),
                "description":  c.get("description"),
                "is_done":      c.get("is_done", False),
                "direct_url":   c.get("direct_url"),
                # assignee
                "assignee_id":   c.get("assignee", {}).get("id"),
                "assignee_name": c.get("assignee", {}).get("name"),
                # board / column
                "board_id":              c.get("board_id"),
                "board_name":            c.get("board_name"),
                "column_id":             c.get("column_id"),
                "column_first_updated":  c.get("column_first_updated"),
                "column_last_updated":   c.get("column_last_updated"),
                # plan / WBS
                "planlet_id":   c.get("planlet_id"),
                "local_id":     c.get("local_id"),
                # status flags
                "is_blocked":        c.get("is_blocked", False),
                "is_blocked_reason": c.get("is_blocked_reason"),
                "is_template":       c.get("is_template", False),
                # checklist & dependencies
                "checklist":    c.get("checklist", []),
                "dependencies": c.get("dependencies", []),
                # full comments array
                "comments":     c.get("comments", []),
            }
            batch.put_item(Item=item)
    logger.info("📝 Wrote %d cards for project %s", len(cards), project_id)


# ─── HANDLER ──────────────────────────────────────────────────────
def lambda_handler(event: dict | None, _ctx):
    logger.info("🚀 Starting enrichment...")
    token = get_projectplace_token()
    if not token:
        return {"statusCode": 500, "body": "Auth failure"}

    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
    projects = get_projects_to_enrich(event, token)
    if not projects:
        return {"statusCode": 404, "body": "No projects found"}

    for pid in projects:
        logger.info("🔄 Enriching project: %s", pid)
        cards = fetch_cards_for_project(pid, token)
        write_cards(pid, cards, table)

    logger.info("✅ Enrichment complete (%d project(s))", len(projects))
    return {"statusCode": 200, "body": f"Processed {len(projects)} project(s)"}
