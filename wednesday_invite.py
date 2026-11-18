"""
Wednesday Paddle emails (7am Wednesday group).

Four jobs. The workflow runs this often; the script works out for itself,
in Michigan time, which job is due and stamps each one in Firebase so it is
never done twice and a late or skipped run is picked up by the next one.

  THURSDAY 8am -> the morning after paddle: clear last week's replies, open
                  sign-up for next Wednesday, email everyone with the link.
  SUNDAY 2pm   -> reminder to everyone, with who is in so far. (If the
                  Thursday email never went out, Sunday does the reset instead.)
  TUESDAY 2pm  -> email everyone the courts: full courts of 4 in the order
                  people said IN, then whoever is left over as "waiting",
                  with how many more players that court needs.
  AFTER THAT   -> every hour until 7am Wednesday, check whether anyone dropped
                  or joined. If a court changed, email only the players whose
                  court (or waiting group) is different now.

MODE (auto / thursday / sunday / tuesday / update) forces one job from the
"Run workflow" button. MODE=open opens sign-up for the coming Wednesday right
away (clears last week's replies) without sending any email. PREVIEW=true emails Kevin only and changes nothing.
"""
import os
import re
import json
import smtplib
import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

# Twilio is optional — text messages are only sent when the credentials are set.
try:
    from twilio.rest import Client as TwilioClient
except ImportError:
    TwilioClient = None

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
THURSDAY_HOUR = 8      # 8:00 AM for the Thursday sign-up email
ROLLOVER_HOUR = 12     # at noon Wednesday the page points at next week

# No paddle before this date. Must match FIRST_WEDNESDAY in the page.
FIRST_WEDNESDAY = datetime.date(2026, 10, 14)

# First Thursday sign-up email. Before this, sign-up opened on Sunday only.
# Must match FIRST_THURSDAY in the page.
FIRST_THURSDAY = datetime.date(2026, 10, 22)

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

def thursday_email(wed):
    when = nice(wed)
    subject = f"Wednesday Paddle {when} - 7am - sign-up is open"
    body = f"""Paddle next Wednesday ({short(wed)}) at 7am. Sign-up is open now.

Tap the link, find your name, and tap IN:
{TRACKER_URL}

Courts fill 4 at a time, in the order people say IN. A reminder goes out Sunday at 2pm, and the courts email goes out Tuesday at 2pm.

If you say IN and then can't make it, tap OUT right away so the next person in line gets your spot.

You're getting this because you're on the Wednesday list. Want off the list? Tap "remove from list" under your name on the page, or just reply and let me know.
"""
    return subject, body


def sunday_email(wed, L=None):
    """L is the lineup so far when sign-up already opened Thursday (reminder);
    None when this Sunday run is the one opening sign-up."""
    when = nice(wed)
    if L is not None:
        subject = f"Reminder: Wednesday Paddle {when} - 7am"
        if L["order"]:
            n = len(L["order"])
            names = "\n".join(f"  {i}. {name}" for i, (_, name) in enumerate(L["order"], start=1))
            so_far = f"{n} {'is' if n == 1 else 'are'} in so far:\n{names}"
        else:
            so_far = "No one is in yet."
        body = f"""Reminder: paddle this Wednesday ({short(wed)}) at 7am.

{so_far}

Not in yet? Tap the link, find your name, and tap IN:
{TRACKER_URL}

Courts fill 4 at a time, in the order people say IN. The courts email goes out Tuesday at 2pm.

If you said IN and can't make it, tap OUT right away so the next person in line gets your spot.

You're getting this because you're on the Wednesday list. Want off the list? Tap "remove from list" under your name on the page, or just reply and let me know.
"""
        return subject, body
    subject = f"Wednesday Paddle {when} - 7am - sign-up is open"
    body = f"""Paddle this Wednesday ({short(wed)}) at 7am. Sign-up is open now.

Tap the link, find your name, and tap IN:
{TRACKER_URL}

Courts fill 4 at a time, in the order people say IN. A second email goes out Tuesday at 2pm with the courts.

If you say IN and then can't make it, tap OUT right away so the next person in line gets your spot.

You're getting this because you're on the Wednesday list. Want off the list? Tap "remove from list" under your name on the page, or just reply and let me know.
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


# ---------------------------------------------------------------- SMS text versions (shorter for phones)

def thursday_sms(wed):
    return (f"Wednesday Paddle {short(wed)} at 7am - sign-up is open.\n\n"
            f"Tap IN: {TRACKER_URL}\n\n"
            f"Courts fill 4 at a time in sign-up order. Reminder Sunday 2pm, courts email Tuesday 2pm.\n\n"
            f"Reply STOP to opt out.")


def sunday_sms(wed, L=None):
    if L is not None and L["order"]:
        n = len(L["order"])
        names = ", ".join(name for _, name in L["order"])
        return (f"Reminder: paddle Wed {short(wed)} 7am.\n\n"
                f"{n} in so far: {names}\n\n"
                f"Not in yet? Tap IN: {TRACKER_URL}\n\n"
                f"Reply STOP to opt out.")
    return (f"Paddle this Wed {short(wed)} at 7am - sign-up is open.\n\n"
            f"Tap IN: {TRACKER_URL}\n\n"
            f"Reply STOP to opt out.")


def tuesday_sms(wed, L):
    if not L["order"]:
        return (f"No one signed up for paddle Wed {short(wed)} 7am yet.\n\n"
                f"Tap IN: {TRACKER_URL}\n\n"
                f"Reply STOP to opt out.")
    lines = [f"Courts for Wed {short(wed)} 7am paddle:"]
    lines.append("")
    n = 0
    for i, court in enumerate(L["courts"], start=1):
        lines.append(f"Court {i}: " + ", ".join(name for _, name in court))
    if L["waiting"]:
        nxt = len(L["courts"]) + 1
        lines.append(f"Waiting (Court {nxt} needs {L['need']} more): " +
                     ", ".join(name for _, name in L["waiting"]))
    lines.append("")
    lines.append(f"Can't make it? Tap OUT: {TRACKER_URL}")
    lines.append("")
    lines.append("Reply STOP to opt out.")
    return "\n".join(lines)


def update_sms(wed, L, me, dropped, added):
    lines = [f"Court change for Wed {short(wed)} 7am paddle."]
    if dropped:
        lines.append(f"Dropped: {names_list(dropped)}")
    if added:
        lines.append(f"Added: {names_list(added)}")
    lines.append("")
    if me["court"]:
        others = [n for k, n in L["courts"][me["court"] - 1] if k != me["key"]]
        lines.append(f"You're on Court {me['court']} with {names_list(others)}.")
    else:
        nxt = len(L["courts"]) + 1
        lines.append(f"You're waiting for Court {nxt} (needs {plural(L['need'])}).")
    lines.append("")
    lines.append(f"Full courts: {TRACKER_URL}")
    lines.append("Reply STOP to opt out.")
    return "\n".join(lines)


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


class TextSender:
    """Send individual SMS messages via Twilio."""

    def __init__(self, account_sid, auth_token, from_number):
        if TwilioClient is None:
            raise RuntimeError("twilio package is not installed")
        self.client = TwilioClient(account_sid, auth_token)
        self.from_number = from_number

    def send(self, to, body):
        """Returns None if sent, or a short reason if not."""
        try:
            msg = self.client.messages.create(body=body, from_=self.from_number, to=to)
            return None if msg.sid else "no SID returned"
        except Exception as err:
            return f"twilio error: {err}"


PHONE_OK = re.compile(r"^\+1\d{10}$")


def clean_phone(raw):
    """Return a +1XXXXXXXXXX number, or None if it can't be used."""
    if not isinstance(raw, str):
        return None
    digits = re.sub(r"[^\d+]", "", raw.strip())
    # Accept 10 digits (add +1), 11 starting with 1 (add +), or already +1
    if re.match(r"^\d{10}$", digits):
        digits = "+1" + digits
    elif re.match(r"^1\d{10}$", digits):
        digits = "+" + digits
    elif not digits.startswith("+"):
        digits = "+" + digits
    if not PHONE_OK.match(digits):
        return None
    return digits


def address_book(players, contacts):
    """{player key: (name, email, phone, method)} for everyone on the list with a usable contact.
    method is 'email' or 'text'. Legacy players with no method field default to 'email'."""
    book, skipped, seen_email, seen_phone = {}, [], set(), set()
    for key, contact in (contacts or {}).items():
        person = players.get(key)
        if not isinstance(person, dict):
            continue                           # contact left behind by a removed player
        name = str(person.get("name", ""))
        if not isinstance(contact, dict):
            continue
        method = contact.get("method", "email")  # legacy contacts have no method -> email
        raw_email = contact.get("email", "")
        raw_phone = contact.get("phone", "")

        if method == "text" and raw_phone:
            phone = clean_phone(raw_phone)
            if not phone:
                skipped.append((name, raw_phone, "bad phone"))
                continue
            if phone in seen_phone:
                continue
            seen_phone.add(phone)
            book[key] = (name, None, phone, "text")
        else:
            # Default to email for legacy or explicit email contacts
            if not (raw_email or "").strip():
                continue
            email = clean_email(raw_email)
            if not email:
                skipped.append((name, raw_email, "bad email"))
                continue
            if email.lower() in seen_email:
                continue
            seen_email.add(email.lower())
            book[key] = (name, email, None, "email")
    return book, skipped


def run(now, mode, preview, store, mailer, texter=None, log=print):
    """
    store  - object with get(path) and update(path, dict)
    mailer - object with .user and .send(to, subject, body)
    texter - optional TextSender for SMS; None = no texts sent
    Returns a short word for what was done (used by the tests).
    """
    wed = target_wednesday(now)
    wk = week_key(wed)
    when = nice(wed)

    players = store.get("wednesday/players") or {}
    contacts = store.get("wednesday/contacts") or {}
    meta = store.get("wednesday/meta") or {}
    players = {k: v for k, v in players.items() if isinstance(v, dict)}

    thursday_day = wed - datetime.timedelta(days=6)
    thursday_due = at(thursday_day, THURSDAY_HOUR)
    sunday_due = at(wed - datetime.timedelta(days=3), SEND_HOUR)
    tuesday_due = at(wed - datetime.timedelta(days=1), SEND_HOUR)
    play_time = at(wed, PLAY_HOUR)

    # ---- which job? ----
    if mode in ("open", "thursday", "sunday", "tuesday", "update"):
        job = mode
    elif (thursday_day >= FIRST_THURSDAY and thursday_due <= now < sunday_due
          and meta.get("thursdaySent") != wk and meta.get("openWeek") != wk):
        job = "thursday"
    elif now >= sunday_due and meta.get("sundaySent") != wk:
        job = "sunday"
    elif tuesday_due <= now < play_time and meta.get("tuesdaySent") != wk:
        job = "tuesday"
    elif tuesday_due <= now < play_time and meta.get("tuesdaySent") == wk:
        job = "update"
    else:
        log(f"[auto] Nothing due right now for {when} "
            f"(Thursday email {'sent' if meta.get('thursdaySent') == wk else 'due ' + thursday_due.strftime('%a %b %d %I:%M %p')}, "
            f"Sunday email {'sent' if meta.get('sundaySent') == wk else 'due ' + sunday_due.strftime('%a %b %d %I:%M %p')}, "
            f"Tuesday email {'sent' if meta.get('tuesdaySent') == wk else 'due ' + tuesday_due.strftime('%a %b %d %I:%M %p')}).")
        return "nothing"

    tag = f"[{job}{' preview' if preview else ''}]"
    book, skipped = address_book(players, contacts)
    for name, raw, reason in skipped:
        log(f"{tag} Unusable contact: {name or '(no name)'} -> {raw!r} ({reason})")

    def everyone():
        # The sender is dropped from the group send - see ADMIN_EMAIL above.
        out = []
        for (n, e, ph, m) in book.values():
            if m == "text" and ph:
                out.append((n, None, ph, "text"))
            elif e and e.lower() != mailer.user.lower():
                out.append((n, e, None, "email"))
        return out

    def deliver(recipients, subject, body, sms_body=None):
        sent_email, sent_text, failed = 0, 0, []
        for name, email, phone, method in recipients:
            if method == "text" and phone and texter:
                text = sms_body or body
                # SMS: strip the unsubscribe line and keep it short
                why = texter.send(phone, text)
                if why:
                    failed.append((name, phone, why))
                    log(f"{tag} NOT SENT (text) {name or '(no name)'} {phone} - {why}")
                else:
                    sent_text += 1
                    log(f"{tag} Texted {name or '(no name)'} {phone}")
            elif method == "text" and phone and not texter:
                failed.append((name, phone, "Twilio not configured"))
                log(f"{tag} NOT SENT (text) {name or '(no name)'} {phone} - Twilio not configured")
            elif email:
                why = mailer.send(email, subject, body)
                if why:
                    failed.append((name, email, why))
                    log(f"{tag} NOT SENT {name or '(no name)'} <{email}> - {why}")
                else:
                    sent_email += 1
                    log(f"{tag} Sent to {name or '(no name)'} <{email}>")
        return sent_email, sent_text, failed

    def receipt(title, sent_email, sent_text, total, failed, body=None, extra=None):
        if not ADMIN_EMAIL:
            return
        sent = sent_email + sent_text
        r = [f"{title} for {when}.", ""]
        if sent_text:
            r.append(f"Emails sent: {sent_email} / Texts sent: {sent_text} (of {total} total)")
        else:
            r.append(f"Emails sent: {sent_email} of {total}")
        if skipped:
            r += ["", f"Unusable contacts ({len(skipped)}) - fix these on the page:"]
            r += [f"  {n or '(no name)'} -> {raw!r} ({reason})" for n, raw, reason in skipped]
        if failed:
            r += ["", f"Not delivered ({len(failed)}):"]
            r += [f"  {n or '(no name)'} {addr} - {why}" for n, addr, why in failed]
        no_contact = sorted(str(p.get("name", "")) for k, p in players.items() if k not in book and p.get("name"))
        if no_contact:
            r += ["", f"On the list with no contact ({len(no_contact)}): {', '.join(no_contact)}"]
        if extra:
            r += [""] + extra
        if body:
            r += ["", "----- what went out (email version) -----", "", body.rstrip()]
        r += ["", TRACKER_URL]
        why = mailer.send(ADMIN_EMAIL, f"[wednesday paddle] {title} - {sent} message(s) for {when}", "\n".join(r) + "\n")
        log(f"{tag} Receipt {'NOT sent - ' + why if why else 'sent to ' + ADMIN_EMAIL}")

    def preview_mail(subject, body, recipients, note=""):
        head = [f"PREVIEW ONLY - nothing was sent to the group and nothing was changed."]
        email_r = [r for r in recipients if r[3] == "email"]
        text_r = [r for r in recipients if r[3] == "text"]
        parts = []
        if email_r:
            parts.append(f"{len(email_r)} email")
        if text_r:
            parts.append(f"{len(text_r)} text")
        head.append(f"This would go to {' + '.join(parts) if parts else '0'} "
                     f"{'person' if len(recipients) == 1 else 'people'}"
                     + (": " + ", ".join(sorted(n for n, _, _, _ in recipients)) if recipients else "") + ".")
        if note:
            head.append(note)
        why = mailer.send(ADMIN_EMAIL, "[PREVIEW] " + subject, "\n".join(head) + "\n\n----------\n\n" + body)
        log(f"{tag} Preview {'NOT sent - ' + why if why else 'sent to ' + ADMIN_EMAIL}")

    def open_signup():
        """Clear last week's replies and open sign-up for this Wednesday. Once per week."""
        if meta.get("openWeek") == wk:
            return
        wipe = {}
        for key in players:
            wipe[f"{key}/status"] = "waiting"
            wipe[f"{key}/inAt"] = None
        if wipe:
            store.update("wednesday/players", wipe)
        store.update("wednesday/meta", {"openWeek": wk, "openedAt": now.isoformat(timespec="seconds")})
        store.update("wednesday/state", {"lineup": None})
        meta["openWeek"] = wk
        log(f"{tag} Replies cleared and sign-up opened for {when} ({len(players)} on the list).")

    # ---- OPEN: open sign-up now, no email ----
    if job == "open":
        if preview:
            log(f"{tag} Would open sign-up for {when} (openWeek is {meta.get('openWeek')!r}). Nothing changed.")
            return "open-preview"
        open_signup()
        log(f"Done. {tag} Sign-up is open for {when}. No email sent.")
        return "open"

    # ---- THURSDAY: reset, open sign-up for next Wednesday, email/text everyone ----
    if job == "thursday":
        subject, body = thursday_email(wed)
        sms = thursday_sms(wed)
        recipients = everyone()
        if preview:
            preview_mail(subject, body, recipients,
                         "" if meta.get("openWeek") != wk else
                         "Note: sign-up for this Wednesday is already open, so a real run would not clear any replies.")
            return "thursday-preview"
        open_signup()
        sent_email, sent_text, failed = deliver(recipients, subject, body, sms)
        store.update("wednesday/meta", {"thursdaySent": wk})
        receipt("Thursday sign-up", sent_email, sent_text, len(recipients), failed, body)
        log(f"Done. {tag} {sent_email} email(s) + {sent_text} text(s) sent for {when}.")
        return "thursday"

    # ---- SUNDAY: reminder if sign-up opened Thursday, otherwise reset + open ----
    if job == "sunday":
        already_open = meta.get("openWeek") == wk
        L_so_far = lineup(players) if already_open else None
        subject, body = sunday_email(wed, L_so_far)
        sms = sunday_sms(wed, L_so_far)
        recipients = everyone()
        if preview:
            preview_mail(subject, body, recipients)
            return "sunday-preview"
        open_signup()
        sent_email, sent_text, failed = deliver(recipients, subject, body, sms)
        store.update("wednesday/meta", {"sundaySent": wk})
        title = "Sunday reminder" if already_open else "Sunday sign-up"
        receipt(title, sent_email, sent_text, len(recipients), failed, body)
        log(f"Done. {tag} {sent_email} email(s) + {sent_text} text(s) sent for {when}.")
        return "sunday"

    L = lineup(players) if meta.get("openWeek") == wk else lineup({})
    G = groups(L)

    # ---- TUESDAY: the courts, to everyone on the list ----
    if job == "tuesday":
        subject, body = tuesday_email(wed, L)
        sms = tuesday_sms(wed, L)
        recipients = everyone()
        if preview:
            preview_mail(subject, body, recipients)
            return "tuesday-preview"
        sent_email, sent_text, failed = deliver(recipients, subject, body, sms)
        store.update("wednesday/state", {"lineup": {"week": wk, "players": G or None}})
        store.update("wednesday/meta", {"tuesdaySent": wk})
        in_no_contact = [n for k, n in L["order"] if k not in book]
        extra = [f"IN but no contact, so they did not get this: {', '.join(in_no_contact)}"] if in_no_contact else None
        receipt("Tuesday courts", sent_email, sent_text, len(recipients), failed, body, extra)
        log(f"Done. {tag} {sent_email} email(s) + {sent_text} text(s) sent for {when}.")
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
    msgs, no_contact = [], []
    for key in changed:
        me = dict(G[key], key=key)
        if key not in book:
            no_contact.append(me["name"])
            continue
        name, email, phone, method = book[key]
        subject, body = update_email(wed, L, me, dropped, added)
        sms = update_sms(wed, L, me, dropped, added)
        msgs.append((name, email, phone, method, subject, body, sms))

    summary = []
    if dropped:
        summary.append(f"Dropped out: {names_list(dropped)}")
    if added:
        summary.append(f"Added: {names_list(added)}")
    summary.append("Court changed for: " + (names_list(G[k]["name"] for k in changed) or "no one still in"))
    if no_contact:
        summary.append(f"Changed but no contact, so not told: {names_list(no_contact)}")
    summary += ["", "Courts now:", "", courts_text(L) or "No one is in."]

    if preview:
        sample = msgs[0][5] if msgs else "(no one to notify)"
        why = mailer.send(ADMIN_EMAIL, f"[PREVIEW] Wednesday Paddle - court change ({when})",
                          "PREVIEW ONLY - nothing was sent to the group and nothing was changed.\n"
                          f"{len(msgs)} player(s) would each get their own message.\n\n"
                          + "\n".join(summary) + "\n\n----- example (the one for "
                          + (msgs[0][0] if msgs else "-") + ") -----\n\n" + sample)
        log(f"{tag} Preview {'NOT sent - ' + why if why else 'sent to ' + ADMIN_EMAIL}")
        return "update-preview"

    sent_email, sent_text, failed = 0, 0, []
    for name, email, phone, method, subject, body, sms in msgs:
        if method == "text" and phone and texter:
            why = texter.send(phone, sms)
            if why:
                failed.append((name, phone, why))
                log(f"{tag} NOT SENT (text) {name} {phone} - {why}")
            else:
                sent_text += 1
                log(f"{tag} Texted {name} {phone}")
        elif method == "text" and phone and not texter:
            failed.append((name, phone, "Twilio not configured"))
            log(f"{tag} NOT SENT (text) {name} {phone} - Twilio not configured")
        elif email:
            why = mailer.send(email, subject, body)
            if why:
                failed.append((name, email, why))
                log(f"{tag} NOT SENT {name} <{email}> - {why}")
            else:
                sent_email += 1
                log(f"{tag} Sent to {name} <{email}>")
    store.update("wednesday/state", {"lineup": {"week": wk, "players": G or None}})
    receipt("Court change", sent_email, sent_text, len(msgs), failed, None, summary)
    log(f"Done. {tag} {sent_email} email(s) + {sent_text} text(s) sent for {when}.")
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
    if mode not in ("open", "thursday", "sunday", "tuesday", "update"):
        mode = "auto"
    preview = os.environ.get("PREVIEW", "false").strip().lower() == "true"
    mailer = Mailer(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])

    # Twilio is optional — texts are sent only when all three env vars are set.
    texter = None
    twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    twilio_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    twilio_number = os.environ.get("TWILIO_PHONE_NUMBER", "").strip()
    if twilio_sid and twilio_token and twilio_number:
        if TwilioClient is None:
            print("[warning] TWILIO credentials set but twilio package not installed — texts will not be sent")
        else:
            texter = TextSender(twilio_sid, twilio_token, twilio_number)
            print(f"[info] Twilio configured, texts will be sent from {twilio_number}")
    else:
        print("[info] Twilio not configured — text-preference players will not receive messages")

    try:
        run(datetime.datetime.now(MICHIGAN), mode, preview, FirebaseStore(), mailer, texter)
    finally:
        mailer.close()
