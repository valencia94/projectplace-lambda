import json, os

FALLBACK_EMAILS = {
    "Default Leader": "default@ikusi.com"
}

def load_email_map():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "email_map.json")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Loading fallback emails: {e}")
        return FALLBACK_EMAILS

EMAIL_MAP = load_email_map()

def resolve_email(name):
    return EMAIL_MAP.get(name.strip(), FALLBACK_EMAILS["Default Leader"])