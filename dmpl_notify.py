"""
DMPL Flight 2 match emails (page: boatcarpet.github.io/dmpl).

Runs every 15 minutes. Each run reads the scheduler data in Firebase and
sends only what is new, stamping each send under dmpl/sent so nothing goes
out twice:

MORNING 8AM     -> once a day, each team gets one email listing the opponents
                   they still need to play who added new times since the last
                   morning email (all their open times, new ones marked, and
                   their note). Every time is a link that opens the page with
                   the reader's team picked and that match ready to book.
                   Nothing new for a team = no email for that team.
MATCH BOOKED    -> when either team books (or moves) a match: both teams.
MATCH CANCELLED -> when either team cancels a booked match: both teams.
DAY BEFORE      -> from 9am the day before a booked match: both teams.
MONDAY 8AM      -> replaces that morning's email: each team gets a reminder to mark times, with the open
                   times of every team they still need to play (as links)
                   and their upcoming booked matches.

Player emails come from the DMPL_ROSTER secret (never stored on the public
page or in this repo). Kevin gets a short receipt when anything is sent.

PREVIEW=true sends everything to Kevin only and changes nothing.
"""
import os
import re
import json
import html
import smtplib
import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

DATABASE_URL = "https://wednesday-tennis-tracker-default-rtdb.firebaseio.com"
PAGE_URL = "https://boatcarpet.github.io/dmpl/"
ADMIN_EMAIL = "sales@boatcarpet.com"
MICHIGAN = ZoneInfo("America/New_York")
REMINDER_HOUR = 9   # 9:00 AM the day before a match
MORNING_HOUR = 8    # daily times email, and the Monday check-in
MAX_TIMES = 15      # most times listed per team in one email

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
PLAYED = {"AJ-CHP", "JK-CHP"}   # must match PLAYED in the page
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
    return f"{t.strftime('%a %b')} {t.day} at {t.strftime('%I').lstrip('0')}:{t.strftime('%M %p')}"


# ---------------------------------------------------------------- schedule helpers

def opp_of(m, team):
    return m["a"] if m["h"] == team else m["h"]

def match_between(a, b):
    for m in MATCHES:
        if {m["h"], m["a"]} == {a, b}:
            return m
    return None

def booked_times(matches, team):
    out = set()
    for mid, b in (matches or {}).items():
        h, a = mid.split("-")
        if team in (h, a) and isinstance(b, dict) and b.get("when"):
            out.add(b["when"])
    return out

def open_times(avail, matches, team, now):
    """Future times a team marked, minus times it already has a match."""
    taken = booked_times(matches, team)
    return sorted(k for k in ((avail or {}).get(team) or {})
                  if slot_time(k) > now and k not in taken)

def unscheduled_opponents(matches, team):
    """Teams this team still has to play with nothing booked."""
    out = []
    for m in MATCHES:
        if team in (m["h"], m["a"]) and not (isinstance((matches or {}).get(m["id"]), dict)
                                             and matches[m["id"]].get("when")):
            out.append(opp_of(m, team))
    return out

def book_link(reader, m, key):
    return f"{PAGE_URL}#book-{reader}-{m['id']}-{key}"

def team_link(team):
    return f"{PAGE_URL}#team-{team}"


# ---------------------------------------------------------------- email building
# Each email is built as (text, html) together from simple pieces.

class Body:
    def __init__(self):
        self.t, self.h = [], []

    def p(self, text):
        self.t += [text, ""]
        self.h.append(f"<p>{html.escape(text)}</p>")

    def raw(self, text_line, html_part):
        self.t.append(text_line)
        self.h.append(html_part)

    def times(self, reader, m, keys, new=()):
        if not keys:
            return
        shown = keys[:MAX_TIMES]
        items = []
        for k in shown:
            tag = "  (new)" if k in new else ""
            self.t.append(f"  - {nice(k)}{tag}: {book_link(reader, m, k)}")
            items.append(f'<li><a href="{book_link(reader, m, k)}">{html.escape(nice(k))}</a>'
                         f'{" <b>new</b>" if k in new else ""}</li>')
        if len(keys) > len(shown):
            self.t.append(f"  ...and {len(keys) - len(shown)} more on the page")
            items.append(f"<li>...and {len(keys) - len(shown)} more on the page</li>")
        self.t.append("")
        self.h.append("<ul>" + "".join(items) + "</ul>")

    def note(self, team, text):
        if text:
            self.t += [f"  {TEAMS[team]} note: {text}", ""]
            self.h.append(f'<p style="margin:4px 0 12px;padding:6px 10px;border-left:3px solid #f2a900;background:#fff3d6">'
                          f"<b>{html.escape(TEAMS[team])} note:</b> {html.escape(text)}</p>")

    def done(self):
        text = "\n".join(self.t).rstrip() + "\n"
        page = ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.45;color:#18232e">'
                + "".join(self.h) + "</div>")
        return text, page


def roster_emails(roster, team):
    return [e.strip() for e in roster.get(team) or [] if EMAIL_OK.match((e or "").strip())]

def heading(m):
    return f"{TEAMS[m['a']]} at {TEAMS[m['h']]}"


def morning_email(reader, posters, avail, matches, notes, new_by_team, now):
    names = [TEAMS[p] for p in posters]
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    subject = f"DMPL Flight 2: new times from {who}"
    b = Body()
    b.p(f"Good morning {TEAMS[reader]}. Teams you still need to play added times they can play. "
        "Tap a time to open the scheduler and book it:")
    for p in posters:
        m = match_between(reader, p)
        keys = [k for k in open_times(avail, matches, p, now) if k not in booked_times(matches, reader)]
        b.raw(f"{TEAMS[p]} ({'home' if m['h'] == p else 'away'}):",
              f"<p style='margin:10px 0 2px'><b>{html.escape(TEAMS[p])}</b> ({'home' if m['h'] == p else 'away'})</p>")
        b.times(reader, m, keys, new_by_team.get(p, set()))
        b.note(p, (notes or {}).get(p, "").strip())
    b.p(f"None of these work? Mark your own times so they can see them: {team_link(reader)}")
    b.p("The home team reserves the court once a match is booked.")
    return subject, *b.done()


def booked_email(m, when, moved, by):
    home, away = TEAMS[m["h"]], TEAMS[m["a"]]
    subject = f"DMPL Flight 2: match {'moved' if moved else 'booked'} - {away} at {home}, {nice(when)}"
    b = Body()
    b.p(f"Your Flight 2 match is {'now moved to' if moved else 'booked for'}:")
    b.raw(f"  {away} at {home}\n  {nice(when)}\n",
          f"<p style='font-size:17px'><b>{html.escape(away)} at {html.escape(home)}</b><br>{html.escape(nice(when))}</p>")
    if by in TEAMS:
        b.p(f"Booked by {TEAMS[by]}.")
    b.p(f"{home} (home team): please reserve a court.")
    b.p(f"Need to change it? Either team can cancel it on the page (tap the booked slot) and pick another time: {PAGE_URL}")
    b.p("A reminder goes out the day before. Enter the score on Tenniscores after you play.")
    return subject, *b.done()

def cancelled_email(m, when):
    home, away = TEAMS[m["h"]], TEAMS[m["a"]]
    subject = f"DMPL Flight 2: match cancelled - {away} at {home}, {nice(when)}"
    b = Body()
    b.p(f"The Flight 2 match {away} at {home} on {nice(when)} was cancelled on the scheduler.")
    b.p(f"{home}: if you reserved a court, release it.")
    b.p(f"Pick a new time here: {PAGE_URL}")
    return subject, *b.done()

def reminder_email(m, when):
    home, away = TEAMS[m["h"]], TEAMS[m["a"]]
    subject = f"Reminder: DMPL Flight 2 tomorrow - {away} at {home}, {nice(when)}"
    b = Body()
    b.p("Reminder: your Flight 2 match is tomorrow.")
    b.raw(f"  {away} at {home}\n  {nice(when)}\n",
          f"<p style='font-size:17px'><b>{html.escape(away)} at {html.escape(home)}</b><br>{html.escape(nice(when))}</p>")
    b.p(f"{home}: double-check the court is reserved. Enter the score on Tenniscores after you play.")
    return subject, *b.done()

def monday_email(team, avail, matches, notes, now):
    subject = f"DMPL Flight 2: Monday check-in for {TEAMS[team]}"
    b = Body()
    upcoming = sorted((b_["when"], mid) for mid, b_ in (matches or {}).items()
                      if isinstance(b_, dict) and b_.get("when") and team in mid.split("-")
                      and slot_time(b_["when"]) > now and mid not in PLAYED)
    todo = unscheduled_opponents(matches, team)
    b.p(f"Good morning {TEAMS[team]}. Please mark the times your team can play this week and next: {team_link(team)}")
    if upcoming:
        b.p("Your booked matches:")
        for when, mid in upcoming:
            h, a = mid.split("-")
            b.raw(f"  - {TEAMS[a]} at {TEAMS[h]}, {nice(when)}",
                  f"<div>&bull; {html.escape(TEAMS[a])} at {html.escape(TEAMS[h])}, <b>{html.escape(nice(when))}</b></div>")
        b.raw("", "<br>")
    if todo:
        b.p(f"Still to schedule ({len(todo)}). Tap a time to book it:")
        for o in todo:
            m = match_between(team, o)
            keys = [k for k in open_times(avail, matches, o, now) if k not in booked_times(matches, team)]
            b.raw(f"{TEAMS[o]} ({'home' if m['h'] == o else 'away'}):",
                  f"<p style='margin:10px 0 2px'><b>{html.escape(TEAMS[o])}</b> ({'home' if m['h'] == o else 'away'})</p>")
            if keys:
                b.times(team, m, keys)
            else:
                b.raw("  hasn't marked any times yet\n", "<p style='margin:0 0 6px;color:#5d6b78'>hasn't marked any times yet</p>")
            b.note(o, (notes or {}).get(o, "").strip())
    else:
        b.p("All of your matches are scheduled or played.")
    return subject, *b.done()


# ---------------------------------------------------------------- the run

def run(now, preview, store, mailer, roster, log=print):
    avail = store.get("dmpl/avail") or {}
    matches = store.get("dmpl/matches") or {}
    notes = store.get("dmpl/notes") or {}
    sent = store.get("dmpl/sent") or {}
    s_book = dict(sent.get("booked") or {})
    s_rem = dict(sent.get("reminder") or {})
    told = dict(sent.get("told") or {})

    outbox = []   # (teams_to_email, label, subject, text, html, stamp_keys)
    stamps = {}
    if "overlap" in sent:
        stamps["overlap"] = None   # old email type, no longer used

    # ---- bookings, cancellations, day-before reminders ----
    for m in MATCHES:
        mid = m["id"]
        b = matches.get(mid) if isinstance(matches.get(mid), dict) else None
        when = b.get("when") if b else None
        if when:
            if s_book.get(mid) != when:
                outbox.append(([m["h"], m["a"]], f"booked {heading(m)}, {nice(when)}",
                               *booked_email(m, when, bool(s_book.get(mid)), b.get("by")), [f"booked/{mid}"]))
                stamps[f"booked/{mid}"] = when
                rem_at = (slot_time(when) - datetime.timedelta(days=1)).replace(hour=REMINDER_HOUR, minute=0)
                if now >= rem_at:
                    stamps[f"reminder/{mid}"] = when   # booked late: no separate reminder
            elif s_rem.get(mid) != when:
                t = slot_time(when)
                rem_at = (t - datetime.timedelta(days=1)).replace(hour=REMINDER_HOUR, minute=0)
                if rem_at <= now < t:
                    outbox.append(([m["h"], m["a"]], f"reminder {heading(m)}, {nice(when)}",
                                   *reminder_email(m, when), [f"reminder/{mid}"]))
                    stamps[f"reminder/{mid}"] = when
        elif s_book.get(mid):
            old = s_book[mid]
            if slot_time(old) > now:
                outbox.append(([m["h"], m["a"]], f"cancelled {heading(m)}, {nice(old)}",
                               *cancelled_email(m, old), [f"booked/{mid}", f"reminder/{mid}"]))
            stamps[f"booked/{mid}"] = None
            stamps[f"reminder/{mid}"] = None

    # ---- 8 AM: Monday check-in, or the daily "new times" email ----
    today = now.date().isoformat()
    if not sent.get("morning"):
        stamps["morning"] = today   # first run after setup: start with tomorrow's 8 AM email
    elif now.hour >= MORNING_HOUR and sent.get("morning") != today:
        cur = {t: open_times(avail, matches, t, now) for t in TEAMS}
        new_by_team = {t: {k for k in cur[t] if k not in (told.get(t) or {})} for t in TEAMS}
        if now.weekday() == 0:
            for team in TEAMS:
                outbox.append(([team], f"monday {TEAMS[team]}",
                               *monday_email(team, avail, matches, notes, now), []))
        else:
            for reader in TEAMS:
                posters = [p for p in unscheduled_opponents(matches, reader)
                           if new_by_team[p] and [k for k in cur[p] if k not in booked_times(matches, reader)]]
                if posters:
                    outbox.append(([reader], f"new times for {TEAMS[reader]} from "
                                   + ", ".join(TEAMS[p] for p in posters),
                                   *morning_email(reader, posters, avail, matches, notes, new_by_team, now), []))
        stamps["morning"] = today
        for t in TEAMS:   # everything open now has been told
            stamps[f"told/{t}"] = {k: True for k in cur[t]} or None
    if "snap" in sent:
        stamps["snap"] = None      # no longer used
    if "monday" in sent:
        stamps["monday"] = None    # replaced by "morning"

    if not outbox:
        if stamps and not preview:
            store.update("dmpl/sent", stamps)
        log("Nothing new to send.")
        return []

    done, problems = [], []
    for teams, label, subject, text, page, keys in outbox:
        to = [e for t in teams for e in roster_emails(roster, t)]
        if preview:
            why = mailer.send([ADMIN_EMAIL], "[PREVIEW] " + subject,
                              f"PREVIEW ONLY - would go to: {', '.join(to) or '(no addresses)'}\n\n----------\n\n" + text,
                              page)
        elif not to:
            why = "no addresses"
        else:
            why = mailer.send(to, subject, text, page)
        if why:
            problems.append(f"{label}: {why}")
            log(f"NOT SENT {label} - {why}")
            for k in keys:            # leave the stamp off so the next run tries again
                stamps.pop(k, None)
        else:
            done.append(label)
            log(f"Sent {label} to {len(to)}")

    if preview:
        log("Preview only - nothing stamped.")
        return done

    if stamps:
        store.update("dmpl/sent", stamps)

    # Short receipt for Kevin: one line per thing that happened.
    posters, monday, lines = {}, 0, []
    for label in done:
        if label.startswith("new times for "):
            for name in label.split(" from ", 1)[1].split(", "):
                posters[name] = posters.get(name, 0) + 1
        elif label.startswith("monday "):
            monday += 1
        elif label.startswith("booked "):
            lines.append("Booked: " + label[7:])
        elif label.startswith("cancelled "):
            lines.append("Cancelled: " + label[10:])
        elif label.startswith("reminder "):
            lines.append("Reminder sent: " + label[9:])
    for name, n in posters.items():
        lines.append(f"{name}'s new times went to {n} team{'s' if n != 1 else ''}.")
    if monday:
        lines.append(f"Monday check-in went to {monday} teams.")
    if problems:
        lines += ["", "Not sent:"] + [f"  {p}" for p in problems]
    subject = "[dmpl] " + (lines[0] if len(lines) == 1 else "Flight 2 update")
    mailer.send([ADMIN_EMAIL], subject, "\n".join(lines) + "\n\n" + PAGE_URL + "\n")
    return done


# ---------------------------------------------------------------- real run

class Mailer:
    def __init__(self, user, password):
        self.user, self.password, self.server = user, password, None

    def send(self, to, subject, text, page=None):
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.user
        msg["To"] = ", ".join(to)
        msg.set_content(text)
        if page:
            msg.add_alternative(page, subtype="html")
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
