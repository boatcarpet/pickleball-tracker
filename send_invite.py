"""
Weekly Friday Pickleball reminder.
Reads the current email list from Firebase and emails each person the invite.
Runs from a GitHub Action every Monday morning (and on-demand for testing).
"""
import os
import json
import smtplib
import datetime
from email.message import EmailMessage

import firebase_admin
from firebase_admin import credentials, db

DATABASE_URL = "https://wednesday-tennis-tracker-default-rtdb.firebaseio.com"
TRACKER_URL = "https://boatcarpet.github.io/pickleball-tracker/"

# --- Connect to Firebase with the service account (bypasses public rules) ---
service_account = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
cred = credentials.Certificate(service_account)
firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})

# --- Gather who to email: anyone on the list with an email on file ---
players = db.reference("pickleball/players").get() or {}
contacts = db.reference("pickleball/contacts").get() or {}

recipients = []
for key, contact in contacts.items():
    if key not in players:
        continue  # skip anyone who was removed from the list
    email = (contact or {}).get("email", "").strip() if isinstance(contact, dict) else ""
    if email:
        name = players[key].get("name", "") if isinstance(players[key], dict) else ""
        recipients.append((name, email))

# de-duplicate by email
seen = set()
recipients = [(n, e) for (n, e) in recipients if not (e.lower() in seen or seen.add(e.lower()))]

if not recipients:
    print("No emails on file. Nothing to send.")
    raise SystemExit(0)

# --- This Friday's date ---
today = datetime.date.today()
friday = today + datetime.timedelta(days=(4 - today.weekday()) % 7)
when = friday.strftime("%A, %B %-d")

subject = f"Pickleball Friday {when} - 6pm"
body = f"""Pickleball this Friday ({when}) at 6pm.
Play first, then drinks and dinner for anyone who wants to stay.

Tap the link, add your name, say IN / MAYBE / OUT:
{TRACKER_URL}

You're getting this because you're on the list. Want off the list, or prefer a text instead? Just reply and let me know.
"""

# --- Send one message per person (so nobody sees anyone else's address) ---
user = os.environ["SMTP_USER"]
password = os.environ["SMTP_PASS"]

sent = 0
with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(user, password)
    for name, email in recipients:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = email
        msg.set_content(body)
        server.send_message(msg)
        sent += 1
        print(f"Sent to {name or '(no name)'} <{email}>")

print(f"Done. {sent} email(s) sent for {when}.")
