"""
Wednesday Paddle emails (7am Wednesday group).

Three jobs. The workflow runs this often; the script works out for itself,
in Michigan time, which job is due and stamps each one in Firebase so it is
never done twice and a late or skipped run is picked up by the next one.

  SUNDAY 2pm   -> clear last week's replies, open sign-up for this Wednesday,
                  email everyone on the list with the link.
  TUESDAY 2pm  -> email everyone the courts: full courts of 4 in the order
                  people said IN, then whoever is left over as "waiting",
                  with how many more players that court needs.
  AFTER THAT   -> every hour until 7am Wednesday, check whether anyone dropped
                  or joined. If a court changed, email only the players whose
                  court (or waiting group) is different now.

MODE (auto / sunday / tuesday / update) forces one job from the
"Run workflow" button. PREVIEW=true emails Kevin only and changes nothing.
"""
import os
import re
import json
import smtplib
import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

DATABASE_URL = "https://wednesday-tennis-tracker-default-rtdb.firebaseio.com"
TRACKER_URL = "https://boatcarpet.github.io/tennis-tracker/"

# Kevin is the sender, so the group email never really lands in his inbox -
# Gmail files a self-addressed copy into the Sent thread. He gets a send
# receipt here instead (with the email text in it). Previews go here too.
ADMIN_EMAIL = "kevin@meadedistributing.com"

MICHIGAN = ZoneInfo("America/New_York")
COURT_SIZE = 4
PLAY_HOUR = 7          # 7:00 AM Wednesday
SEND_HOUR = 14         # 2:00 PM for the Sunday and Tuesday emails
ROLLOVER_HOUR = 12     # at noon Wednesday the page points at next week

# No paddle before this date. Must match FIRST_WEDNESDAY in the page.
FIRST_WEDNESDAY = datetime.date(2026, 10, 21)

# A deliberately loose check: no spaces, exactly one @, a dot in the domain,
# plain ASCII only. Enough to catch typos and stray characters that make
# Gmail reject the address outright (555 5.5.2).
EMAIL_OK = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def clean_email(raw):
    """Return a tidy address, or None if it can't be used."""
    if not isinstance(raw, str):
        return None
    # Strip whitespace, zero-width characters, and smart quotes that phones add.
    e = raw.strip().strip("'\"‘’“”")
    e = e.replace("​", "").replace("﻿", "").replace(" ", "")
    if not e or not EMAIL_OK.match(e):
        return None
    return e


# ---------------------------------------------------------------- the week

def target_wednesday(now):
    """The Wednesday the page is showing. Same rule as the page."""
    today = now.date()
    ahead = (2 - today.weekday()) % 7          # Monday=0 ... Wednesday=2
    if ahead == 0 and now.hour >= ROLLOVER_HOUR:
        ahead = 7
    return max(today + datetime.timedelta(days=ahead), FIRST_WEDNESDAY)


def at(day, hour):
    return datetime.datetime(day.year, day.month, day.day, hour, 0, tzinfo=MICHIGAN)


def week_key(wed):
    return wed.strftime("%Y-%m-%d")            # must match weekKey() in the page


def nice(wed):
    return f"{wed.strftime('%A, %B')} {wed.day}"


def short(wed):
    return f"{wed.strftime('%B')} {wed.day}"


# ---------------------------------------------------------------- the courts

def lineup(players):
    """Courts of 4 in the order people said IN. Same rule as the page."""
    ins = []
    for key, p in players.items():
        if isinstance(p, dict) and p.get("status") == "in" and p.get("name"):
            ins.append((p.get("inAt") or 0, key, str(p["name"])))
    ins.sort(key=lambda t: (t[0], t[1]))
    order = [(key, name) for _, key, name in ins]
    full = len(order) // COURT_SIZE
    courts = [order[i * COURT_SIZE:(i + 1) * COURT_SIZE] for i in range(full)]
    waiting = order[full * COURT_SIZE:]
    need = COURT_SIZE - len(waiting) if waiting else 0
    return {"order": order, "courts": courts, "waiting": waiting, "need": need}


def groups(L):
    """For each player who is IN: which court (0 = waiting) and who with."""
    out = {}
    for i, court in enumerate(L["courts"], start=1):
        mates = sorted(k for k, _ in court)
        for key, name in court:
            out[key] = {"name": name, "court": i, "mates": mates}
    mates = sorted(k for k, _ in L["waiting"])
    for key, name in L["waiting"]:
        out[key] = {"name": name, "court": 0, "mates": mates}
    return out


def plural(n, word="player"):
    return f"{n} more {word}" + ("" if n == 1 else "s")


def courts_text(L):
    """The courts as plain text, numbered straight through in reply order."""
    lines = []
    n = 0
    for i, court in enumerate(L["courts"], start=1):
        lines.append(f"COURT {i}")
        for _, name in court:
            n += 1
            lines.append(f"  {n}. {name}")
        lines.append("")
    if L["waiting"]:
        nxt = len(L["courts"]) + 1
        lines.append(f"WAITING FOR COURT {nxt} - need {plural(L['need'])}")
        for _, name in L["waiting"]:
            n += 1
            lines.append(f"  {n}. {name}")
        lines.append("")
    return "\n".join(lines).rstrip()


def names_list(names):
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


# ---------------------------------------------------------------- the emails

def sunday_email(wed):
    when = nice(wed)
    subject = f"Wednesday Paddle {when} - 7am - sign-up is open"
    body = f"""Paddle this Wednesday ({short(wed)}) at 7am. Sign-up is open now.

Tap the link, find your name, and tap IN:
{TRACKER_URL}

Courts fill 4 at a time, in the order people say IN. A second email goes out Tuesday at 2pm with the courts.

If you say IN and then can't make it, tap OUT right away so the next person in line gets your spot.

You're getting this because you're on the Wednesday list. Want off the list? Tap the x next to your name on the page, or just reply and let me know.
"""
    return subject, body


def tuesday_email(wed, L):
    when = nice(wed)
    subject = f"Wednesday 7am Paddle - courts ({when})"
    lines = []
    count = len(L["order"])
    if count == 0:
        lines.append(f"No one has signed up for paddle this Wednesday ({short(wed)}) at 7am yet.")
        lines.append("")
        lines.append("If you want to play, tap IN here. It takes 4 to make a court:")
        lines.append(TRACKER_URL)
    else:
        if L["courts"]:
            lines.append(f"Here are the courts for paddle this Wednesday ({short(wed)}) at 7am.")
        else:
            lines.append(f"No court is set yet for paddle this Wednesday ({short(wed)}) at 7am.")
        lines.append("")
        lines.append(courts_text(L))
        lines.append("")
        if L["waiting"]:
            who = names_list(name for _, name in L["waiting"])
            verb = "is" if len(L["waiting"]) == 1 else "are"
            nxt = len(L["courts"]) + 1
            lines.append(f"{who} {verb} in, but Court {nxt} needs {plural(L['need'])} to go. "
                         f"Until it fills, {'that player does' if len(L['waiting']) == 1 else 'those players do'} not have a court.")
            lines.append("")
            lines.append("Not in yet? Tap IN here to fill that court:")
        else:
            lines.append("Not in yet? You can still tap IN here. It takes 4 more to add a court:")
        lines.append(TRACKER_URL)
        lines.append("")
        lines.append("If you're in and can't make it, tap OUT right away. The next person in line "
                     "moves up, and the players whose court changes get an email.")
    lines.append("")
    lines.append("See you Wednesday!")
    return subject, "\n".join(lines) + "\n"


def update_email(wed, L, me, dropped, added):
    """One player's court changed since the last email."""
    when = nice(wed)
    subject = f"Wednesday Paddle - court change ({when})"
    lines = [f"The courts for paddle this Wednesday ({short(wed)}) at 7am changed since the last email."]
    if dropped:
        lines.append(f"Dropped out: {names_list(dropped)}.")
    if added:
        lines.append(f"Added: {names_list(added)}.")
    lines.append("")
    if me["court"]:
        others = [n for k, n in L["courts"][me["court"] - 1] if k != me["key"]]
        lines.append(f"YOU ARE NOW ON COURT {me['court']} with {names_list(others)}.")
    else:
        nxt = len(L["courts"]) + 1
        lines.append(f"YOU ARE NOW WAITING for Court {nxt}. It needs {plural(L['need'])}. "
                     "Until it fills, you do not have a court.")
    lines.append("")
    lines.append("All the courts as they stand now:")
    lines.append("")
    lines.append(courts_text(L))
    lines.append("")
    lines.append("Can't make it? Tap OUT right away:")
    lines.append(TRACKER_URL)
    return subject, "\n".join(lines) + "\n"


# ---------------------------------------------------------------- sending

class Mailer:
    """One Gmail connection, one message per person (nobody sees other addresses)."""

    def __init__(self, user, password):
        self.user, self.password, self.server = user, password, None

    def _connect(self):
        self.server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        self.server.login(self.user, self.password)

    def send(self, to, subject, body):
        """Returns None if sent, or a short reason if not."""
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.user
        msg["To"] = to
        msg.set_content(body)
        for attempt in (1, 2):
            try:
                if self.server is None:
                    self._connect()
                self.server.send_message(msg)
                return None
            except smtplib.SMTPRecipientsRefused as err:
                return f"refused: {err}"       # one bad address never stops the rest
            except smtplib.SMTPServerDisconnected:
                self.server = None             # connection dropped - reconnect once
                if attempt == 2:
                    return "connection dropped"
            except smtplib.SMTPAuthenticationError:
                raise                          # nothing can be sent - fail the run so it retries
            except smtplib.SMTPException as err:
                return f"smtp error: {type(err).__name__}"

    def close(self):
        if self.server is not None:
            try:
                self.server.quit()
            except smtplib.SMTPException:
                pass
            self.server = None


def address_book(players, contacts):
    """{player key: (name, email)} for everyone on the list with a usable email."""
    book, skipped, seen = {}, [], set()
    for key, contact in (contacts or {}).items():
        person = players.get(key)
        if not isinstance(person, dict):
            continue                           # email left behind by a removed player
        name = str(person.get("name", ""))
        raw = contact.get("email", "") if isinstance(contact, dict) else ""
        if not (raw or "").strip():
            continue
        email = clean_email(raw)
        if not email:
            skipped.append((name, raw))
            continue
        if email.lower() in seen:
            continue                           # two names sharing one address: one email
        seen.add(email.lower())
        book[key] = (name, email)
    return book, skipped


def run(now, mode, preview, store, mailer, log=print):
    """
    store  - object with get(path) and update(path, dict)
    mailer - object with .user and .send(to, subject, body)
    Returns a short word for what was done (used by the tests).
    """
    wed = target_wednesday(now)
    wk = week_key(wed)
    when = nice(wed)

    players = store.get("wednesday/players") or {}
    contacts = store.get("wednesday/contacts") or {}
    meta = store.get("wednesday/meta") or {}
    players = {k: v for k, v in players.items() if isinstance(v, dict)}

    sunday_due = at(wed - datetime.timedelta(days=3), SEND_HOUR)
    tuesday_due = at(wed - datetime.timedelta(days=1), SEND_HOUR)
    play_time = at(wed, PLAY_HOUR)

    # ---- which job? ----
    if mode in ("sunday", "tuesday", "update"):
        job = mode
    elif now >= sunday_due and meta.get("sundaySent") != wk:
        job = "sunday"
    elif tuesday_due <= now < play_time and meta.get("tuesdaySent") != wk:
        job = "tuesday"
    elif tuesday_due <= now < play_time and meta.get("tuesdaySent") == wk:
        job = "update"
    else:
        log(f"[auto] Nothing due right now for {when} "
            f"(Sunday email {'sent' if meta.get('sundaySent') == wk else 'due ' + sunday_due.strftime('%a %b %d %I:%M %p')}, "
            f"Tuesday email {'sent' if meta.get('tuesdaySent') == wk else 'due ' + tuesday_due.strftime('%a %b %d %I:%M %p')}).")
        return "nothing"

    tag = f"[{job}{' preview' if preview else ''}]"
    book, skipped = address_book(players, contacts)
    for name, raw in skipped:
        log(f"{tag} Unusable address: {name or '(no name)'} -> {raw!r}")

    def everyone():
        # The sender is dropped from the group send - see ADMIN_EMAIL above.
        return [(n, e) for (n, e) in book.values() if e.lower() != mailer.user.lower()]

    def deliver(recipients, subject, body):
        sent, failed = 0, []
        for name, email in recipients:
            why = mailer.send(email, subject, body)
            if why:
                failed.append((name, email, why))
                log(f"{tag} NOT SENT {name or '(no name)'} <{email}> - {why}")
            else:
                sent += 1
                log(f"{tag} Sent to {name or '(no name)'} <{email}>")
        return sent, failed

    def receipt(title, sent, total, failed, body=None, extra=None):
        if not ADMIN_EMAIL:
            return
        r = [f"{title} for {when}.", "", f"Emails sent: {sent} of {total}"]
        if skipped:
            r += ["", f"Unusable addresses ({len(skipped)}) - fix these on the page:"]
            r += [f"  {n or '(no name)'} -> {raw!r}" for n, raw in skipped]
        if failed:
            r += ["", f"Not delivered ({len(failed)}):"]
            r += [f"  {n or '(no name)'} <{e}> - {why}" for n, e, why in failed]
        no_email = sorted(str(p.get("name", "")) for k, p in players.items() if k not in book and p.get("name"))
        if no_email:
            r += ["", f"On the list with no email ({len(no_email)}): {', '.join(no_email)}"]
        if extra:
            r += [""] + extra
        if body:
            r += ["", "----- what went out -----", "", body.rstrip()]
        r += ["", TRACKER_URL]
        why = mailer.send(ADMIN_EMAIL, f"[wednesday paddle] {title} - {sent} email(s) for {when}", "\n".join(r) + "\n")
        log(f"{tag} Receipt {'NOT sent - ' + why if why else 'sent to ' + ADMIN_EMAIL}")

    def preview_mail(subject, body, recipients, note=""):
        head = [f"PREVIEW ONLY - nothing was sent to the group and nothing was changed.",
                f"This would go to {len(recipients)} "
                f"{'person' if len(recipients) == 1 else 'people'}"
                + (": " + ", ".join(sorted(n or e for n, e in recipients)) if recipients else "") + "."]
        if note:
            head.append(note)
        why = mailer.send(ADMIN_EMAIL, "[PREVIEW] " + subject, "\n".join(head) + "\n\n----------\n\n" + body)
        log(f"{tag} Preview {'NOT sent - ' + why if why else 'sent to ' + ADMIN_EMAIL}")

    # ---- SUNDAY: reset, open sign-up, email everyone ----
    if job == "sunday":
        subject, body = sunday_email(wed)
        recipients = everyone()
        if preview:
            preview_mail(subject, body, recipients)
            return "sunday-preview"
        if meta.get("openWeek") != wk:
            # Clear last week's replies. Names and emails stay.
            wipe = {}
            for key in players:
                wipe[f"{key}/status"] = "waiting"
                wipe[f"{key}/inAt"] = None
            if wipe:
                store.update("wednesday/players", wipe)
            store.update("wednesday/meta", {"openWeek": wk, "openedAt": now.isoformat(timespec="seconds")})
            store.update("wednesday/state", {"lineup": None})
            log(f"{tag} Replies cleared and sign-up opened for {when} ({len(players)} on the list).")
        sent, failed = deliver(recipients, subject, body)
        store.update("wednesday/meta", {"sundaySent": wk})
        receipt("Sunday sign-up email", sent, len(recipients), failed, body)
        log(f"Done. {tag} {sent} email(s) sent for {when}.")
        return "sunday"

    L = lineup(players) if meta.get("openWeek") == wk else lineup({})
    G = groups(L)

    # ---- TUESDAY: the courts, to everyone on the list ----
    if job == "tuesday":
        subject, body = tuesday_email(wed, L)
        recipients = everyone()
        if preview:
            preview_mail(subject, body, recipients)
            return "tuesday-preview"
        sent, failed = deliver(recipients, subject, body)
        store.update("wednesday/state", {"lineup": {"week": wk, "players": G or None}})
        store.update("wednesday/meta", {"tuesdaySent": wk})
        in_no_email = [n for k, n in L["order"] if k not in book]
        extra = [f"IN but no email, so they did not get this: {', '.join(in_no_email)}"] if in_no_email else None
        receipt("Tuesday courts email", sent, len(recipients), failed, body, extra)
        log(f"Done. {tag} {sent} email(s) sent for {when}.")
        return "tuesday"

    # ---- UPDATE: did anyone's court change since the last email? ----
    snap = store.get("wednesday/state/lineup") or {}
    old = snap.get("players") or {} if snap.get("week") == wk else None
    if old is None:
        if not preview:
            store.update("wednesday/state", {"lineup": {"week": wk, "players": G or None}})
        log(f"{tag} No earlier court list to compare with for {when}. Saved the current one.")
        return "update-baseline"

    changed = [k for k, g in G.items()
               if k not in old
               or old[k].get("court") != g["court"]
               or sorted(old[k].get("mates") or []) != g["mates"]]
    dropped = sorted(str(v.get("name", "")) for k, v in old.items() if k not in G)
    added = sorted(g["name"] for k, g in G.items() if k not in old)

    if not changed and not dropped:
        log(f"{tag} No court changes for {when}.")
        if preview:
            mailer.send(ADMIN_EMAIL, f"[PREVIEW] Wednesday Paddle - no court changes ({when})",
                        "PREVIEW ONLY - no court has changed since the last email, so nothing would be sent.\n\n"
                        + (courts_text(L) or "No one is in.") + "\n")
        return "update-none"

    order_pos = {k: i for i, (k, _) in enumerate(L["order"])}
    changed.sort(key=lambda k: order_pos[k])
    mails, no_email = [], []
    for key in changed:
        me = dict(G[key], key=key)
        if key not in book:
            no_email.append(me["name"])
            continue
        subject, body = update_email(wed, L, me, dropped, added)
        mails.append((book[key][0], book[key][1], subject, body))

    summary = []
    if dropped:
        summary.append(f"Dropped out: {names_list(dropped)}")
    if added:
        summary.append(f"Added: {names_list(added)}")
    summary.append("Court changed for: " + (names_list(G[k]["name"] for k in changed) or "no one still in"))
    if no_email:
        summary.append(f"Changed but no email, so not told: {names_list(no_email)}")
    summary += ["", "Courts now:", "", courts_text(L) or "No one is in."]

    if preview:
        sample = mails[0][3] if mails else "(no one to email)"
        why = mailer.send(ADMIN_EMAIL, f"[PREVIEW] Wednesday Paddle - court change ({when})",
                          "PREVIEW ONLY - nothing was sent to the group and nothing was changed.\n"
                          f"{len(mails)} player(s) would each get their own email.\n\n"
                          + "\n".join(summary) + "\n\n----- example (the one for "
                          + (mails[0][0] if mails else "-") + ") -----\n\n" + sample)
        log(f"{tag} Preview {'NOT sent - ' + why if why else 'sent to ' + ADMIN_EMAIL}")
        return "update-preview"

    sent, failed = 0, []
    for name, email, subject, body in mails:
        why = mailer.send(email, subject, body)
        if why:
            failed.append((name, email, why))
            log(f"{tag} NOT SENT {name} <{email}> - {why}")
        else:
            sent += 1
            log(f"{tag} Sent to {name} <{email}>")
    store.update("wednesday/state", {"lineup": {"week": wk, "players": G or None}})
    receipt("Court change email", sent, len(mails), failed, None, summary)
    log(f"Done. {tag} {sent} email(s) sent for {when}.")
    return "update"


# ---------------------------------------------------------------- real run

class FirebaseStore:
    def __init__(self):
        import firebase_admin
        from firebase_admin import credentials, db
        # The service account bypasses the public rules, so it can read the
        # private emails and write the sign-up stamps the page can't.
        cred = credentials.Certificate(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]))
        firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})
        self.db = db

    def get(self, path):
        return self.db.reference(path).get()

    def update(self, path, values):
        self.db.reference(path).update(values)


if __name__ == "__main__":
    mode = os.environ.get("MODE", "auto").strip().lower()
    if mode not in ("sunday", "tuesday", "update"):
        mode = "auto"
    preview = os.environ.get("PREVIEW", "false").strip().lower() == "true"
    mailer = Mailer(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
    try:
        run(datetime.datetime.now(MICHIGAN), mode, preview, FirebaseStore(), mailer)
    finally:
        mailer.close()
