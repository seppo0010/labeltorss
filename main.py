#!/usr/bin/env python
import os
import sys
import imaplib
import email
import re
import json
import argparse
import datetime
from unidecode import unidecode
import unicodedata
from dateutil.parser import parse
from feedgen.feed import FeedGenerator

# New imports for fetching web titles
import requests
from bs4 import BeautifulSoup

# --- Configuration ---
IMAP_HOST = 'imap.gmail.com'
IMAP_PASSWORD = os.getenv('IMAP_PASSWORD')
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_FOLDER = os.getenv('EMAIL_FOLDER')
OUT_PATH = os.getenv('OUT_PATH')
BASE_URL = os.getenv('BASE_URL')
STATE_FILE = os.path.join(OUT_PATH, 'metadata.json')

try:
    os.makedirs(OUT_PATH)
except FileExistsError:
    pass

# --- Helper Functions ---

def remove_control_characters(s):
    return "".join(ch for ch in str(s) if unicodedata.category(ch)[0]!="C")

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
    with open(STATE_FILE, 'w') as f:
        json.dump({'last_uid': last_uid, 'entries': entries}, f, indent=4)

def generate_feed(entries):
    """Regenerates the RSS file from the list of entries."""
    # Strict Sort: Date Descending (Newest First)
    # reverse=True ensures the latest dates are at index 0
    sorted_entries = sorted(entries, key=lambda x: parse(x['date']), reverse=True)

    fg = FeedGenerator()
    fg.id(f'{BASE_URL}/rss.xml')
    fg.title('My Newsletters')
    fg.description('Personal Newsletter Feed')
    fg.link(href=f'{BASE_URL}/rss.xml')

    # Add entries in order (Newest -> Oldest)
    for entry in sorted_entries:
        fe = fg.add_entry()
        fe.id(entry['link'])
        fe.title(entry['title'])
        fe.updated(parse(entry['date']))
        fe.link(href=entry['link'], rel='self')
        fe.description(entry.get('description', ''))
        fe.summary(entry.get('description', ''), type='html')

    fg.atom_file(os.path.join(OUT_PATH, 'rss.xml'))
    print(f"RSS Feed generated with {len(sorted_entries)} items (Newest first).")

# --- Core Logic ---

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
    
    return url  # Fallback to URL if title fetch fails

def add_manual_link(url):
    """Adds a custom web link to the feed, automatically parsing the title."""
    last_uid, entries = load_state()
    
    title = fetch_web_title(url)
    
    new_entry = {
        'date': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'title': title,
        'link': url,
        'description': f"External Link: {title}"
    }
    
    entries.append(new_entry)
    save_state(last_uid, entries)
    generate_feed(entries)
    print(f"Successfully added: {title}")

def fetch_emails():
    """Connects to IMAP, fetches new emails, and updates the feed."""
    M = imaplib.IMAP4_SSL(IMAP_HOST)

    try:
        M.login(EMAIL_ACCOUNT, IMAP_PASSWORD)
    except imaplib.IMAP4.error:
        print("LOGIN FAILED!!!")
        sys.exit(1)

    rv, data = M.select(EMAIL_FOLDER)
    if rv != 'OK':
        print("ERROR: Unable to open mailbox ", rv)
        M.logout()
        return

    last_uid, existing_entries = load_state()
    print(f"Checking for new emails (Last UID: {last_uid})...")

    if last_uid > 0:
        search_crit = f'{last_uid + 1}:*'
        rv, data = M.uid('search', None, search_crit)
    else:
        rv, data = M.uid('search', None, "ALL")

    uids = data[0].split()
    
    new_entries = []
    current_max_uid = last_uid

    if uids:
        for uid_bytes in uids:
            uid = int(uid_bytes)
            if uid <= last_uid: continue
            
            current_max_uid = max(current_max_uid, uid)
            
            rv, data = M.uid('fetch', uid_bytes, '(RFC822)')
            if rv != 'OK': continue

            msg = email.message_from_bytes(data[0][1])
            subject = str(email.header.make_header(email.header.decode_header(msg['Subject'])))
            
            body, current_ctype = "", None
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if current_ctype is None or ctype == 'text/html':
                        body = (part.get_payload(decode=True) or b'').decode('utf-8', errors='backslashreplace')
            else:
                body = (msg.get_payload(decode=True) or b'').decode('utf-8', errors='backslashreplace')

            id_ = re.sub('[^0-9a-zA-Z]+', '_', unidecode(subject))
            file_name = f'{id_}.html'
            with open(os.path.join(OUT_PATH, file_name), 'w') as fp:
                fp.write(body)

            date_obj = parse(msg['Date'])
            new_entries.append({
                'date': date_obj.isoformat(),
                'title': id_,
                'link': f'{BASE_URL}/{file_name}',
                'description': remove_control_characters(body.strip())
            })
        
        print(f"Processed {len(new_entries)} new emails.")
    else:
        print("No new emails.")

    M.close()
    M.logout()

    all_entries = existing_entries + new_entries
    save_state(current_max_uid, all_entries)
    generate_feed(all_entries)

# --- Entry Point ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Email to RSS with Manual Link Support")
    parser.add_argument('--add', metavar='URL', help='Add a manual link to the RSS feed')
    
    args = parser.parse_args()

    if args.add:
        add_manual_link(args.add)
    else:
        fetch_emails()