"""
DMPL Flight 2 match emails (page: boatcarpet.github.io/dmpl).

Runs every 15 minutes. Each run reads the scheduler data in Firebase and
sends only what is new, stamping each send under dmpl/sent so nothing goes
out twice:

TIMES LINE UP -> the first time two teams have a time in common, one email to
                 every player on both teams with the common times. The home
                 team books it on the page and reserves a court.
MATCH BOOKED  -> when the home team taps Book (or moves a booking to a new
                 time), one email to every player on both teams.
DAY BEFORE    -> from 9am the day before a booked match, a reminder to both
                 teams. Skipped if the match was booked after that point.

Each email goes to both teams together so they can reply-all to sort out
the court. Player emails come from the DMPL_ROSTER secret (never stored on
the public page or in this repo). Kevin gets a short receipt when anything
is sent.

MODE=auto runs normally. PREVIEW=true emails Kevin only and changes nothing.
"""
import os
import re
import json
import smtplib
import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

DATABASE_URL = "https://wednesday-tennis-tracker-default-rtdb.firebaseio.com"
PAGE_URL = "https://boatcarpet.github.io/dmpl/"
ADMIN_EMAIL = "kevin@meadedistributing.com"
MICHIGAN = ZoneInfo("America/New_York")
REMINDER_HOUR = 9  # 9:00 AM the day before

TEAMS = {
    "AJ": "Arnaud/Jess", "BD": "Bryan/Dan", "CHP": "Curry/Heil/Peterson",
    "DCP": "David/Chris/Patrick", "DS": "David/Scott", "JK": "Jim/Kevin",
    "ND": "Nick/Drew", "RRM": "Ryan/Ryan/Michael", "WRS": "Wade/Rick/Sandeep",
}
# [home, away] - must match PAIRS in the page
PAIRS = [["AJ","CHP"],["AJ","BD"],["ND","AJ"],["DS","AJ"],["AJ","DCP"],["RRM","AJ"],["AJ","JK"],["WRS","AJ"],
 ["BD","DS"],["DCP","BD"],["BD","RRM"],["JK","BD"],["BD","WRS"],["ND","BD"],["BD","CHP"],
 ["CHP","ND"],["CHP","DS"],["DCP","CHP"],["CHP","RRM"],["JK","CHP"],["CHP","WRS"],
 ["DCP","JK"],["WRS","DCP"],["ND","DCP"],["DCP","DS"],["RRM","DCP"],
 ["DS","WRS"],["DS","ND"],["DS","RRM"],["JK","DS"],
 ["JK","RRM"],["WRS","JK"],["ND","JK"],
 ["RRM","ND"],["WRS","ND"],["RRM","WRS"]]
PLAYED = {"AJ-CHP"}
MATCHES = [{"id": f"{h}-{a}", "h": h, "a": a} for h, a in PAIRS if f"{h}-{a}" not in PLAYED]

EMAIL_OK = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


# ---------------------------------------------------------------- times

def slot_time(key):
    """'2026-11-17T19-00' -> aware datetime in Michigan time."""
    d, t = key.split("T")
    y, mo, da = (int(x) for x in d.split("-"))
    h, mi = (int(x) for x in t.split("-"))
    return datetime.datetime(y, mo, da, h, mi, tzinfo=MICHIGAN)

def nice(key):
    t = slot_time(key)
    hour = t.strftime("%I").lstrip("0")
    return f"{t.strftime('%a %b')} {t.day} at {hour}:{t.strftime('%M %p')}"


# ---------------------------------------------------------------- schedule logic (same rules as the page)

def avail_of(avail, team):
    return set((avail or {}).get(team) or {})

def booked_for(matches, team):
    out = {}
    for mid, b in (matches or {}).items():
        h, a = mid.split("-")
        if team in (h, a) and isinstance(b, dict) and b.get("when"):
            out[b["when"]] = mid
    return out

def overlap(m, avail, matches, now):
    bh, ba = booked_for(matches, m["h"]), booked_for(matches, m["a"])
    common = avail_of(avail, m["h"]) & avail_of(avail, m["a"])
    return sorted(k for k in common if slot_time(k) > now and k not in bh and k not in ba)


# ---------------------------------------------------------------- emails

def roster_emails(roster, team):
    out = []
    for e in roster.get(team) or []:
        e = (e or "").strip()
        if EMAIL_OK.match(e):
            out.append(e)
    return out

def heading(m):
    return f"{TEAMS[m['a']]} at {TEAMS[m['h']]}"

def overlap_email(m, times):
    home, away = TEAMS[m["h"]], TEAMS[m["a"]]
    listed = "\n".join(f"  - {nice(k)}" for k in times)
    subject = f"DMPL Flight 2: {away} at {home} - times that work for both teams"
    body = f"""{home} and {away}: you both marked these times as available on the Flight 2 scheduler:

{listed}

{home} is the home team. {home}, please pick one, tap Book on the page, and reserve a court:
{PAGE_URL}

Reply-all here if you need to work out which time or which club.

Everyone on both teams will get an email once the match is booked.
"""
    return subject, body

def booked_email(m, when, moved):
    home, away = TEAMS[m["h"]], TEAMS[m["a"]]
    subject = f"DMPL Flight 2: match {'moved' if moved else 'booked'} - {away} at {home}, {nice(when)}"
    body = f"""Your Flight 2 match is {'now moved to' if moved else 'booked for'}:

  {away} at {home}
  {nice(when)}

{home} (home team): please make sure a court is reserved.

Need to change it? The home team can tap Unbook on the page and pick another time:
{PAGE_URL}

A reminder goes out the day before. Enter the score on Tenniscores after you play.
"""
    return subject, body

def reminder_email(m, when):
    home, away = TEAMS[m["h"]], TEAMS[m["a"]]
    subject = f"Reminder: DMPL Flight 2 tomorrow - {away} at {home}, {nice(when)}"
    body = f"""Reminder: your Flight 2 match is tomorrow.

  {away} at {home}
  {nice(when)}

{home}: double-check the court is reserved.
Enter the score on Tenniscores after you play.

{PAGE_URL}
"""
    return subject, body


# ---------------------------------------------------------------- the run

def run(now, preview, store, mailer, roster, log=print):
    avail = store.get("dmpl/avail") or {}
    matches = store.get("dmpl/matches") or {}
    sent = store.get("dmpl/sent") or {}
    s_over = dict(sent.get("overlap") or {})
    s_book = dict(sent.get("booked") or {})
    s_rem = dict(sent.get("reminder") or {})

    outbox = []   # (match, kind, subject, body)
    stamps = {}   # path under dmpl/sent -> value (None deletes)

    for m in MATCHES:
        mid = m["id"]
        b = matches.get(mid) if isinstance(matches.get(mid), dict) else None
        when = b.get("when") if b else None

        if when:
            # MATCH BOOKED (new or moved)
            if s_book.get(mid) != when:
                outbox.append((m, "booked", *booked_email(m, when, moved=bool(s_book.get(mid)))))
                stamps[f"booked/{mid}"] = when
                # booked late (after the reminder point): no separate reminder
                rem_at = (slot_time(when) - datetime.timedelta(days=1)).replace(hour=REMINDER_HOUR, minute=0)
                if now >= rem_at:
                    stamps[f"reminder/{mid}"] = when
            # DAY BEFORE
            elif s_rem.get(mid) != when:
                t = slot_time(when)
                rem_at = (t - datetime.timedelta(days=1)).replace(hour=REMINDER_HOUR, minute=0)
                if rem_at <= now < t:
                    outbox.append((m, "reminder", *reminder_email(m, when)))
                    stamps[f"reminder/{mid}"] = when
        else:
            if s_book.get(mid):
                # unbooked: allow fresh emails when they line up / book again
                stamps[f"booked/{mid}"] = None
                stamps[f"reminder/{mid}"] = None
                stamps[f"overlap/{mid}"] = None
                s_over.pop(mid, None)
            # TIMES LINE UP (first time only)
            times = overlap(m, avail, matches, now)
            if times and not s_over.get(mid):
                outbox.append((m, "overlap", *overlap_email(m, times)))
                stamps[f"overlap/{mid}"] = now.isoformat(timespec="minutes")

    if not outbox:
        if stamps and not preview:
            store.update("dmpl/sent", stamps)
        log("Nothing new to send.")
        return []

    done, problems = [], []
    for m, kind, subject, body in outbox:
        to = roster_emails(roster, m["h"]) + roster_emails(roster, m["a"])
        if preview:
            why = mailer.send([ADMIN_EMAIL], "[PREVIEW] " + subject,
                              f"PREVIEW ONLY - would go to: {', '.join(to) or '(no addresses)'}\n\n----------\n\n{body}")
        elif not to:
            why = "no addresses for either team"
        else:
            why = mailer.send(to, subject, body)
        if why:
            problems.append(f"{heading(m)} ({kind}): {why}")
            log(f"NOT SENT {kind} {m['id']} - {why}")
            # leave the stamp off so the next run tries again
            for k in [k for k in stamps if k.endswith("/" + m["id"])]:
                stamps.pop(k)
        else:
            done.append(f"{kind:9} {heading(m)}  ({len(to)} players)")
            log(f"Sent {kind} {m['id']} to {len(to)}")

    if preview:
        log("Preview only - nothing stamped.")
        return done

    if stamps:
        store.update("dmpl/sent", stamps)

    lines = ["Flight 2 scheduler emails sent:", ""] + [f"  {d}" for d in done]
    if problems:
        lines += ["", "Problems:"] + [f"  {p}" for p in problems]
    lines += ["", PAGE_URL]
    mailer.send([ADMIN_EMAIL], f"[dmpl] {len(done)} Flight 2 email(s) sent", "\n".join(lines) + "\n")
    return done


# ---------------------------------------------------------------- real run

class Mailer:
    def __init__(self, user, password):
        self.user, self.password, self.server = user, password, None

    def send(self, to, subject, body):
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.user
        msg["To"] = ", ".join(to)
        msg.set_content(body)
        for attempt in (1, 2):
            try:
                if self.server is None:
                    self.server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
                    self.server.login(self.user, self.password)
                refused = self.server.send_message(msg)
                return f"refused: {', '.join(refused)}" if refused else None
            except smtplib.SMTPServerDisconnected:
                self.server = None
                if attempt == 2:
                    return "connection dropped"
            except smtplib.SMTPAuthenticationError:
                raise
            except smtplib.SMTPException as err:
                return f"smtp error: {type(err).__name__}"

    def close(self):
        if self.server is not None:
            try:
                self.server.quit()
            except smtplib.SMTPException:
                pass


class FirebaseStore:
    def __init__(self):
        import firebase_admin
        from firebase_admin import credentials, db
        cred = credentials.Certificate(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]))
        firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})
        self.db = db

    def get(self, path):
        return self.db.reference(path).get()

    def update(self, path, values):
        self.db.reference(path).update(values)


if __name__ == "__main__":
    preview = os.environ.get("PREVIEW", "false").strip().lower() == "true"
    roster = json.loads(os.environ["DMPL_ROSTER"])
    mailer = Mailer(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
    try:
        run(datetime.datetime.now(MICHIGAN), preview, FirebaseStore(), mailer, roster)
    finally:
        mailer.close()
