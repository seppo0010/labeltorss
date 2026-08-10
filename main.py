#!/usr/bin/env python
import os
import sys
import email
import email.utils
import re
import json
import argparse
import datetime
from unidecode import unidecode
import unicodedata
from dateutil.parser import parse
from feedgen.feed import FeedGenerator
import requests
from bs4 import BeautifulSoup
from imapclient import IMAPClient as _IMAPClient

# --- Configuration ---
IMAP_HOST = 'imap.gmail.com'
IMAP_PASSWORD = os.getenv('IMAP_PASSWORD')
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_FOLDER = os.getenv('EMAIL_FOLDER')
OUT_PATH = os.getenv('OUT_PATH')
BASE_URL = os.getenv('BASE_URL')
STATE_FILE = os.path.join(OUT_PATH, 'metadata.json')

VIKUNJA_API_URL = os.getenv('VIKUNJA_API_URL')
VIKUNJA_API_TOKEN = os.getenv('VIKUNJA_API_TOKEN')
VIKUNJA_PROJECT_NAME = '📩Newsletters'

SENDER_TAG_MAP = {
    'someunpleasant@substack.com': 'Mindel',
    'hola@a1000.ar': 'A1000',
    'noteconomics@substack.com': 'Ajzenman',
    'pricetheory@substack.com': 'Hendrickson',
    'aisnakeoil@substack.com': 'Kapoor and Narayanan',
}

SENDER_TAG_REGEX_MAP = [
    (r'causalinf(\+[^@]+)?@substack\.com', 'Cunningham'),
    (r'.+@cenital\.com', 'Cenital'),
]

# --- IMAP Connection ---

class IMAPClient:
    def __init__(self):
        self._conn = None

    def ensure_connected(self):
        if self._conn is not None:
            try:
                self._conn.noop()
                return self._conn
            except Exception:
                print("IMAP connection lost, reconnecting...")
                self._conn = None
        return self._connect()

    def _connect(self):
        conn = _IMAPClient(IMAP_HOST, ssl=True)
        try:
            conn.login(EMAIL_ACCOUNT, IMAP_PASSWORD)
        except Exception:
            print("LOGIN FAILED!!!")
            sys.exit(1)
        conn.select_folder(EMAIL_FOLDER)
        self._conn = conn
        return conn

    def idle_until_change(self, timeout=5 * 60):
        """Block in IDLE until EXISTS/RECENT or timeout. Returns True if new mail signaled."""
        conn = self.ensure_connected()
        try:
            conn.idle()
            responses = conn.idle_check(timeout=timeout)
            conn.idle_done()
            return any(
                isinstance(r, tuple) and len(r) > 1 and r[1] in (b'EXISTS', b'RECENT', b'EXPUNGE')
                for r in responses
            )
        except Exception as e:
            print(f"IDLE interrupted: {e}")
            self._conn = None
            return True  # trigger a fetch on next iteration

    def close(self):
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

try:
    os.makedirs(OUT_PATH)
except FileExistsError:
    pass

# --- Helper Functions ---

def remove_control_characters(s):
    return "".join(ch for ch in str(s) if unicodedata.category(ch)[0]!="C")

def clean_author_name(name):
    if not name:
        return name
    name = str(email.header.make_header(email.header.decode_header(name)))
    if '|' in name:
        name = name[:name.index('|')].strip()
    return name

def load_state():
    """Loads the last seen UID and previous entries."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                return data.get('last_uid', 0), data.get('entries', [])
        except json.JSONDecodeError:
            pass
    return 0, []

def save_state(last_uid, entries):
    """Saves the high-water mark UID and the entry history."""
    entries = sorted(entries, key=lambda x: parse(x['date']), reverse=True)[:20]
    with open(STATE_FILE, 'w') as f:
        json.dump({'last_uid': last_uid, 'entries': entries}, f, indent=4)

def generate_feed(entries):
    """Regenerates the RSS file from the list of entries."""
    sorted_entries = sorted(entries, key=lambda x: parse(x['date']), reverse=True)

    fg = FeedGenerator()
    fg.id(f'{BASE_URL}/rss.xml')
    fg.title('My Newsletters')
    fg.description('Personal Newsletter Feed')
    fg.link(href=f'{BASE_URL}/rss.xml')

    # Append in sorted order (prevents default prepending behaviour)
    for entry in sorted_entries:
        fe = fg.add_entry(order='append')
        fe.id(entry['link'])
        fe.title(entry.get('subject') or entry.get('title') or '(sin asunto)')
        fe.updated(parse(entry['date']))
        fe.link(href=entry['link'], rel='self')
        fe.description(entry.get('description', ''))
        fe.summary(entry.get('description', ''), type='html')
        if entry.get('author'):
            fe.author(name=entry.get('author_name') or entry['author'], email=entry['author'])

    fg.atom_file(os.path.join(OUT_PATH, 'rss.xml'))
    print(f"RSS Feed generated with {len(sorted_entries)} items (Newest first).")

# --- Vikunja Sync ---

def sanitize_title(title):
    title = re.sub(r'#(\S+)', r'\1', title)
    title = re.sub(r'  +', ' ', title).strip()
    return title

def get_tag_name(sender_email):
    if not sender_email:
        return None
    if sender_email in SENDER_TAG_MAP:
        return SENDER_TAG_MAP[sender_email]
    for pattern, tag in SENDER_TAG_REGEX_MAP:
        if re.fullmatch(pattern, sender_email):
            return tag
    return None

def _vikunja_headers():
    return {'Authorization': f'Bearer {VIKUNJA_API_TOKEN}'}

_vikunja_project_id = None

def _get_vikunja_project_id():
    global _vikunja_project_id
    if _vikunja_project_id is not None:
        return _vikunja_project_id
    try:
        response = requests.get(
            f'{VIKUNJA_API_URL}/projects',
            headers=_vikunja_headers(),
            params={'s': VIKUNJA_PROJECT_NAME},
        )
        response.raise_for_status()
        for project in response.json():
            if project['title'] == VIKUNJA_PROJECT_NAME:
                _vikunja_project_id = project['id']
                return _vikunja_project_id
    except Exception as e:
        print(f"Error fetching Vikunja project ID: {e}")
    return None

def _get_label_id(label_name):
    try:
        response = requests.get(
            f'{VIKUNJA_API_URL}/labels',
            headers=_vikunja_headers(),
            params={'s': label_name},
        )
        response.raise_for_status()
        for label in response.json():
            if label['title'] == label_name:
                return label['id']
        print(f"Vikunja label '{label_name}' not found.")
        return None
    except Exception as e:
        print(f"Error fetching Vikunja label ID: {e}")
        return None

def _find_vikunja_task(title, project_id):
    try:
        alpha_words = [w for w in title.split() if w.isalpha() and w.isascii() and len(w) > 4]
        short_query = max(alpha_words, key=len) if alpha_words else None
        params = {'s': short_query} if short_query else {}
        response = requests.get(
            f'{VIKUNJA_API_URL}/projects/{project_id}/tasks',
            headers=_vikunja_headers(),
            params=params,
        )
        response.raise_for_status()
        for task in response.json():
            if sanitize_title(task['title']) == sanitize_title(title):
                return task
        return None
    except Exception as e:
        print(f"Error checking existing Vikunja tasks: {e}")
        return None

def _add_vikunja_task(title, project_id, label_ids=None):
    try:
        existing = _find_vikunja_task(title, project_id)
        if existing:
            existing_label_ids = {l['id'] for l in (existing.get('labels') or [])}
            missing = [l for l in (label_ids or []) if l not in existing_label_ids]
            for label_id in missing:
                requests.put(
                    f'{VIKUNJA_API_URL}/tasks/{existing["id"]}/labels',
                    headers=_vikunja_headers(),
                    json={'label_id': label_id},
                ).raise_for_status()
            if missing:
                print(f"Vikunja: updated labels for existing task: {title}")
            else:
                print(f"Vikunja: task already exists (no label changes): {title}")
            return True
        now = datetime.datetime.now(datetime.timezone.utc)
        payload = {
            'title': title,
            'due_date': now.isoformat(),
        }
        response = requests.put(
            f'{VIKUNJA_API_URL}/projects/{project_id}/tasks',
            headers=_vikunja_headers(),
            json=payload,
        )
        response.raise_for_status()
        task = response.json()
        for label_id in (label_ids or []):
            requests.put(
                f'{VIKUNJA_API_URL}/tasks/{task["id"]}/labels',
                headers=_vikunja_headers(),
                json={'label_id': label_id},
            ).raise_for_status()
        print(f"Vikunja: added task: {title}")
        return True
    except Exception as e:
        print(f"Error adding Vikunja task: {e}")
        return False

def sync_entries_to_vikunja(entries):
    pending = [e for e in entries if not e.get('vikunja_synced')]
    if not pending:
        return
    project_id = _get_vikunja_project_id()
    if not project_id:
        print(f"Vikunja sync: project '{VIKUNJA_PROJECT_NAME}' not found, skipping.")
        return
    for entry in pending:
        author_email = entry.get('author')
        author_name = entry.get('author_name')
        if author_name and '@' in author_name:
            author_name = None
        title = entry.get('subject') or entry.get('title')
        pub_date = None
        if entry.get('date'):
            try:
                pub_date = parse(entry['date']).strftime('%Y-%m-%d')
            except Exception:
                pass

        label_ids = []
        tag_name = get_tag_name(author_email)
        if tag_name:
            label_id = _get_label_id(tag_name)
            if label_id:
                label_ids.append(label_id)

        display_author = author_name or tag_name
        parts = ([display_author] if display_author else []) + ([title] if title else [])
        task_title = ' - '.join(parts)
        if pub_date:
            task_title += f' ({pub_date})'

        entry['vikunja_synced'] = _add_vikunja_task(sanitize_title(task_title), project_id, label_ids)

# --- Core Logic ---

def strip_icon_images(html):
    """Remove decorative icon <img> tags (e.g. Substack UI icons) that tend to return 404."""
    soup = BeautifulSoup(html, 'html.parser')
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if '%2Ficon%2F' in src or '/icon/' in src:
            img.decompose()
    return str(soup)

def fetch_web_title(url):
    """Fetches the <title> tag from a URL."""
    print(f"Fetching title for: {url}...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, 'html.parser')
            if soup.title and soup.title.string:
                return soup.title.string.strip()
    except Exception as e:
        print(f"Warning: Could not fetch title ({e}).")
    return url

def add_manual_link(url):
    """Adds a custom web link to the feed, automatically parsing the title."""
    last_uid, entries = load_state()
    title = fetch_web_title(url)
    new_entry = {
        'date': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'title': title,
        'link': url,
        'description': f"External Link: {title}",
        'author': 'manual@link'
    }
    entries.append(new_entry)
    generate_feed(entries)
    sync_entries_to_vikunja(entries)
    save_state(last_uid, entries)
    print(f"Successfully added: {title}")

def fetch_emails(client):
    """Fetches new emails and updates the feed."""
    conn = client.ensure_connected()
    last_uid, existing_entries = load_state()

    current_uids = set(conn.search(['ALL']))

    # Remove entries for emails that no longer have the label
    before = len(existing_entries)
    existing_entries = [e for e in existing_entries if 'uid' not in e or e['uid'] in current_uids]
    removed = before - len(existing_entries)
    if removed:
        print(f"Removed {removed} entries no longer in folder.")

    known_uids = {e['uid'] for e in existing_entries if 'uid' in e}
    new_uids = sorted(uid for uid in current_uids if uid not in known_uids)
    print(f"Checking for new emails (known: {len(known_uids)}, in folder: {len(current_uids)}, new: {len(new_uids)})...")

    new_entries = []
    current_max_uid = max(current_uids) if current_uids else last_uid

    if new_uids:
        fetch_data = conn.fetch(new_uids, ['RFC822'])
        for uid in new_uids:
            raw = fetch_data.get(uid, {}).get(b'RFC822')
            if not raw:
                continue

            msg = email.message_from_bytes(raw)
            subject = str(email.header.make_header(email.header.decode_header(msg['Subject'] or '')))

            from_header = msg.get('From')
            name, sender_email = email.utils.parseaddr(from_header)

            body, current_ctype = "", None
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if current_ctype is None or ctype == 'text/html':
                        body = (part.get_payload(decode=True) or b'').decode('utf-8', errors='backslashreplace')
                        current_ctype = ctype
            else:
                body = (msg.get_payload(decode=True) or b'').decode('utf-8', errors='backslashreplace')

            body = strip_icon_images(body)

            id_ = re.sub('[^0-9a-zA-Z]+', '_', unidecode(subject)).strip('_') or f'email_{uid}'
            file_name = f'{id_}.html'
            with open(os.path.join(OUT_PATH, file_name), 'w') as fp:
                fp.write(body)

            date_obj = parse(msg['Date'])
            new_entries.append({
                'uid': uid,
                'date': date_obj.isoformat(),
                'title': id_,
                'subject': subject or '(sin asunto)',
                'link': f'{BASE_URL}/{file_name}',
                'description': remove_control_characters(body.strip()),
                'author': sender_email,
                'author_name': clean_author_name(name),
            })

        print(f"Processed {len(new_entries)} new emails.")
    else:
        print("No new emails.")

    all_entries = existing_entries + new_entries
    print([e['title'] for e in all_entries])
    generate_feed(all_entries)
    sync_entries_to_vikunja(all_entries)
    save_state(current_max_uid, all_entries)

def migrate_entries(client):
    """Backfills author_name and subject for existing entries that lack them."""
    last_uid, entries = load_state()

    needs_update = [e for e in entries if 'uid' in e and (
        'author_name' not in e or 'subject' not in e or
        (e.get('author_name') and '=?' in e['author_name'])
    )]
    if not needs_update:
        print("All entries already have author_name and subject.")
        return

    conn = client.ensure_connected()

    for entry in needs_update:
        uid = entry['uid']
        fetch_data = conn.fetch([uid], ['RFC822.HEADER'])
        raw = fetch_data.get(uid, {}).get(b'RFC822.HEADER')
        if not raw:
            print(f"Could not fetch UID {uid}")
            continue

        msg = email.message_from_bytes(raw)
        subject = str(email.header.make_header(email.header.decode_header(msg['Subject'])))
        from_header = msg.get('From')
        name, _ = email.utils.parseaddr(from_header)

        entry['subject'] = subject
        entry['author_name'] = clean_author_name(name)
        print(f"Updated UID {uid}: author_name={name!r}, subject={subject!r}")

    save_state(last_uid, entries)
    generate_feed(entries)
    print("Migration complete.")


# --- Entry Point ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Email to RSS with Manual Link Support")
    parser.add_argument('--add', metavar='URL', help='Add a manual link to the RSS feed')
    parser.add_argument('--migrate', action='store_true', help='Backfill author_name and subject for existing entries')

    args = parser.parse_args()

    if args.add:
        add_manual_link(args.add)
    else:
        with IMAPClient() as client:
            if args.migrate:
                migrate_entries(client)
            else:
                while True:
                    fetch_emails(client)
                    print("Waiting for new mail (IDLE)...")
                    client.idle_until_change()
