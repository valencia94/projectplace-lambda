import os
import json
import time
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import subprocess
import boto3
from botocore.exceptions import ClientError
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_ALIGN_VERTICAL, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import ast

# ------------------------------------------------------------------------------
# Logging Setup
# ------------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------------------
SECRET_NAME = "ProjectPlaceAPICredentials"
REGION = "us-east-2"
PROJECTPLACE_API_URL = "https://api.projectplace.com"
DYNAMO_TABLE = os.getenv("DYNAMODB_TABLE_NAME", "ProjectPlace_DataExtrator_landing_table_v3")
S3_BUCKET = os.getenv("S3_BUCKET_NAME", "projectplace-dv-2025-x9a7b")
BRAND_COLOR_HEADER = "4AC795"
LIGHT_SHADE_2X2 = "FAFAFA"
OUTPUT_EXCEL = "/tmp/Acta_de_Seguimiento.xlsx"
LOGO_IMAGE_PATH = os.path.join(os.path.dirname(__file__), "logo", "company_logo.png")

# ------------------------------------------------------------------------------
# Lambda Handler
# ------------------------------------------------------------------------------
def lambda_handler(event, context):
    logger.info("🚀 Starting Acta generation workflow.")

    # 1) Secrets → creds
    secret = load_secrets()
    cid = secret.get("PROJECTPLACE_ROBOT_CLIENT_ID")
    csec = secret.get("PROJECTPLACE_ROBOT_CLIENT_SECRET")
    if not (cid and csec):
        return {"statusCode": 500, "body": "Missing ProjectPlace credentials."}

    # 2) OAuth token
    token = get_robot_access_token(cid, csec)
    if not token:
        return {"statusCode": 500, "body": "Failed to get ProjectPlace token."}

    # 3) Active projects
    projects = get_all_account_projects(token, include_archived=False)
    if not projects:
        msg = "No active projects returned. Possibly no new or user has limited visibility."
        logger.warning(msg)
        return {"statusCode": 200, "body": msg}

    # 4) Build Excel with all cards
    excel_path = generate_excel_report(token, projects)
    if not excel_path:
        return {"statusCode": 500, "body": "No Excel generated (no cards?)."}

    # 5) DataFrame load
    df = pd.read_excel(excel_path)
    logger.info(f"[DEBUG] Excel load → rows={len(df)}, cols={list(df.columns)}")
    if df.empty:
        logger.warning("Excel is empty—no tasks to process.")
        return {"statusCode": 200, "body": "No tasks found in Excel."}

    # 6) Store in DynamoDB
    store_in_dynamodb(df)

    # 7) Business filter and enrichment
    df = snippet_filter(df)
    logger.info(f"[DEBUG] After snippet_filter → rows={len(df)}")
    if df.empty:
        logger.warning("No tasks remain after snippet filter.")
        return {"statusCode": 200, "body": "No tasks remain after snippet filter."}

    # 8) Per-project docx (and optional PDF)
    doc_count = 0
    grouped = df.groupby("project_id")
    for pid, project_df in grouped:
        logger.info(f"[DEBUG] Build acta pid={pid} rows={len(project_df)}")
        doc_path = build_acta_for_project(pid, project_df)
        if not doc_path:
            logger.error(f"❌ build_acta returned None for {pid}")
            continue
        doc_count += 1

        first_row = project_df.iloc[0]
        p_name = str(first_row.get("project_name", "UnknownProject"))
        safe_proj = p_name.replace("/", "_").replace(" ", "_")
        s3_key_docx = f"actas/Acta_{safe_proj}_{pid}.docx"
        upload_file_to_s3(doc_path, s3_key_docx)

        # (OPTIONAL) Convert to PDF and upload
        try:
            pdf_path = convert_docx_to_pdf(doc_path)
            if pdf_path:
                s3_key_pdf = f"actas/Acta_{safe_proj}_{pid}.pdf"
                upload_file_to_s3(pdf_path, s3_key_pdf)
        except Exception as exc:
            logger.error(f"PDF conversion failed for doc {doc_path}: {exc}")

    # 9) Upload Excel as well
    s3_excel_key = "actas/Acta_de_Seguimiento.xlsx"
    upload_file_to_s3(excel_path, s3_excel_key)

    msg = {
        "message": f"Multi-Acta creation done. Docs created={doc_count}",
        "excel_s3": f"s3://{S3_BUCKET}/{s3_excel_key}"
    }
    return {"statusCode": 200, "body": json.dumps(msg)}

# ------------------------------------------------------------------------------
# Secrets & OAuth
# ------------------------------------------------------------------------------
def load_secrets():
    sm = boto3.client("secretsmanager", region_name=REGION)
    try:
        resp = sm.get_secret_value(SecretId=SECRET_NAME)
        return json.loads(resp["SecretString"])
    except ClientError as e:
        logger.error(f"Secrets error: {str(e)}")
        return {}

def get_robot_access_token(client_id, client_secret):
    url = f"{PROJECTPLACE_API_URL}/oauth2/access_token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }
    try:
        resp = requests.post(url, data=data)
        resp.raise_for_status()
        return resp.json().get("access_token")
    except requests.exceptions.RequestException as e:
        logger.error(f"Token fetch error: {str(e)}")
        return None

# ------------------------------------------------------------------------------
# Enterprise Projects
# ------------------------------------------------------------------------------
def get_all_account_projects(token, include_archived=False):
    """Return list of enterprise projects (optionally incl. archived)."""
    url = f"{PROJECTPLACE_API_URL}/1/account/projects"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    params = {}
    if include_archived:
        params["include_archived"] = 1
    try:
        r = requests.get(url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            logger.warning(f"Projects response not dict: {type(data)} => {data}")
            return []
        projects_list = data.get("projects", [])
        if not projects_list:
            logger.info("No projects found in /1/account/projects.")
            return []
        if not include_archived:
            active_projects = [p for p in projects_list if not p.get("archived", False)]
            logger.info(f"Fetched {len(active_projects)} active projects.")
            return active_projects
        else:
            logger.info(f"Fetched {len(projects_list)} total projects (including archived).")
            return projects_list
    except requests.exceptions.RequestException as e:
        logger.error(f"Error listing enterprise projects: {e}")
        return []

# ------------------------------------------------------------------------------
# Excel Generation & Card Helpers
# ------------------------------------------------------------------------------
def generate_excel_report(token, projects):
    if not projects:
        logger.warning("No projects provided to generate_excel_report.")
        return None
    all_cards = []
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    for project_info in projects:
        pid = str(project_info.get("id"))
        p_name = project_info.get("name", "Unnamed Project")
        cards_url = f"{PROJECTPLACE_API_URL}/1/projects/{pid}/cards"
        try:
            resp = requests.get(cards_url, headers=headers)
            resp.raise_for_status()
            cards = resp.json()
            for c in cards:
                row = dict(c)
                cid = c.get("id")
                cmts = fetch_comments_for_card(token, cid)
                # Capture label_id
                label_val = None
                if "label_id" in c:
                    label_val = c.get("label_id")
                elif isinstance(c.get("labels"), list) and c["labels"]:
                    label_val = c["labels"][0].get("id")
                row["label_id"] = label_val
                row["Comments"] = str(cmts)
                row["project_id"] = pid
                row["project_name"] = p_name
                row["archived"] = project_info.get("archived", False)
                all_cards.append(row)
            logger.info(f"Fetched {len(cards)} cards from project '{p_name}' ({pid}).")
        except Exception as e:
            logger.error(f"Error fetching cards for project {pid}: {str(e)}")
    if not all_cards:
        logger.warning("No cards found across all projects.")
        return None
    df = pd.DataFrame(all_cards)
    df.to_excel(OUTPUT_EXCEL, index=False)
    logger.info(f"✅ Wrote {len(df)} rows to {OUTPUT_EXCEL}")
    return OUTPUT_EXCEL

def fetch_comments_for_card(token, card_id):
    if not card_id:
        return []
    url = f"{PROJECTPLACE_API_URL}/1/cards/{card_id}/comments"
    headers = {"Authorization": f"Bearer {token}", "Accept":"application/json"}
    out = []
    try:
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        for c in r.json():
            out.append(c.get("text","N/A"))
    except Exception as e:
        logger.error(f"Failed to fetch comments for card {card_id}: {str(e)}")
    return out

# ------------------------------------------------------------------------------
# DynamoDB Ingest
# ------------------------------------------------------------------------------
def store_in_dynamodb(df):
    if df.empty:
        return
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(DYNAMO_TABLE)
    inserted = 0
    for idx, row in df.iterrows():
        pid = row.get("project_id")
        if not pid or pd.isna(pid):
            continue
        item = {
            "project_id": str(pid),
            "card_id": str(row.get("id","N/A")),
            "title": str(row.get("title","N/A")),
            "label_id": str(row.get("label_id","")),
            "timestamp": int(time.time())
        }
        table.put_item(Item=item)
        inserted += 1
    logger.info(f"Inserted {inserted} items into {DYNAMO_TABLE}")

# ------------------------------------------------------------------------------
# Business Filter & Enrichment
# ------------------------------------------------------------------------------
def snippet_filter(df):
    if "column_id" in df.columns:
        df = df[df["column_id"] == 1].copy()
    df["comments_parsed"] = df["Comments"].apply(parse_last_comment)
    if "project" in df.columns:
        df["project_dict"] = df["project"].apply(parse_dict_column)
        df["project_id"] = df["project_dict"].apply(lambda p: p.get("id", None))
        df["project_name"] = df["project_dict"].apply(lambda p: p.get("name","Unknown Project"))
    if "creator" in df.columns:
        df["creator_dict"] = df["creator"].apply(parse_dict_column)
        df["creator_name"] = df["creator_dict"].apply(lambda c: c.get("name","N/A"))
    else:
        df["creator_name"] = "N/A"
    if "planlet" in df.columns:
        df["planlet_dict"] = df["planlet"].apply(parse_dict_column)
        df["planlet_name"] = df["planlet_dict"].apply(lambda d: d.get("label", d.get("name","")))
        df["wbs_str"] = df["planlet_dict"].apply(lambda d: d.get("wbs_id",""))
        df["wbs_tuple"] = df["wbs_str"].apply(parse_wbs_id)
        df = df[df["wbs_tuple"].apply(len)>0].copy()
    df = df[df["comments_parsed"].str.strip() != ""].copy()
    return df

def parse_dict_column(raw_value):
    try:
        return ast.literal_eval(str(raw_value))
    except:
        return {}

def parse_last_comment(raw_value):
    try:
        arr = ast.literal_eval(str(raw_value))
        if isinstance(arr, list) and len(arr) > 0:
            return str(arr[-1])
    except:
        pass
    return ""

def parse_wbs_id(wbs_str):
    if not wbs_str:
        return ()
    parts = wbs_str.split(".")
    out = []
    for p in parts:
        try:
            out.append(int(p.strip().rstrip(",.")))
        except:
            pass
    return tuple(out)

# ----------------------------------------------------------------------------
# 6) BUILD ACTA => includes COMPROMISOS
# ----------------------------------------------------------------------------
def build_acta_for_project(pid, project_df):
    if project_df.empty:
        return None

    if "wbs_tuple" in project_df.columns:
        project_df = project_df.sort_values("wbs_tuple", ascending=True)

    first_row = project_df.iloc[0]
    project_name = str(first_row.get("project_name", "Unknown Project"))
    leader_name = str(first_row.get("creator_name", "N/A"))

    doc = Document()
    set_document_margins_and_orientation(doc)
    add_page_x_of_y_footer(doc)

    add_unified_visual_header(doc, "ACTA DE SEGUIMIENTO", LOGO_IMAGE_PATH)
    doc.add_paragraph()

    date_now = datetime.now().strftime("%m/%d/%Y")
    date_text = f"FECHA DEL ACTA: {date_now}"
    add_two_by_two_table(doc, date_text, project_name, str(pid), leader_name, shade_color=LIGHT_SHADE_2X2)

    doc.add_paragraph()
    add_asistencia_table(doc, project_df)

    doc.add_paragraph()
    add_horizontal_rule(doc)
    doc.add_paragraph()

    add_section_header(doc, "ESTADO DEL PROYECTO Y COMENTARIOS")
    add_project_status_table(doc, project_df)

    doc.add_paragraph()
    add_horizontal_rule(doc)
    doc.add_paragraph()

    add_section_header(doc, "COMPROMISOS")
    add_commitments_table(doc, project_df)
    doc.add_paragraph()

    safe_proj_name = project_name.replace(" ", "_").replace("/", "_")
    doc_path = f"/tmp/Acta_{safe_proj_name}_{pid}.docx"
    doc.save(doc_path)
    logger.info(f"Created doc => {doc_path}")
    return doc_path

def add_project_status_table(doc, df):
    # --- filter rows ---------------------------------------------------
    df = df[df["label_id"] != 0]
    df = df[df.get("board_name", "") != "COMPROMISOS"].copy()
    df = df[~df["planlet_name"].isin(
        ["ASISTENCIA", "ASISTENCIA CLIENTE", "ASISTENCIA IKUSI"]
    )].copy()

    # --- collect rows for the table -----------------------------------
    table_data = []
    for _, row in df.iterrows():
        hito        = str(row.get("planlet_name", ""))
        actividades = str(row.get("title", ""))
        desarrollo  = str(row.get("comments_parsed", ""))
        table_data.append([hito, actividades, desarrollo])

    # --- build the actual Word table (one-time) ------------------------
    table = doc.add_table(rows=1, cols=3)
    table.style  = "Table Grid"
    table.autofit = False

    hdr_row = table.rows[0]
    hdr_row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    hdr_row.height      = Inches(0.45)

    table.columns[0].width = Inches(2.5)
    table.columns[1].width = Inches(2.5)
    table.columns[2].width = Inches(5.0)

    headers = ["HITO/TEMA", "ACTIVIDADES", "DESARROLLO"]
    hdr_cells = hdr_row.cells
    for i, hdr_text in enumerate(headers):
        hdr_cells[i].paragraphs[0].alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
        hdr_cells[i].text = hdr_text
        run = hdr_cells[i].paragraphs[0].runs[0]
        run.bold = True
        run.font.size = Pt(14)
        run.font.name = "Verdana"
        run.font.color.rgb = RGBColor(0xFF,0xFF,0xFF)

        shading_elm = OxmlElement("w:shd")
        shading_elm.set(qn("w:fill"), BRAND_COLOR_HEADER)
        hdr_cells[i]._element.get_or_add_tcPr().append(shading_elm)

    for row_idx, row_data in enumerate(table_data):
        new_cells = table.add_row().cells
        for col_idx, val in enumerate(row_data):
