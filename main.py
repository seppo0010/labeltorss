#!/usr/bin/env python
#
# Basic example of using Python3 and IMAP to read emails in a gmail folder/label.
# Remove legacy email.header api use.
import os
import sys
import imaplib
import email
import datetime
import re
import unicodedata

from dateutil.parser import parse
from feedgen.feed import FeedGenerator

IMAP_HOST = 'imap.gmail.com'
IMAP_PASSWORD = os.getenv('IMAP_PASSWORD')
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_FOLDER = os.getenv('EMAIL_FOLDER')
OUT_PATH = os.getenv('OUT_PATH')
BASE_URL = os.getenv('BASE_URL')

try:
    os.makedirs(OUT_PATH)
except FileExistsError:
    pass

def remove_control_characters(s):
    return "".join(ch for ch in str(s) if unicodedata.category(ch)[0]!="C")

def process_mailbox(M):
    rv, data = M.search(None, "ALL")
    if rv != 'OK':
        print("No messages found!")
        return

    mails = []
    for i, num in enumerate(data[0].split()[::-1]):
        rv, data = M.fetch(num, '(RFC822)')
        if rv != 'OK':
            print("ERROR getting message", num)
            return

        msg = email.message_from_bytes(data[0][1])
        subject = str(email.header.make_header(email.header.decode_header(msg['Subject'])))
        body, current_ctype = "", None

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()

                if current_ctype is None or ctype == 'text/html':
                    body = (part.get_payload(decode=True) or b'').decode('utf-8')
        else:
            body = (msg.get_payload(decode=True) or b'').decode('utf-8')

        mails.append((parse(msg['Date']), subject, body))
        if i == 20: break

    fg = FeedGenerator()
    fg.id(f'{BASE_URL}/rss.xml')
    fg.title('My Newsletters')
    fg.description('My Newsletters')
    fg.link(href=f'{BASE_URL}/rss.xml')

    for mail in mails[::-1]:
        fe = fg.add_entry()
        id_ = re.sub('[^0-9a-zA-Z]+', '_', mail[1])
        fe.id(id_)
        fe.updated(mail[0])
        fe.title(id_)
        fe.description(remove_control_characters(mail[2].strip()))
        fe.summary(remove_control_characters(mail[2].strip()), type='html')
        fe.link(href=f'{BASE_URL}/{id_}.html', rel='self')
        with open(os.path.join(OUT_PATH, f'{id_}.html'), 'w') as fp:
            fp.write(mail[2])
    fg.atom_file(os.path.join(OUT_PATH, 'rss.xml'))



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
