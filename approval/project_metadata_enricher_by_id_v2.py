#!/usr/bin/env python3
import os, zipfile, boto3, sys, shutil

def require_env(key):
    val = os.getenv(key)
    if not val:
        print(f"❌ Missing env var: {key}")#!/usr/bin/env python3
import os, json, time, uuid, boto3
from urllib import request, parse, error

REGION       = os.environ["AWS_REGION"]
TABLE_NAME   = os.environ["DYNAMODB_ENRICHMENT_TABLE"]
SECRET_NAME  = os.environ.get("PROJECTPLACE_SECRET_NAME", "ProjectPlaceAPICredentials")
API_BASE_URL = "https://api.projectplace.com"

ddb     = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
secrets = boto3.client("secretsmanager", region_name=REGION)

def get_projectplace_token():
    creds = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    data = parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": creds["PROJECTPLACE_ROBOT_CLIENT_ID"],
        "client_secret": creds["PROJECTPLACE_ROBOT_CLIENT_SECRET"]
    }).encode()
    req = request.Request(f"{API_BASE_URL}/oauth2/access_token", data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]

def get_pm_email(project_id, token, creator_id):
    url = f"{API_BASE_URL}/1/projects/{project_id}/members"
    req = request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with request.urlopen(req) as resp:
            for m in json.loads(resp.read()):
                if str(m.get("id")) == str(creator_id):
                    return m.get("email", ""), m.get("name", "")
    except error.HTTPError as e:
        print("⚠️ Member fetch failed:", e.read().decode())
    return "", ""

def get_all_cards(project_id, token):
    url = f"{API_BASE_URL}/1/projects/{project_id}/cards"
    req = request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    with request.urlopen(req) as resp:
        return json.loads(resp.read())

def lambda_handler(event, _):
    project_id = event.get("project_id")
    if not project_id:
        return {"statusCode": 400, "body": "Missing project_id"}

    print(f"🚀 Enriching project {project_id}")
    token = get_projectplace_token()

    try:
        cards = get_all_cards(project_id, token)
        for card in cards:
            cid = card.get("id")
            title = card.get("title", "")
            comments = card.get("comments", [])
            creator = card.get("creator", {})
            creator_id = creator.get("id")

            client_email = comments[0] if title == "Client_Email" and isinstance(comments, list) and comments else ""
            pm_email, pm_name = get_pm_email(project_id, token, creator_id)

            item = {
                "project_id": str(project_id),
                "card_id": str(cid),
                "title": title,
                "description": card.get("description"),
                "creator_id": str(creator_id),
                "created_time": card.get("created_time"),
                "client_email": client_email,
                "pm_email": pm_email,
                "pm_name": pm_name,
                "board_id": card.get("board_id"),
                "board_name": card.get("board_name"),
                "column_id": card.get("column_id"),
                "is_done": card.get("is_done"),
                "is_blocked": card.get("is_blocked"),
                "is_blocked_reason": card.get("is_blocked_reason"),
                "checklist": card.get("checklist", []),
                "comments": comments,
                "progress": card.get("progress"),
                "direct_url": card.get("direct_url"),
                "approval_token": str(uuid.uuid4()),
                "sent_timestamp": int(time.time()),
                "status": "pending"
            }

            ddb.put_item(Item=item)
            print(f"✅ Updated card {cid}")
            time.sleep(1)
    except Exception as e:
        return {"statusCode": 500, "body": f"Error: {str(e)}"}

    return {"statusCode": 200, "body": f"Enrichment complete for project {project_id}"}
        sys.exit(1)
    return val

print("🟢 STARTING deploy_metadata_enricher.py")

try:
    REGION       = require_env("AWS_REGION")
    ACCOUNT_ID   = require_env("AWS_ACCOUNT_ID")
    TABLE_NAME   = require_env("DYNAMODB_ENRICHMENT_TABLE")
    SECRET_NAME  = require_env("PROJECTPLACE_SECRET_NAME")

    FUNCTION     = "projectMetadataEnricher"
    HANDLER      = "project_metadata_enricher.lambda_handler"
    ROLE_ARN     = f"arn:aws:iam::{ACCOUNT_ID}:role/ProjectplaceLambdaRole"
    ZIP_DIR      = "./deployment_zips"
    SRC_FILE     = "approval/project_metadata_enricher.py"

    if not os.path.exists(SRC_FILE):
        print(f"❌ ERROR: Lambda source file missing → {SRC_FILE}")
        sys.exit(1)

    if os.path.exists(ZIP_DIR):
        shutil.rmtree(ZIP_DIR)

    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = f"{ZIP_DIR}/{FUNCTION}.zip"
    print(f"📦 Creating zip → {zip_path}")
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(SRC_FILE, arcname=os.path.basename(SRC_FILE))

    client = boto3.client("lambda", region_name=REGION)
    with open(zip_path, "rb") as f:
        zipped_code = f.read()

    env_vars = {
        "DYNAMODB_ENRICHMENT_TABLE": TABLE_NAME,
        "PROJECTPLACE_SECRET_NAME": SECRET_NAME
    }

    try:
        print("🔁 Updating Lambda code...")
        client.get_function(FunctionName=FUNCTION)
        client.update_function_code(FunctionName=FUNCTION, ZipFile=zipped_code)

        print("⏳ Waiting for update to complete...")
        client.get_waiter("function_updated").wait(FunctionName=FUNCTION)

        print("⚙️ Updating Lambda environment and timeout...")
        client.update_function_configuration(
            FunctionName=FUNCTION,
            Environment={"Variables": env_vars},
            Timeout=300
        )
    except client.exceptions.ResourceNotFoundException:
        print("🚀 Creating new Lambda function...")
        client.create_function(
            FunctionName=FUNCTION,
            Runtime="python3.9",
            Role=ROLE_ARN,
            Handler=HANDLER,
            Code={"ZipFile": zipped_code},
            Timeout=300,
            MemorySize=256,
            Publish=True,
            Environment={"Variables": env_vars}
        )

    arn = client.get_function(FunctionName=FUNCTION)["Configuration"]["FunctionArn"]
    print(f"✅ Lambda deployed → {arn}")

except Exception as e:
    print("❌ DEPLOY FAILED:", str(e))
    sys.exit(1)
