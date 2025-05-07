#!/usr/bin/env python3
"""
project_metadata_enricher.py   — “requests-free” edition
────────────────────────────────────────────────────────
▪ If invoked with {"project_id": "123"}  → enrich only that project
▪ If invoked with {} or no payload      → enumerate *all* projects the
  ProjectPlace robot can access and enrich each of them.

ENV VARS (all already set in your workflow)
──────────────────────────────────────────
AWS_REGION                 us-east-2
DYNAMODB_TABLE_NAME        ProjectPlace_DataExtractor_landing_table_v2
PROJECTPLACE_SECRET_NAME   ProjectPlaceAPICredentials   (AWS Secrets Manager)
"""

from __future__ import annotations
import json, os, time, logging, urllib.parse, urllib.request
from typing import Dict, List, Optional, Any

# ──────────────────────────── CONFIG ─────────────────────────────
REGION        = os.environ["AWS_REGION"]
TABLE_NAME    = os.environ["DYNAMODB_TABLE_NAME"]
SECRET_NAME   = os.environ["PROJECTPLACE_SECRET_NAME"]
PP_API_ROOT   = "https://api.projectplace.com"

# ────────────────────────── LOGGING ──────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────── HTTP (urllib) ──────────────────────────
def _http(
    method: str,
    url: str,
    token: Optional[str] = None,
    form: Optional[dict] = None,
    timeout: float = 20.0,
) -> dict:
    """
    Tiny wrapper around urllib that returns {"json": …, "headers": …}.
    """
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return {
            "json": json.loads(body) if body else {},
            "headers": dict(resp.headers),
        }


def _next_link(resp_json: dict, headers: dict) -> Optional[str]:
    """
    Determine the ‘next’ page URL from either an @odata.nextLink field or a
    Link: <url>; rel="next" header (ProjectPlace uses both in different endpoints).
    """
    nxt = resp_json.get("@odata.nextLink")
    if nxt:
        return nxt
    link = headers.get("Link") or headers.get("link")
    if link and 'rel="next"' in link:
        return link.split(";")[0].strip("<> ")
    return None


# ───────────────────── TOKEN FROM SECRETS MANAGER ───────────────
def get_pp_token() -> str | None:
    import boto3
    sm = boto3.client("secretsmanager", region_name=REGION)
    secret = json.loads(sm.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    payload = {
        "grant_type":    "client_credentials",
        "client_id":     secret["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": secret["PROJECTPLACE_ROBOT_CLIENT_SECRET"],
    }
    r = _http("POST", f"{PP_API_ROOT}/oauth2/access_token", form=payload, timeout=15)
    tok = r["json"].get("access_token")
    if not tok:
        log.error("Failed to obtain ProjectPlace token – check credentials.")
    return tok


# ─────────────────────── LIST / FETCH HELPERS ────────────────────
def list_projects(token: str) -> List[str]:
    out: List[str] = []
    url = f"{PP_API_ROOT}/1/projects"
    while url:
        r = _http("GET", url, token=token)
        out.extend(str(p["id"]) for p in r["json"].get("entities", []))
        url = _next_link(r["json"], r["headers"])
    return out


def fetch_cards(pid: str, token: str) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    url = f"{PP_API_ROOT}/1/projects/{pid}/cards"
    while url:
        r = _http("GET", url, token=token)
        cards.extend(r["json"].get("entities", []))
        url = _next_link(r["json"], r["headers"])
    return cards


# ───────────────────────── DDB WRITE ─────────────────────────────
def write_cards(pid: str, cards: List[Dict[str, Any]], table) -> None:
    now = int(time.time())
    with table.batch_writer(overwrite_by_pkeys=["project_id", "card_id"]) as bw:
        for c in cards:
            bw.put_item(
                Item={
                    "project_id": pid,
                    "card_id":    str(c["id"]),
                    "title":      c.get("title"),
                    "description": c.get("description"),
                    "direct_url":  c.get("direct_url"),
                    "is_done":     c.get("is_done", False),
                    "created_time": c.get("created_time"),
                    "assignee_id":  c.get("assignee", {}).get("id"),
                    "assignee_name": c.get("assignee", {}).get("name"),
                    "board_id":   c.get("board_id"),
                    "board_name": c.get("board_name"),
                    "column_id":  c.get("column_id"),
                    "planlet_id": c.get("planlet_id"),
                    "local_id":   c.get("local_id"),
                    "is_blocked": c.get("is_blocked", False),
                    "checklist":  c.get("checklist", []),
                    "dependencies": c.get("dependencies", []),
                    "comments":   c.get("comments", []),
                    # enrichment metadata
                    "ingested_ts": now,
                }
            )
    log.info("📝 Stored %d cards for project %s", len(cards), pid)


# ────────────────────── LAMBDA ENTRY POINT ───────────────────────
def lambda_handler(event: dict | None, _context):
    log.info("🚀 Starting enrichment run")
    token = get_pp_token()
    if not token:
        return {"statusCode": 500, "body": "Auth failure"}

    target_projects = (
        [str(event["project_id"])]
        if isinstance(event, dict) and event.get("project_id")
        else list_projects(token)
    )
    if not target_projects:
        return {"statusCode": 404, "body": "No projects accessible"}

    import boto3
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

    for pid in target_projects:
        log.info("🔄 Enriching %s", pid)
        try:
            cards = fetch_cards(pid, token)
            write_cards(pid, cards, table)
        except Exception as exc:  # noqa: BLE001
            log.exception("⚠️  Project %s failed: %s", pid, exc)

    log.info("✅ Finished – %d project(s) processed", len(target_projects))
    return {"statusCode": 200, "body": f"Processed {len(target_projects)} project(s)"}  # noqa: EM101
