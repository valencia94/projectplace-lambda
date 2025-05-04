#!/usr/bin/env python3
"""
…same header…
"""

import os, json, time, urllib.request
from uuid import uuid4
from collections import defaultdict
import boto3

REGION       = os.getenv("AWS_REGION") or sys.exit("❌ AWS_REGION")
TABLE_NAME   = os.getenv("DYNAMODB_TABLE_NAME") or sys.exit("❌ DYNAMODB_TABLE_NAME")
SECRET_NAME  = os.getenv("PROJECTPLACE_SECRET_NAME", "ProjectPlaceAPICredentials")
API_BASE_URL = "https://api.projectplace.com"

ddb     = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
secrets = boto3.client("secretsmanager", region_name=REGION)


def get_pp_token():
    sec = secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"]
    creds = json.loads(sec)
    body = (
      f"grant_type=client_credentials"
      f"&client_id={creds['PROJECTPLACE_ROBOT_CLIENT_ID']}"
      f"&client_secret={creds['PROJECTPLACE_ROBOT_CLIENT_SECRET']}"
    ).encode()
    req = urllib.request.Request(
      f"{API_BASE_URL}/oauth2/access_token",
      data=body,
      headers={"Content-Type":"application/x-www-form-urlencoded"},
      method="POST"
    )
    return json.loads(urllib.request.urlopen(req).read())["access_token"]


def get_pm_email(project_id, token, creator_id):
    req = urllib.request.Request(
      f"{API_BASE_URL}/1/projects/{project_id}/members",
      headers={"Authorization":f"Bearer {token}"})
    members = json.loads(urllib.request.urlopen(req).read())
    for m in members:
        if str(m.get("id")) == str(creator_id):
            return m.get("email","")
    return ""


def lambda_handler(event, ctx):
    print("🚀 Starting enrichment…")
    try:
        token = get_pp_token()
    except Exception as e:
        print("❌ Auth failed:", e)
        return {"statusCode":500,"body":"Auth failure"}

    items = ddb.scan().get("Items",[])
    if not items:
        return {"statusCode":404,"body":"No records"}

    by_proj = defaultdict(list)
    for it in items:
        if "project_id" in it and "card_id" in it:
            by_proj[it["project_id"]].append(it)

    for project_id, cards in by_proj.items():
        print("🔄 Project", project_id)
        for it in cards:
            proj = project_id
            card = it["card_id"]
            creator = it.get("creator","")
            client_email = ""
            if it.get("title")=="Client_Email" and isinstance(it.get("comments"),list):
                client_email = it["comments"][0]
            try:
                cid = json.loads(creator.replace("'",'"')).get("id")
            except: cid = None
            pm = get_pm_email(proj, token, cid) if cid else ""

            new_token = str(uuid4())
            ts = int(time.time())

            try:
                ddb.update_item(
                  Key={"project_id":proj,"card_id":card},
                  UpdateExpression=(
                    "SET client_email=:ce, pm_email=:pe, "
                    "approval_token=:at, sent_timestamp=:ts, #s=:st"
                  ),
                  ExpressionAttributeNames={"#s":"status"},
                  ExpressionAttributeValues={
                    ":ce":client_email,
                    ":pe":pm,
                    ":at":new_token,
                    ":ts":ts,
                    ":st":"pending"
                  }
                )
                print("✅ Updated", proj, card)
            except Exception as e:
                print("❌ Err updating", card, e)

    print("✅ Done")
    return {"statusCode":200,"body":"OK"}
