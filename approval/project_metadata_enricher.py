#!/usr/bin/env python3
"""
ProjectPlace → DynamoDB enrichment Lambda   v2.4 (2025-05-10)
* Stores the full card payload → attr map below
* No external libraries (Python 3.9 stock)
"""

import os, json, time, re, random, boto3, urllib.error
from urllib import request, parse
REGION = os.getenv("AWS_REGION", boto3.Session().region_name)
TABLE  = os.environ["DYNAMODB_ENRICHMENT_TABLE"].strip()
API    = "https://api.projectplace.com"
SECRET = os.getenv("PROJECTPLACE_SECRET_NAME","ProjectPlaceAPICredentials")
DRY_RUN= os.getenv("DRY_RUN","0")=="1"

ddb = boto3.resource("dynamodb",region_name=REGION).Table(TABLE)
sm  = boto3.client("secretsmanager",region_name=REGION)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",re.I)

# ── tiny retry wrapper ──────────────────────────────────────────────
def _http(url:str, tok:str|None=None):
    hdr={"Authorization":f"Bearer {tok}"} if tok else{}
    delay,last=None,0.8
    for _ in range(5):
        try:
            with request.urlopen(request.Request(url,headers=hdr),timeout=10) as r:
                return json.loads(r.read())
        except (urllib.error.HTTPError,urllib.error.URLError) as e:
            if isinstance(e,urllib.error.HTTPError) and e.code<500 and e.code!=429: raise
            last=e; time.sleep(delay+random.random()*0.3); delay*=1.8
    raise last

def _token():
    cred=json.loads(sm.get_secret_value(SecretId=SECRET)["SecretString"])
    data=parse.urlencode({"grant_type":"client_credentials",
                          "client_id":cred["PROJECTPLACE_ROBOT_CLIENT_ID"],
                          "client_secret":cred["PROJECTPLACE_ROBOT_CLIENT_SECRET"],}).encode()
    with request.urlopen(request.Request(f"{API}/oauth2/access_token",data=data,
         headers={"Content-Type":"application/x-www-form-urlencoded"})) as r:
        return json.loads(r.read())["access_token"]

_cards      = lambda pid,tok: _http(f"{API}/1/projects/{pid}/cards",tok)
_comments   = lambda cid,tok: _http(f"{API}/1/cards/{cid}/comments",tok)
_members    = lambda pid,tok: _http(f"{API}/1/projects/{pid}/members",tok)

def _pm_email(pid,tok,creator):
    for m in _members(pid,tok):
        if str(m.get("id"))==str(creator):
            return m.get("email",""),m.get("name","")
    return "",""

def lambda_handler(event=None,context=None):
    start=time.time(); tok=_token()
    projects={i["project_id"] for i in ddb.scan()["Items"]}
    rows=0
    for pid in projects:
        for card in _cards(pid,tok):
            cid=str(card["id"]); title=card.get("title","")
            comments=_comments(cid,tok) if card.get("comment_count",0) else []
            if title=="Client_Email" and comments:
                client_email=comments[0].get("text","")
            else:
                client_email=next((EMAIL_RE.search(c.get("text","")).group(0)
                                   for c in comments if EMAIL_RE.search(c.get("text",""))),"")
            pm_email,pm_name=_pm_email(pid,tok,card.get("creator",{}).get("id"))

            ddb.update_item(
                Key={"project_id": str(pid), "card_id": cid},
                UpdateExpression="""
                    SET title              = :title,
                        description        = :description,
                        client_email       = :client_email,
                        pm_email           = :pm_email,
                        pm_name            = :pm_name,
                        assignee           = :assignee,
                        assignee_id        = :assignee_id,
                        board_id           = :board_id,
                        board_name         = :board_name,
                        connected_issues   = :connected_issues,
                        connected_risks    = :connected_risks,
                        contributors       = :contributors,
                        created_time       = :created_time,
                        creator            = :creator,
                        dependencies       = :dependencies,
                        planlet            = :planlet,
                        planlet_id         = :planlet_id,
                        progress           = :progress,
                        project            = :project,
                        reported_time      = :reported_time,
                        comments           = :comments,
                        direct_url         = :direct_url,
                        is_done            = :is_done,
                        is_blocked         = :is_blocked,
                        is_blocked_reason  = :blocked_reason,
                        checklist          = :checklist,
                        column_id          = :column_id,
                        board_display_order=:board_display_order,
                        last_refreshed     = :now
                """,
                ExpressionAttributeValues=attr
            )

            if not DRY_RUN:
                ddb.update_item(
                    Key={"project_id":str(pid),"card_id":cid},
                    UpdateExpression=\"\"\"\nSET title=:title, description=:description,\n    client_email=:client_email, pm_email=:pm_email, pm_name=:pm_name,\n    assignee=:assignee, assignee_id=:assignee_id,\n    board_id=:board_id, board_name=:board_name,\n    connected_issues=:connected_issues, connected_risks=:connected_risks,\n    contributors=:contributors, created_time=:created_time, creator=:creator,\n    dependencies=:dependencies, planlet=:planlet, planlet_id=:planlet_id,\n    progress=:progress, project=:project, reported_time=:reported_time,\n    comments=:comments, direct_url=:direct_url,\n    is_done=:is_done, is_blocked=:is_blocked, is_blocked_reason=:blocked_reason,\n    checklist=:checklist, column_id=:column_id, board_display_order=:board_display_order,\n    last_refreshed=:now\"\"\",ExpressionAttributeValues=attr)

            rows+=1; time.sleep(0.05)

    print(f\"✅ Enriched {rows} cards in {int(time.time()-start)} s\")
    return {\"statusCode\":200,
            \"body\":f\"Enriched {rows} cards in {int(time.time()-start)} s\"}
