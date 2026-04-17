#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# flake8: noqa
# type: ignore

import json, os, re, shutil, sqlite3, subprocess, sys, tempfile, unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import requests

# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent.parent / 'agila.db'
TOKEN_CACHE = Path.home() / '.microsoft_mcp_token_cache.json'
MCPORTER = Path.home() / '.npm-global/bin/mcporter'
ATTACHMENTS_DIR = Path(__file__).parent.parent / 'data' / 'attachments'
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

ACCOUNT_ID = '87fbfc0e-dfa2-4621-aab2-319dad4e93ae.c44c0a70-24ac-4b5c-adc5-8c24d4f62e21'

# Destination Outlook folder
ACCOUNTING_FOLDER_ID = 'AAMkAGY1MmU1ODA4LTM1ODUtNGRlNC1iMzkwLWNkMzY5MjlkZjNkMAAuAAAAAABJeHP2wkpCToySbdGEyMhDAQAaI8cPJGD4Q7IgwPjtj_8LAAJN613FAAA='

# SAFETY: This script NEVER deletes emails. It only moves them.
# mcporter move_email is BANNED - it can hard-delete when folder lookup fails.
# All moves go through Graph API /messages/{id}/move with pre/post verification.

# OneDrive Q1–Q4 folders (Q3/Q4 created on demand)
STATEMENTS_FOLDER_ID = '017IBDTVS6U7GEAQVW4FH3WPYHKWF26BGE'
GRAPH_BASE = 'https://graph.microsoft.com/v1.0/me/drive'

ONEDRIVE_FOLDERS = {
    'Q1': {'folder_id': '017IBDTVTNKFRJBZNNP5CKSZAULMNHDUML',
           'path': '01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/2026Q1_Invoices-Receipts'},
    'Q2': {'folder_id': '017IBDTVQXONRYF6RZM5EIF4PLJJTUV4CE',
           'path': '01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/2026Q2_Invoices-Receipts'},
    'Q3': {'folder_id': None, 'path': None},
    'Q4': {'folder_id': None, 'path': None},
}

INVOICE_EXTENSIONS  = {'.pdf', '.xml'}
RECEIPT_EXTENSIONS  = {'.pdf', '.jpg', '.jpeg', '.png', '.heic'}

# ── Constants: filtering ──────────────────────────────────────────────────────

# Sender domains that are definitively NOT Agila accounting
SKIP_DOMAINS = {
    'puigfontanals.com',  # IVO tax declarations
    'rmt-labs.com',       # RMT Labs proposals
    'luxinnovation.lu',   # Event registrations
}

# Subject keywords → skip (non-accounting)
SKIP_SUBJECT_KW = {
    'registration confirmed', 'qrcode', 'qr code',
}

# Keywords that indicate accounting relevance (invoice, receipt, travel booking, etc.)
ACCOUNTING_KW = {
    'invoice', 'factura', 'facture', 'receipt', 'recibo', 'reçu',
    'statement', 'bank', 'vat', 'tva', 'tax', 'impôt',
    'declaration', 'déclaration', 'compliance', 'accounting',
    'subscription', 'abonnement', 'payment', 'pago', 'paiement',
    'order', 'pedido', 'commande', 'purchase', 'compra',
    'd.ieteren', 'd''ieteren', 'everestcard', 'attc', 'fiscoges',
    'odoo', 'revolut', 'sage', 'puigfontanals',
    'e-ticket', 'eticket', 'boarding pass',
    'your booking', 'we have everything for your trip',
    'your receipt', 'confirmación de viaje', 'reserva de viaje',
}

# Attachment-name keywords that override to include
ATT_INCLUDE_KW = {
    'invoice', 'receipt', 'factura', 'facture', 'reçu', 'recibo',
    'statement', 'eticket', 'e-ticket', 'boarding',
}

# Per-category VAT
VAT_RATES = {
    'restaurant': 0.17, 'hotel': 0.03, 'travel': 0.17, 'flight': 0.17,
    'software': 0.17, 'subscription': 0.17, 'office': 0.17,
    'professional': 0.17, 'taxi': 0.17, 'car': 0.17, 'parking': 0.17,
    'other': 0.17,
}
NON_RECOVERABLE = {'restaurant'}

CATEGORY_KEYWORDS = {
    'restaurant': ['cafe','restaurant','brasserie','bella','ciao','lloyd','coffee',
                   'osteria','cantine','beaulieu','coron','globe','rive','mudanza',
                   'dinner','lunch','meal','pizzeria'],
    'hotel':      ['agoda','hotel','booking','accommodation','yorkdesign'],
    'travel':     ['luxair','flight','lufthansa','sas','uber','taxi','train',
                   'brussels','bio','airplane','aeromexico','kiwi','kayak'],
    'flight':     ['flight','lufthansa','luxair','sas','airline','aeromexico','kiwi.com'],
    'software':   ['anthropic','claude','openai','openrouter','zoom','notion',
                   'subscription','github','aws','digitalocean','vercel','netlify'],
    'subscription':['mobile vikings','vikings'],
    'office':     ['amazon','office','supplies'],
    'professional':['attc','luxtrust','siliconlux','fiscoges','advisory','fiscoGes','puigfontanals'],
    'taxi':       ['uber','taxi'],
    'car':        ['tesla','charging','garage','quaresma','adtyres','deleren',
                   'd.ieteren','energy','vignette','toll'],
    'parking':    ['parking','serviparc'],
    'other':      [],
}

# ── Token ─────────────────────────────────────────────────────────────────────

def get_access_token() -> str:
    with open(TOKEN_CACHE) as f:
        cache = json.load(f)
    at = cache.get('AccessToken', {})
    for key, val in at.items():
        if isinstance(val, dict) and 'secret' in val:
            return val['secret']
    raise RuntimeError('No access token found')


# ── Graph helpers ─────────────────────────────────────────────────────────────

def graph_get(url: str, token: str) -> dict:
    resp = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def graph_post(url: str, token: str, json_body: dict) -> dict:
    resp = requests.post(url, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                         json=json_body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def graph_put(url: str, token: str, data: bytes, content_type: str) -> dict:
    resp = requests.put(url, headers={'Authorization': f'Bearer {token}', 'Content-Type': content_type},
                        data=data, timeout=60)
    resp.raise_for_status()
    return resp.json()


def graph_patch(url: str, token: str, json_body: dict) -> dict:
    resp = requests.patch(url, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                          json=json_body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def graph_delete(url: str, token: str) -> int:
    resp = requests.delete(url, headers={'Authorization': f'Bearer {token}'}, timeout=15)
    return resp.status_code


def ensure_onedrive_quarter_folder(quarter: str, token: str) -> tuple:
    info = ONEDRIVE_FOLDERS[quarter]
    if info.get('folder_id'):
        return info['folder_id'], info['path']

    folder_name = f'2026{quarter}_Invoices-Receipts'
    try:
        result = graph_get(
            f'{GRAPH_BASE}/items/{STATEMENTS_FOLDER_ID}/children'
            f'?$filter=name eq \u0027{folder_name}\u0027&$select=id,name,folder',
            token,
        )
        for item in result.get('value', []):
            if item.get('folder'):
                info['folder_id'] = item['id']
                info['path'] = f'01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/{folder_name}'
                return info['folder_id'], info['path']
    except Exception:
        pass

    result = graph_post(f'{GRAPH_BASE}/items/{STATEMENTS_FOLDER_ID}/children',
                        token, {'name': folder_name, 'folder': {}})
    info['folder_id'] = result['id']
    info['path'] = f'01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/{folder_name}'
    return info['folder_id'], info['path']


def upload_to_onedrive(folder_id: str, filename: str, content: bytes,
                       content_type: str, token: str) -> dict:
    from urllib.parse import quote
    safe_name = quote(filename, safe='')

    # Check existing
    try:
        result = graph_get(
            f'{GRAPH_BASE}/items/{folder_id}/children'
            f'?$filter=name eq \u0027{safe_name}\u0027&$select=id,name',
            token,
        )
        for item in result.get('value', []):
            item_id = item.get('id', '')
            url = f'{GRAPH_BASE}/items/{item_id}/content'
            return graph_put(url, token, content, content_type)
    except Exception:
        pass

    url = f'{GRAPH_BASE}/items/{folder_id}:/{safe_name}:/content'
    return graph_put(url, token, content, content_type)


# ── mcporter helpers ──────────────────────────────────────────────────────────

def mcporter_call(tool: str, args: dict) -> dict:
    result = subprocess.run(
        [str(MCPORTER), 'call', f'microsoft.{tool}', '--args', json.dumps(args)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f'mcporter error: {result.stderr[:300]}')
    raw = result.stdout.strip()
    try:
        wrapped = json.loads(raw)
        if isinstance(wrapped, dict) and 'content' in wrapped:
            inner = wrapped['content']
            if isinstance(inner, list) and len(inner) > 0:
                return json.loads(inner[0].get('text', raw))
        return json.loads(raw)
    except json.JSONDecodeError:
        return {'raw': raw}


def list_emails_with_attachments(limit: int = 50) -> list:
    result = mcporter_call('list_emails', {
        'account_id': ACCOUNT_ID, 'folder': 'inbox',
        'limit': limit, 'include_body': False,
    })
    if isinstance(result, list):
        return [e for e in result if e.get('hasAttachments')]
    return []


def get_email_with_attachments(email_id: str) -> dict:
    return mcporter_call('get_email', {
        'account_id': ACCOUNT_ID, 'email_id': email_id,
        'include_body': False, 'include_attachments': True,
    })


def download_attachment(email_id: str, attachment_id: str, save_path: str) -> dict:
    return mcporter_call('get_attachment', {
        'account_id': ACCOUNT_ID, 'email_id': email_id,
        'attachment_id': attachment_id, 'save_path': save_path,
    })


def verify_email_exists(email_id: str, token: str) -> bool:
    """Verify an email still exists in the mailbox."""
    try:
        resp = requests.get(
            f'https://graph.microsoft.com/v1.0/me/messages/{email_id}?$select=id',
            headers={'Authorization': f'Bearer {token}'}, timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def move_email_to_accounting(email_id: str, token: str) -> bool:
    """Move email with pre-move and post-move verification.

    Safety protocol (prevents data loss):
    1. Verify email exists before move
    2. Call Graph API move
    3. Verify email arrived in destination folder
    4. If post-verify fails, flag critical error
    5. Never consider move successful unless email is confirmed in destination
    """
    # Phase 1: Pre-move verification
    if not verify_email_exists(email_id, token):
        print('    PRE-MOVE CHECK FAILED: email not found. ABORTING move.')
        return False

    # Phase 2: Execute move
    try:
        resp = requests.post(
            f'https://graph.microsoft.com/v1.0/me/messages/{email_id}/move',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={'destinationId': ACCOUNTING_FOLDER_ID}, timeout=15,
        )
        if resp.status_code not in (200, 201):
            print(f'    Move API returned {resp.status_code}. ABORTING.')
            return False
    except Exception as e:
        print(f'    Move API exception: {e}. ABORTING.')
        return False

    # Phase 3: Post-move verification
    new_id = resp.json().get('id', email_id)
    try:
        verify_resp = requests.get(
            f'https://graph.microsoft.com/v1.0/me/messages/{new_id}?$select=id,parentFolderId',
            headers={'Authorization': f'Bearer {token}'}, timeout=10,
        )
        if verify_resp.status_code == 200:
            folder_id = verify_resp.json().get('parentFolderId', '')
            if folder_id == ACCOUNTING_FOLDER_ID:
                return True
            else:
                print(f'    POST-MOVE WARNING: email in wrong folder.')
                return False
        else:
            print(f'    POST-MOVE CRITICAL: email not found after move. EMAIL MAY BE LOST!')
            return False
    except Exception as e:
        print(f'    POST-MOVE verification exception: {e}. Cannot confirm email safety.')
        return False


# ── PDF parsing ───────────────────────────────────────────────────────────────

def extract_amount_from_pdf(pdf_path: Path) -> Optional[float]:
    def _parse_float(s: str) -> Optional[float]:
        if not s: return None
        if ',' in s and '.' in s:
            s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
        elif ',' in s:
            parts = s.rsplit(',', 1)
            s = s.replace(',', '.') if len(parts[1]) == 2 else s.replace(',', '')
        try:
            val = float(s)
            return val if 0 < val < 1_000_000 else None
        except ValueError:
            return None

    text = ''
    try:
        r = subprocess.run(['pdftotext', str(pdf_path), '-'], capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            text = r.stdout
    except FileNotFoundError:
        pass

    if not text.strip():
        try:
            r = subprocess.run(['strings', str(pdf_path)], capture_output=True, text=True, timeout=10)
            text = r.stdout
        except Exception:
            pass

    if not text:
        return None

    patterns = [
        r'Importe\\s*Total[:\\s]*\\n?\\s*([\\d.,]+)',
        r'Total\\s*a\\s*Pagar[:\\s]*([\\d.,]+)',
        r'Montant\\s*Total[:\\s]*([\\d.,]+)',
        r'Net\\s*a\\s*Payer[:\\s]*([\\d.,]+)',
        r'Amount\\s*Due[:\\s]*([\\d.,]+)',
        r'Invoice\\s*Total[:\\s]*([\\d.,]+)',
        r'Grand\\s*Total[:\\s]*([\\d.,]+)',
        r'Total[:\\s]+([\\d.,]+)\\s*(?:EUR|€|USD)',
        r'(?:Total|total)[:\\s]+([\\d.,]+)',
        r'MXN\\s+([\\d.,]+\\d)', r'USD\\s+([\\d.,]+\\d)', r'EUR\\s+([\\d.,]+\\d)',
        r'€\\s*([\\d.,]+\\d)', r'([\\d.,]+\\d)\\s*€',
    ]

    amounts = []
    for pat in patterns:
        for m in re.findall(pat, text, re.MULTILINE | re.IGNORECASE):
            val = _parse_float(m.strip())
            if val:
                amounts.append(val)

    return max(amounts) if amounts else None


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_accounting_email(sender_email: str, subject: str,
                         attachment_names: List[str]) -> bool:
    sender_domain = sender_email.split('@')[-1].lower() if sender_email else ''
    sub = subject.lower()
    combined = f'{sender_domain} {sub}'

    # Skip non-Agila domains
    if any(d in sender_domain for d in SKIP_DOMAINS):
        return False

    # Skip non-accounting subject lines
    if any(kw in sub for kw in SKIP_SUBJECT_KW):
        return False

    # Positive: accounting keywords
    if any(kw in combined for kw in ACCOUNTING_KW):
        return True

    # Override: attachment name contains invoice/receipt
    for att in attachment_names:
        if any(kw in att.lower() for kw in ATT_INCLUDE_KW):
            return True

    # Default: skip
    return False


def infer_category(vendor: str, sender_domain: str, subject: str) -> str:
    text = f'{vendor} {sender_domain} {subject}'.lower()
    best_cat, best_score = 'other', 0
    for cat, kws in CATEGORY_KEYWORDS.items():
        if cat == 'other':
            continue
        score = sum(1 for kw in kws if kw.lower() in text)
        if score > best_score:
            best_score, best_cat = score, cat
    return best_cat


def get_quarter(date_str: str) -> str:
    try:
        month = int(date_str[5:7]) if date_str and '-' in date_str else 4
        if month <= 3:   return 'Q1'
        elif month <= 6: return 'Q2'
        elif month <= 9: return 'Q3'
        else:            return 'Q4'
    except (ValueError, IndexError):
        return 'Q2'


def make_filename(date_str: str, vendor: str, amount: float, ext: str) -> str:
    # Strip accents, non-ASCII chars
    vendor_norm = unicodedata.normalize('NFKD', vendor)
    vendor_ascii = vendor_norm.encode('ascii', 'ignore').decode('ascii')
    safe_vendor = re.sub(r'[^\u0041-\u005a\u0061-\u007a0-9_\\-]', '_', vendor_ascii).strip()[:30]
    ds = date_str.replace('-', '')[:8] if date_str else datetime.now().strftime('%Y%m%d')
    amount_str = f'{amount:.2f}'.replace('.', '')
    name = f'{ds}_{safe_vendor}_{amount_str}EUR{ext}'
    return re.sub(r'_{2,}', '_', name).strip('_')


def clean_filename(s: str) -> str:
    return re.sub(r'[<>:/\\|?*]', '_', s)


# ── Database ──────────────────────────────────────────────────────────────────

def ensure_columns():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    for col in ('onedrive_path', 'email_id', 'email_subject'):
        cur.execute('PRAGMA table_info(expenses)')
        existing = {r[1] for r in cur.fetchall()}
        if col not in existing:
            cur.execute(f'ALTER TABLE expenses ADD COLUMN {col} TEXT')
    conn.commit()
    conn.close()


def insert_expense(date_str, amount, category, vendor, description,
                   filename, onedrive_id, onedrive_path, email_id, email_subject) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    vat_rate   = VAT_RATES.get(category, 0.17)
    amount_vat = round(amount * vat_rate, 2)
    vat_rec    = 0.0 if category in NON_RECOVERABLE else amount_vat
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO expenses (date, amount, amount_vat, category, vendor,
            description, vat_rate, vat_recoverable, onedrive_id, onedrive_path,
            source, notes, status, email_id, email_subject)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'email_attachment', ?, 'pending', ?, ?)
    ''', (date_str, amount, amount_vat, category, vendor, description,
          vat_rate, vat_rec, onedrive_id, onedrive_path, f'File: {filename}',
          email_id, email_subject))
    conn.commit()
    conn.close()
    return cur.lastrowid


def update_expense(expense_id, amount, onedrive_id, onedrive_path, filename):
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute('''
        UPDATE expenses SET
            amount = ?, onedrive_id = ?, onedrive_path = ?,
            notes = COALESCE(notes,'') || ' | File: ' || ?, status = 'pending'
        WHERE id = ?
    ''', (amount, onedrive_id, onedrive_path, filename, expense_id))
    conn.commit()
    conn.close()


def expense_exists(date_str, vendor, amount) -> Optional[int]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT id FROM expenses WHERE date=? AND vendor=? AND amount=?',
                (date_str, vendor, amount))
    row = cur.fetchone()
    conn.close()
    return row['id'] if row else None


# ── Telegram notification ──────────────────────────────────────────────────────

def notify_telegram(message: str):
    bot_token = os.environ.get('AGILA_BOT_TOKEN', '')
    if not bot_token:
        env_path = Path.home() / '.agila-telegram' / '.env'
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith('BOT_TOKEN='):
                    bot_token = line.split('=', 1)[1].strip()
                    break
    if not bot_token:
        print('  [WARN] No bot token for Telegram notification')
        return

    chat_id = '8366636229'
    try:
        requests.post(
            f'https://api.telegram.org/bot{bot_token}/sendMessage',
            json={'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'},
            timeout=10,
        )
    except Exception as e:
        print(f'  [WARN] Telegram notification failed: {e}')


# ── Main ───────────────────────────────────────────────────────────────────────

def process_accounting_emails(dry_run: bool = False, limit: int = 50, no_move: bool = False) -> dict:
    ensure_columns()
    token = get_access_token()
    emails = list_emails_with_attachments(limit=limit)
    print(f'Found {len(emails)} emails with attachments in inbox')

    summary = {'processed': 0, 'inserted': 0, 'moved': 0,
               'skipped': 0, 'errors': 0, 'details': []}

    for email in emails:
        email_id    = email['id']
        subject     = email.get('subject', 'Unknown')
        from_addr   = email.get('from', {}).get('emailAddress', {})
        sender_name = from_addr.get('name', 'Unknown')
        sender_email = from_addr.get('address', '')
        sender_domain = sender_email.split('@')[-1].lower() if sender_email else ''
        received    = email.get('receivedDateTime', '')[:10]

        # Fetch full email with attachments
        try:
            full_email = get_email_with_attachments(email_id)
        except Exception as e:
            print(f'  ERROR fetching email: {e}')
            summary['errors'] += 1
            continue

        attachments = full_email.get('attachments', [])
        if not attachments:
            summary['skipped'] += 1
            continue

        attachment_names = [a.get('name', '') for a in attachments]

        # Accounting relevance check
        if not is_accounting_email(sender_email, subject, attachment_names):
            print(f'  SKIP (non-accounting): {sender_email} / {subject[:50]}')
            summary['skipped'] += 1
            continue

        # Filter to relevant file types
        relevant_atts = [a for a in attachments
                        if Path(a.get('name', '')).suffix.lower()
                        in (INVOICE_EXTENSIONS | RECEIPT_EXTENSIONS)]

        if not relevant_atts:
            print(f'  SKIP (no relevant attachments): {subject[:50]}')
            summary['skipped'] += 1
            continue

        print(f'\n  Email: {subject[:70]}')
        print(f'  From: {sender_name} <{sender_email}>, Date: {received}')
        print(f'  Relevant attachments: {len(relevant_atts)}')

        email_processed = False
        email_has_error = False

        for att in relevant_atts:
            att_id           = att.get('id')
            att_name         = att.get('name', 'unknown')
            att_content_type = att.get('contentType', 'application/octet-stream')
            ext              = Path(att_name).suffix.lower()

            # Download
            if dry_run:
                print(f'    [DRY RUN] Would download: {att_name}')
                amount = None
                local_path = None
            else:
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                try:
                    download_attachment(email_id, att_id, str(tmp_path))
                except Exception as e:
                    print(f'    ERROR downloading {att_name}: {e}')
                    email_has_error = True
                    tmp_path.unlink(missing_ok=True)
                    continue

                amount = extract_amount_from_pdf(tmp_path) if ext == '.pdf' else None
                if amount:
                    print(f'    Amount extracted: EUR {amount:.2f}')

                vendor_clean = re.sub(r'[^\u0041-\u005a\u0061-\u007a0-9 _\\-]', '',
                                      sender_name).strip()[:30]
                if amount is None:
                    amount = 0.0

                filename = clean_filename(make_filename(received, vendor_clean, amount, ext))
                local_path = ATTACHMENTS_DIR / filename
                shutil.copy2(tmp_path, local_path)
                tmp_path.unlink(missing_ok=True)
                print(f'    Saved: {filename}')

            # Determine quarter, ensure OneDrive folder
            quarter = get_quarter(received)
            folder_info = ONEDRIVE_FOLDERS[quarter]

            if not dry_run:
                if not folder_info.get('folder_id'):
                    try:
                        fid, fpath = ensure_onedrive_quarter_folder(quarter, token)
                        folder_info['folder_id'] = fid
                        folder_info['path'] = fpath
                        print(f'    Created OneDrive folder: {quarter}')
                    except Exception as e:
                        print(f'    ERROR creating {quarter} folder: {e}')
                        email_has_error = True
                        continue

                # Upload to OneDrive
                onedrive_id = ''
                try:
                    result = upload_to_onedrive(
                        folder_info['folder_id'], local_path.name,
                        local_path.read_bytes(), att_content_type, token,
                    )
                    onedrive_id = result.get('id', '')
                    onedrive_path = folder_info['path'] + '/' + local_path.name
                    print(f'    OneDrive: OK')
                except Exception as e:
                    print(f'    OneDrive upload ERROR: {e}')
                    email_has_error = True
                    onedrive_path = ''

                # DB insert/update
                if amount and amount > 0:
                    existing_id = expense_exists(received, sender_name, amount)
                    category = infer_category(sender_name, sender_domain, subject)
                    if existing_id:
                        update_expense(existing_id, amount, onedrive_id,
                                       onedrive_path, local_path.name)
                        print(f'    DB: updated #{existing_id}')
                    else:
                        eid = insert_expense(
                            received, amount, category, sender_name, subject,
                            local_path.name, onedrive_id, onedrive_path,
                            email_id, subject,
                        )
                        summary['inserted'] += 1
                        print(f'    DB: inserted #{eid} ({category})')

            summary['processed'] += 1
            email_processed = True
            summary['details'].append({
                'filename': local_path.name if local_path else att_name,
                'sender': sender_name, 'subject': subject[:60],
                'amount': amount, 'quarter': quarter,
                'date': received, 'dry_run': dry_run,
            })

        # Move email to Accounting folder
        # SAFETY: only move if no errors AND no-move flag is not set
        if email_processed and not email_has_error and not dry_run and not no_move:
            if move_email_to_accounting(email_id, token):
                summary['moved'] += 1
                print('  Moved to Agila/LU/LU_Int/01_Accounting')
            else:
                print('  ERROR: failed to move email')
                summary['errors'] += 1
        elif email_processed and (email_has_error or no_move):
            reason = 'processing errors' if email_has_error else '--no-move flag'
            print(f'  NOT moving ({reason})')

    return summary


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Process Agila accounting emails')
    parser.add_argument('--dry-run',   action='store_true')
    parser.add_argument('--limit',     type=int, default=50)
    parser.add_argument('--no-notify', action='store_true')
    parser.add_argument('--no-move', action='store_true', help='Download and upload but do NOT move emails')
    args = parser.parse_args()

    print('=' * 60)
    print('Agila Accounting Email Processor')
    print(f'Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}')
    if args.dry_run:
        print('[DRY RUN]')
    print('=' * 60)

    summary = process_accounting_emails(dry_run=args.dry_run, limit=args.limit, no_move=args.no_move)

    print(f'\n{'='*60}')
    print(f'Summary: {summary['processed']} processed | {summary['inserted']} inserted | '
          f'{summary['moved']} moved | {summary['skipped']} skipped | {summary['errors']} errors')
    for d in summary.get('details', []):
        amt = f'EUR {d['amount']:.2f}' if d.get('amount') else 'amount unknown'
        print(f'  {d['quarter']} | {amt:>14} | {d['sender'][:25]} | {d['filename']}')

    if not args.no_notify and not args.dry_run:
        lines = [f'📋 Agila Email Processor']
        lines.append(f'{summary['processed']} processed | {summary['inserted']} inserted | '
                     f'{summary['moved']} moved | {summary['errors']} errors')
        for d in summary.get('details', []):
            amt = f'€{d['amount']:.2f}' if d.get('amount') else '€?'
            lines.append(f'• {d['quarter']} {amt} {d['sender'][:20]}')
        if summary['errors'] > 0:
            lines.append(f'⚠️ {summary['errors']} error(s) — check logs')
        notify_telegram('\n'.join(lines))


if __name__ == '__main__':
    main()