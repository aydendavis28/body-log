"""One-time setup, run on your own computer.

1. Authorises your Whoop developer app and gets a long-lived refresh token.
2. Logs in to Garmin (handles two-factor) and saves a token blob.
3. Makes a dashboard passphrase.
4. Does a quick test pull, then sets the GitHub secrets for you (if the gh CLI is installed)
   or prints them so you can paste them in.
"""
import getpass, http.server, json, os, secrets, shutil, subprocess, sys, threading, urllib.parse, webbrowser
from datetime import datetime, timedelta, timezone
import bodylog as B

REDIRECT = "http://localhost:8765/callback"
WORDS = ("amber basin cedar delta ember fjord glacier harbor island juniper kestrel lagoon meadow nectar orchid "
         "prairie quartz river summit timber umber valley willow yarrow zephyr canyon falcon granite mesa pine").split()


def whoop_auth():
    print("\n— Whoop —")
    print(f"In your Whoop developer app, the redirect URL must be exactly: {REDIRECT}")
    cid = input("Whoop Client ID: ").strip()
    secret = getpass.getpass("Whoop Client Secret (hidden): ").strip()
    state = secrets.token_urlsafe(12)
    url = B.WHOOP_AUTH + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": REDIRECT, "scope": B.WHOOP_SCOPES, "state": state})
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b"<h2>Whoop connected. You can close this tab and go back to the terminal.</h2>")
        def log_message(self, *a): pass

    srv = http.server.HTTPServer(("localhost", 8765), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    print("Opening your browser to approve access… (if it doesn't open, paste this link)\n" + url)
    webbrowser.open(url)
    t = threading.Event()
    while not got and not t.wait(0.5):
        pass
    srv.server_close()
    if got.get("state") != state or "code" not in got:
        sys.exit(f"Whoop authorisation didn't complete: {got}")
    tok = B.Whoop.exchange_code(cid, secret, got["code"], REDIRECT)
    if not tok.get("refresh_token"):
        sys.exit("Whoop didn't return a refresh token. Make sure the 'offline' scope is enabled on your app.")
    print("Whoop authorised.")
    return cid, secret, tok["refresh_token"]


def garmin_auth():
    print("\n— Garmin —")
    from garminconnect import Garmin
    email = input("Garmin email: ").strip()
    pw = getpass.getpass("Garmin password (hidden, not stored): ")
    g = Garmin(email, pw, prompt_mfa=lambda: input("Garmin two-factor code: ").strip())
    g.login()
    print("Garmin logged in as", g.get_full_name() if hasattr(g, "get_full_name") else email)
    return g


def main():
    cid, secret, refresh = whoop_auth()
    g = garmin_auth()

    print("\n— Test pull (last 3 days) —")
    tokens = {"refresh": refresh}
    w = B.Whoop(cid, secret, refresh, on_new_refresh=lambda t: tokens.update(refresh=t))
    w.authenticate()
    now = datetime.now(timezone.utc)
    raw = w.fetch(now - timedelta(days=3), now)
    print(f"Whoop: {len(raw['recovery'])} recoveries, {len(raw['sleep'])} sleeps, {len(raw['workouts'])} workouts")
    days = [now.date() - timedelta(days=i) for i in (2, 1, 0)]
    graw = B.fetch_garmin(g, days)
    test = {"daily": {}, "workouts": []}
    B.normalise_garmin(graw, test)
    print(f"Garmin: {len(test['daily'])} days with data, {len(graw['activities'])} activities")
    garmin_tokens = g.client.dumps()

    phrase = "-".join(secrets.choice(WORDS) for _ in range(6))
    use_own = input(f"\nDashboard passphrase — press Enter to use '{phrase}' or type your own: ").strip()
    phrase = use_own or phrase

    secrets_map = {
        "WHOOP_CLIENT_ID": cid, "WHOOP_CLIENT_SECRET": secret, "WHOOP_REFRESH_TOKEN": tokens["refresh"],
        "GARMIN_TOKENS": garmin_tokens, "DASH_PASSPHRASE": phrase,
    }
    if shutil.which("gh") and input("\nSet these as GitHub secrets on this repo now with the gh CLI? [Y/n] ").strip().lower() in ("", "y", "yes"):
        for k, v in secrets_map.items():
            subprocess.run(["gh", "secret", "set", k], input=v.encode(), check=True)
            print("  set", k)
        print("\nDone. Remember to also add SECRETS_PAT (see README step 4).")
    else:
        print("\nAdd each of these in GitHub → your repo → Settings → Secrets and variables → Actions → New repository secret:\n")
        for k, v in secrets_map.items():
            print(f"{k}\n{v}\n")
    print(f"Your dashboard passphrase is: {phrase}\nKeep it somewhere safe (a password manager is ideal).")
    print("Don't run this test again after setting the secrets without re-setting WHOOP_REFRESH_TOKEN — Whoop rotates it on every use.")


if __name__ == "__main__":
    main()
