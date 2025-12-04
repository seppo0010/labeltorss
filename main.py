#!/usr/bin/env python
#
# Basic example of using Python3 and IMAP to read emails in a gmail folder/label.
# Now supports incremental fetching via UID storage.
import os
import sys
import imaplib
import email
import re
import json
from unidecode import unidecode
import unicodedata

from dateutil.parser import parse
from feedgen.feed import FeedGenerator

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

def remove_control_characters(s):
    return "".join(ch for ch in str(s) if unicodedata.category(ch)[0]!="C")

def load_state():
    """Loads the last seen UID and previous entries to preserve RSS history."""
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

def process_mailbox(M):
    last_uid, existing_entries = load_state()
    
    print(f"Checking for new emails (Last UID: {last_uid})...")

    # Determine search criteria based on last_uid
    if last_uid > 0:
        # Search for UIDs greater than the last one seen
        search_crit = f'{last_uid + 1}:*'
        rv, data = M.uid('search', None, search_crit)
    else:
        # First run, fetch all
        rv, data = M.uid('search', None, "ALL")

    if rv != 'OK':
        print("No messages found!")
        return

    # If data[0] is empty (b''), no new mails found
    uids = data[0].split()
    if not uids:
        print("No new emails to process.")
        # We still regenerate the RSS to be safe, or just exit. 
        # But we need to pass existing_entries to feed gen if we want to update checking logic.
        # For now, let's just proceed to ensure RSS is generated if it was missing.
    
    new_entries = []
    current_max_uid = last_uid

    for uid_bytes in uids:
        uid = int(uid_bytes)
        
        # In case the range query includes the old UID (depending on server implementation), skip it
        if uid <= last_uid:
            continue
            
        current_max_uid = max(current_max_uid, uid)
        
        rv, data = M.uid('fetch', uid_bytes, '(RFC822)')
        if rv != 'OK':
            print("ERROR getting message", uid)
            continue

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

        # Create the ID/Filename
        id_ = re.sub('[^0-9a-zA-Z]+', '_', unidecode(subject))
        
        # Write the HTML file (only for new emails)
        file_name = f'{id_}.html'
        with open(os.path.join(OUT_PATH, file_name), 'w') as fp:
            fp.write(body)

        # Append to our list of entries
        # Store date as ISO string for JSON serialization
        date_obj = parse(msg['Date'])
        
        new_entries.append({
            'date': date_obj.isoformat(),
            'title': id_,
            'link': f'{BASE_URL}/{file_name}',
            'description': remove_control_characters(body.strip())
        })

    print(f"Processed {len(new_entries)} new emails.")

    # Combine old and new entries
    all_entries = existing_entries + new_entries
    
    # Sort by date descending
    all_entries = sorted(all_entries, key=lambda x: parse(x['date']), reverse=True)

    # Generate Feed
    fg = FeedGenerator()
    fg.id(f'{BASE_URL}/rss.xml')
    fg.title('My Newsletters')
    fg.description('My Newsletters')
    fg.link(href=f'{BASE_URL}/rss.xml')

    for entry in all_entries:
        fe = fg.add_entry()
        fe.id(entry['title'])
        fe.title(entry['title'])
        # Parse the ISO string back to datetime object for feedgen
        fe.updated(parse(entry['date']))
        fe.link(href=entry['link'], rel='self')
        fe.description(entry['description'])
        fe.summary(entry['description'], type='html')

    fg.atom_file(os.path.join(OUT_PATH, 'rss.xml'))
    
    # Save the new state
    save_state(current_max_uid, all_entries)
    print(f"State saved. New High-Water Mark UID: {current_max_uid}")

# --- Main Execution ---

M = imaplib.IMAP4_SSL(IMAP_HOST)

try:
    rv, data = M.login(EMAIL_ACCOUNT, IMAP_PASSWORD)
except imaplib.IMAP4.error:
    print ("LOGIN FAILED!!! ")
    sys.exit(1)

rv, data = M.select(EMAIL_FOLDER)
if rv == 'OK':
    process_mailbox(M)
    M.close()
else:
    print("ERROR: Unable to open mailbox ", rv)
M.logout()