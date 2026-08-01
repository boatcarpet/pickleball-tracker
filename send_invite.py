"""
Friday Pickleball emails.

Two sends, decided automatically by the day it runs:
  - MONDAY   -> sign-up nudge to everyone on the list who has an email
  - THURSDAY -> a CONFIRMATION roster (who's playing, who's staying for dinner),
                sent to everyone marked IN or MAYBE who has an email

MODE can be forced from the workflow's "Run workflow" button
(auto / monday / thursday) for testing on demand.
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
    mode = "thursday" if today.weekday() == 3 else "monday"

# --- This week's upcoming Friday ---
friday = today + datetime.timedelta(days=(4 - today.weekday()) % 7)
when = friday.strftime("%A, %B %-d")

# --- Pull the list ---
players = db.reference("pickleball/players").get() or {}
contacts = db.reference("pickleball/contacts").get() or {}

# --- Who to email ---
recipients = []
for key, contact in contacts.items():
    if key not in players:
        continue
    person = players[key] if isinstance(players[key], dict) else {}
    email = (contact or {}).get("email", "").strip() if isinstance(contact, dict) else ""
    if not email:
        continue
    if mode == "thursday" and person.get("status") not in ("in", "maybe"):
        continue
    recipients.append((person.get("name", ""), email))

# Extra people who get the Thursday roster only (not players; here for the dinner headcount).
# Add more lines here if needed.
if mode == "thursday":
    recipients.append(("Mark", "mking@bacmi.net"))

# de-duplicate by email
seen = set()
recipients = [(n, e) for (n, e) in recipients if not (e.lower() in seen or seen.add(e.lower()))]

if not recipients:
    print(f"[{mode}] No matching emails. Nothing to send.")
    raise SystemExit(0)

# --- Message wording per send ---
if mode == "thursday":
    # Roster built from everyone's current choices (dinner is independent of playing).
    in_players, maybe_players, dinner_only = [], [], []
    for person in players.values():
        if not isinstance(person, dict):
            continue
        st = person.get("status")
        nm = person.get("name", "")
        dn = bool(person.get("dinner"))
        if st == "in":
            in_players.append((nm, dn))
        elif st == "maybe":
            maybe_players.append((nm, dn))
        if dn and st not in ("in", "maybe"):
            dinner_only.append(nm)  # staying for dinner but not playing
    in_players.sort(key=lambda x: x[0].lower())
    maybe_players.sort(key=lambda x: x[0].lower())
    dinner_only.sort(key=lambda s: s.lower())

    dinner_playing = sum(1 for _, d in in_players if d) + sum(1 for _, d in maybe_players if d)
    dinner_only_count = len(dinner_only)
    dinner_total = dinner_playing + dinner_only_count

    lines = []
    lines.append(f"Here's who's confirmed for pickleball this Friday ({when}) at 6pm.")
    lines.append("Play first, then drinks and dinner for anyone who wants to stay.")
    lines.append("")
    lines.append(f"PLAYING ({len(in_players)}):")
    for nm, dn in in_players:
        lines.append(f"  {nm}" + ("   (staying for dinner)" if dn else ""))
    if maybe_players:
        lines.append("")
        lines.append(f"MAYBE ({len(maybe_players)}):")
        for nm, dn in maybe_players:
            lines.append(f"  {nm}" + ("   (staying for dinner)" if dn else ""))
    if dinner_only:
        lines.append("")
        lines.append(f"DINNER ONLY ({len(dinner_only)}):")
        for nm in dinner_only:
            lines.append(f"  {nm}")
    lines.append("")
    lines.append("Staying for dinner:")
    lines.append(f"  Playing: {dinner_playing}")
    lines.append(f"  Dinner only: {dinner_only_count}")
    lines.append(f"  Total: {dinner_total}")
    lines.append("")
    lines.append(f"Need to change your reply? {TRACKER_URL}")
    lines.append("")
    lines.append("See you Friday!")

    subject = f"Friday 6pm Pickleball - confirmed players ({when})"
    body = "\n".join(lines) + "\n"
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
