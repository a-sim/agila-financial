import json
from pathlib import Path

TOKEN_PATH = Path.home() / ".microsoft_mcp_token_cache.json"

def load_token():
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH) as f:
            return json.load(f)
    return None

def get_accounting_folder_url():
    """Return OneDrive URL for the accounting folder for browser opening."""
    return "https://1drv.ms/f/s!AgilaAccounting"
