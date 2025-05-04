#!/usr/bin/env python3
import os
import boto3
import requests

# ─── ENVIRONMENT ────────────────────────────────────────────────
REGION      = os.environ["AWS_REGION"]
TABLE_NAME  = os.environ["DYNAMODB_TABLE_NAME"]
SECRET_NAME = os.environ["PROJECTPLACE_SECRET_NAME"]

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table    = dynamodb.Table(TABLE_NAME)

# ─── FETCH PROJECTPLACE CARDS ──────────────────────────────────
def fetch_projects():
    # (your existing logic to page through ProjectPlace cards)
    # must return a list of dicts with at least 'project_id' and 'card_id'
    pass

def fetch_card_details(card_id):
    # (your existing logic to call ProjectPlace API for a given card)
    # must return metadata dict
    pass

# ─── MAIN ENRICH LOOP ──────────────────────────────────────────
def lambda_handler(event, context):
    print("🚀 Starting full metadata enrichment…")
    for row in fetch_projects():
        pid    = row["project_id"]
        cid    = row["card_id"]
        print(f"🔄 Enriching project {pid} / card {cid}")

        try:
            meta = fetch_card_details(cid)
            table.update_item(
                Key={                    # ← BOTH keys now present
                    "project_id": pid,
                    "card_id":    cid
                },
                UpdateExpression="SET " + ", ".join(f"{k}=:{k}" for k in meta),
                ExpressionAttributeValues={f":{k}": v for k, v in meta.items()}
            )
        except Exception as e:
            print(f"[ERROR] Updating card_id {cid}: {e}")

    print("✅ Enrichment complete for all projects.")
