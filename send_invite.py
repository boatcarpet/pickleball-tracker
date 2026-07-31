"""
Friday Pickleball reminders.

Two sends, decided automatically by the day it runs:
  - MONDAY   -> everyone on the list who has an email (the weekly sign-up nudge)
  - THURSDAY -> only people marked IN or MAYBE for this Friday, who have an email

Runs from a GitHub Action. MODE can be forced via the workflow's "Run workflow"
button (auto / monday / thursday) for testing on demand.
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

# --- Decide which send this is ---
today = datetime.date.today()
mode = os.environ.get("MODE", "auto").strip().lower()
if mode not in ("monday", "thursday"):
    # auto: Thursday (weekday 3) => reminder; anything else => the Monday sign-up send
    mode = "thursday" if today.weekday() == 3 else "monday"

# --- This week's upcoming Friday ---
friday = today + datetime.timedelta(days=(4 - today.weekday()) % 7)
when = friday.strftime("%A, %B %-d")

# --- Pull the list ---
players = db.reference("pickleball/players").get() or {}
contacts = db.reference("pickleball/contacts").get() or {}

recipients = []
for key, contact in contacts.items():
    if key not in players:
        continue  # skip anyone removed from the list
    person = players[key] if isinstance(players[key], dict) else {}
    email = (contact or {}).get("email", "").strip() if isinstance(contact, dict) else ""
    if not email:
        continue
    if mode == "thursday" and person.get("status") not in ("in", "maybe"):
        continue  # Thursday reminder is only for people who said IN or MAYBE
    recipients.append((person.get("name", ""), email))

# de-duplicate by email
seen = set()
recipients = [(n, e) for (n, e) in recipients if not (e.lower() in seen or seen.add(e.lower()))]

if not recipients:
    print(f"[{mode}] No matching emails. Nothing to send.")
    raise SystemExit(0)

# --- Message wording per send ---
if mode == "thursday":
    subject = f"Reminder: Pickleball this Friday {when} - 6pm"
    body = f"""Quick reminder \u2014 you're on the list for pickleball this Friday ({when}) at 6pm.
Play first, then drinks and dinner for anyone who wants to stay.

Need to change your reply (IN / MAYBE / OUT)? Tap the link:
{TRACKER_URL}

See you Friday!
"""
else:
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
        print(f"[{mode}] Sent to {name or '(no name)'} <{email}>")

print(f"Done. [{mode}] {sent} email(s) sent for {when}.")
