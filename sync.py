"""Daily job: pull recent Whoop + Garmin data, merge with history, write the encrypted dashboard site.

Environment variables (set as GitHub secrets):
  WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET, WHOOP_REFRESH_TOKEN
  GARMIN_TOKENS        JSON token blob from setup_local.py
  DASH_PASSPHRASE      passphrase that unlocks the dashboard
Optional:
  BACKFILL_DAYS        how many days to pull (default 5; first run pulls 180)
"""
import argparse, json, os, shutil, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import bodylog as B

HERE = Path(__file__).parent
TOKENS_OUT = HERE / ".tokens"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev", default="prev.enc.json", help="previous data.enc.json (if any)")
    ap.add_argument("--out", default="site")
    ap.add_argument("--days", type=int, default=int(os.getenv("BACKFILL_DAYS") or 0))
    args = ap.parse_args()

    passphrase = os.environ["DASH_PASSPHRASE"]
    history, salt = {"daily": {}, "workouts": []}, None
    prev = Path(args.prev)
    if prev.exists() and prev.stat().st_size > 0:
        try:
            payload, salt = B.decrypt(json.loads(prev.read_text()), passphrase)
            history = payload["db"]
            print(f"Loaded history: {len(history['daily'])} days, {len(history['workouts'])} workouts")
        except Exception as e:
            print(f"Couldn't open previous data ({type(e).__name__}); starting fresh. "
                  "If you changed DASH_PASSPHRASE this is expected.")
    days = args.days or (5 if history["daily"] else 180)
    today = datetime.now(timezone.utc).date()
    start_day = today - timedelta(days=days)
    print(f"Pulling {days} days: {start_day} → {today}")

    fresh = {"daily": {}, "workouts": []}
    status = {}
    TOKENS_OUT.mkdir(exist_ok=True)

    # --- Whoop
    try:
        def save_whoop(tok):
            (TOKENS_OUT / "whoop_refresh").write_text(tok)
        w = B.Whoop(os.environ["WHOOP_CLIENT_ID"], os.environ["WHOOP_CLIENT_SECRET"],
                    os.environ["WHOOP_REFRESH_TOKEN"], on_new_refresh=save_whoop)
        w.authenticate()
        start = datetime.combine(start_day - timedelta(days=1), datetime.min.time(), timezone.utc)
        raw = w.fetch(start, datetime.now(timezone.utc))
        B.normalise_whoop(raw, fresh)
        status["whoop"] = {"ok": True, "records": sum(len(v) for v in raw.values())}
        print(f"Whoop: {status['whoop']['records']} records")
    except Exception as e:
        status["whoop"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        print("Whoop failed:", status["whoop"]["error"])

    # --- Garmin
    try:
        g = B.garmin_client(os.environ["GARMIN_TOKENS"])
        dlist = [start_day + timedelta(days=i) for i in range((today - start_day).days + 1)]
        raw = B.fetch_garmin(g, dlist)
        (TOKENS_OUT / "garmin.json").write_text(g.client.dumps())
        B.normalise_garmin(raw, fresh)
        status["garmin"] = {"ok": True, "days": len(raw["days"]), "activities": len(raw["activities"])}
        print(f"Garmin: {len(raw['days'])} days, {len(raw['activities'])} activities")
    except Exception as e:
        status["garmin"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        print("Garmin failed:", status["garmin"]["error"])

    # today's day totals are still accumulating; keep last night's sleep/HRV but not partial steps/calories
    for dev in fresh["daily"].get(today.isoformat(), {}).values():
        for key in ("steps", "kcal", "activeKcal", "stress", "bodyBattery"):
            dev.pop(key, None)

    # the first day of the window can be partial — don't let it overwrite good history
    cutoff = (start_day + timedelta(days=1)).isoformat()
    if history["daily"]:
        fresh["daily"] = {k: v for k, v in fresh["daily"].items() if k >= cutoff}
    B.merge_into(history, fresh)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "site" / "index.html", out / "index.html") if out.resolve() != (HERE / "site").resolve() else None
    payload = {"generated": datetime.now(timezone.utc).isoformat(), "status": status, "db": history}
    (out / "data.enc.json").write_text(json.dumps(B.encrypt(payload, passphrase, salt)))
    (out / ".nojekyll").write_text("")
    print(f"Wrote {out/'data.enc.json'}: {len(history['daily'])} days, {len(history['workouts'])} workouts")

    if not status.get("whoop", {}).get("ok") and not status.get("garmin", {}).get("ok"):
        sys.exit("Both sources failed — see errors above.")


if __name__ == "__main__":
    main()
