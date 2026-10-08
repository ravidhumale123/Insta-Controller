import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    ClientError,
    LoginRequired,
    PleaseWaitFewMinutes,
    RateLimitError,
    TwoFactorRequired,
)

load_dotenv()

USERNAME = os.getenv("IG_USERNAME")
PASSWORD = os.getenv("IG_PASSWORD")
VERIFICATION_CODE = os.getenv("IG_VERIFICATION_CODE") or ""
PROXY = os.getenv("IG_PROXY") or ""
MAX_DMS_PER_RUN = int(os.getenv("MAX_DMS_PER_RUN", "10"))
MIN_DELAY = int(os.getenv("MIN_DELAY_SECONDS", "45"))
MAX_DELAY = int(os.getenv("MAX_DELAY_SECONDS", "120"))
SESSION_FILE = Path(os.getenv("SESSION_FILE", "session.json"))
SENT_LOG_FILE = Path(os.getenv("SENT_LOG_FILE", "sent_log.json"))

def _fresh_login(old_uuids=None) -> Client:
    cl = Client()
    cl.delay_range = [2, 6]  # small random pause between internal API calls
    if PROXY:
        cl.set_proxy(PROXY)
    if old_uuids:
        cl.set_uuids(old_uuids)  # keep the same "device" so IG sees one phone
    try:
        cl.login(USERNAME, PASSWORD, verification_code=VERIFICATION_CODE)
    except TwoFactorRequired:
        sys.exit("2FA required: put the current code in IG_VERIFICATION_CODE and rerun.")
    except ChallengeRequired:
        sys.exit(
            "Instagram wants a security check. Open the Instagram app/website, "
            "approve 'It was me', then rerun."
        )
    cl.dump_settings(SESSION_FILE)
    return cl

def get_client() -> Client:
    if not USERNAME or not PASSWORD:
        sys.exit("Set IG_USERNAME and IG_PASSWORD in your .env file.")

    if SESSION_FILE.exists():
        cl = Client()
        cl.delay_range = [2, 6]
        if PROXY:
            cl.set_proxy(PROXY)
        try:
            cl.load_settings(SESSION_FILE)
            cl.login(USERNAME, PASSWORD)  # reuses the saved session
            cl.get_timeline_feed()  # cheap call to check the session is alive
            print("Logged in using saved session.")
            return cl
        except LoginRequired:
            print("Saved session expired, logging in again...")
            old_uuids = cl.get_settings().get("uuids")
            return _fresh_login(old_uuids)
        except Exception as e:  # corrupted file, etc.
            print(f"Session reuse failed ({e}), logging in again...")

    print("Logging in with username/password...")
    return _fresh_login()

def cmd_search(args):
    cl = get_client()
    results = cl.search_users(args.query)
    if not results:
        print("No users found.")
        return
    for u in results[: args.limit]:
        flag = "private" if getattr(u, "is_private", False) else "public"
        print(f"@{u.username:<30} {u.full_name or '':<30} id={u.pk} ({flag})")

def load_lines(path: str):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [l.strip().lstrip("@") for l in lines if l.strip() and not l.startswith("#")]


def load_sent():
    if SENT_LOG_FILE.exists():
        return set(json.loads(SENT_LOG_FILE.read_text(encoding="utf-8")))
    return set()


def save_sent(sent):
    SENT_LOG_FILE.write_text(json.dumps(sorted(sent), indent=2), encoding="utf-8")


def cmd_dm(args):
    usernames = load_lines(args.users)
    template = Path(args.message).read_text(encoding="utf-8").strip()
    sent = load_sent()

    todo = [u for u in usernames if u.lower() not in {s.lower() for s in sent}]
    todo = todo[:MAX_DMS_PER_RUN]
    print(f"{len(usernames)} in list, {len(todo)} to send this run (cap {MAX_DMS_PER_RUN}).")

    if args.dry_run:
        for u in todo:
            print(f"[dry-run] would DM @{u}: {template.format(username=u)!r}")
        return

    cl = get_client()

    for i, username in enumerate(todo, 1):
        try:
            user_id = cl.user_id_from_username(username)
            text = template.format(username=username)
            cl.direct_send(text, user_ids=[user_id])
            sent.add(username)
            save_sent(sent)  # save after every send, so a crash never causes a resend
            print(f"[{i}/{len(todo)}] sent to @{username}")
        except (PleaseWaitFewMinutes, RateLimitError):
            print("Instagram is rate-limiting this account. Stopping. Try again much later.")
            break
        except ChallengeRequired:
            print("Security challenge triggered. Stopping. Resolve it in the Instagram app.")
            break
        except LoginRequired:
            print("Session invalid. Delete session.json and rerun.")
            break
        except ClientError as e:
            print(f"[{i}/{len(todo)}] failed for @{username}: {e}")

        if i < len(todo):
            wait = random.randint(MIN_DELAY, MAX_DELAY)
            print(f"  waiting {wait}s...")
            time.sleep(wait)

    print("Done.")

def main():
    parser = argparse.ArgumentParser(description="Instagram search + DM helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="search users by name/keyword")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.set_defaults(func=cmd_search)

    p_dm = sub.add_parser("dm", help="send DMs to usernames from a file")
    p_dm.add_argument("--users", required=True, help="text file, one username per line")
    p_dm.add_argument("--message", required=True, help="text file with the message template")
    p_dm.add_argument("--dry-run", action="store_true", help="print only, send nothing")
    p_dm.set_defaults(func=cmd_dm)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()