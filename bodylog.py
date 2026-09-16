"""Shared code: pull Whoop + Garmin, normalise to the dashboard's format, encrypt."""
from __future__ import annotations
import base64, json, os, time
from datetime import date, datetime, timedelta, timezone
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

WHOOP_AUTH = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API = "https://api.prod.whoop.com/developer"
WHOOP_SCOPES = "offline read:recovery read:cycles read:sleep read:workout read:profile"
KDF_ITER = 310_000


def num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def put(d: dict, key: str, v):
    v = num(v)
    if v is not None:
        d[key] = round(v, 2)


# ---------------------------------------------------------------- encryption
def _key(passphrase: str, salt: bytes) -> bytes:
    return PBKDF2HMAC(hashes.SHA256(), 32, salt, KDF_ITER).derive(passphrase.encode())


def encrypt(payload: dict, passphrase: str, salt: bytes | None = None) -> dict:
    salt = salt or os.urandom(16)
    iv = os.urandom(12)
    ct = AESGCM(_key(passphrase, salt)).encrypt(iv, json.dumps(payload, separators=(",", ":")).encode(), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": KDF_ITER, "salt": b64(salt), "iv": b64(iv), "data": b64(ct)}


def decrypt(blob: dict, passphrase: str) -> tuple[dict, bytes]:
    salt = base64.b64decode(blob["salt"])
    pt = AESGCM(_key(passphrase, salt)).decrypt(base64.b64decode(blob["iv"]), base64.b64decode(blob["data"]), None)
    return json.loads(pt), salt


# ---------------------------------------------------------------- whoop
class Whoop:
    def __init__(self, client_id, client_secret, refresh_token, on_new_refresh=None):
        self.cid, self.secret, self.refresh = client_id, client_secret, refresh_token
        self.on_new_refresh = on_new_refresh
        self.access = None

    @staticmethod
    def exchange_code(client_id, client_secret, code, redirect_uri) -> dict:
        r = requests.post(WHOOP_TOKEN, data={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "client_secret": client_secret, "redirect_uri": redirect_uri}, timeout=30)
        r.raise_for_status()
        return r.json()

    def authenticate(self):
        r = requests.post(WHOOP_TOKEN, data={
            "grant_type": "refresh_token", "refresh_token": self.refresh, "client_id": self.cid,
            "client_secret": self.secret, "scope": "offline"}, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"Whoop refused the refresh token ({r.status_code}): {r.text[:200]}. "
                               "Run setup_local.py again to re-authorise Whoop.")
        tok = r.json()
        self.access = tok["access_token"]
        if tok.get("refresh_token"):
            self.refresh = tok["refresh_token"]  # Whoop rotates refresh tokens on every use
            if self.on_new_refresh:
                self.on_new_refresh(self.refresh)

    def _get_all(self, path, start: datetime, end: datetime):
        out, nxt = [], None
        while True:
            params = {"limit": 25, "start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z")}
            if nxt:
                params["nextToken"] = nxt
            for attempt in range(5):
                r = requests.get(f"{WHOOP_API}{path}", params=params, headers={"Authorization": f"Bearer {self.access}"}, timeout=30)
                if r.status_code == 429:
                    time.sleep(2 ** attempt * 3)
                    continue
                r.raise_for_status()
                break
            body = r.json()
            out += body.get("records", [])
            nxt = body.get("next_token") or body.get("nextToken")
            if not nxt:
                return out

    def fetch(self, start: datetime, end: datetime) -> dict:
        return {
            "cycles": self._get_all("/v2/cycle", start, end),
            "recovery": self._get_all("/v2/recovery", start, end),
            "sleep": self._get_all("/v2/activity/sleep", start, end),
            "workouts": self._get_all("/v2/activity/workout", start, end),
        }


def _local_dt(iso: str, offset: str | None) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if offset:
        sign = -1 if offset.startswith("-") else 1
        hh, mm = offset.lstrip("+-").split(":")
        dt = dt.astimezone(timezone(sign * timedelta(hours=int(hh), minutes=int(mm))))
    return dt


def normalise_whoop(raw: dict, db: dict):
    daily = db["daily"]
    cycle_day, sleep_day = {}, {}
    for s in raw["sleep"]:
        if s.get("nap") or not s.get("end") or s.get("score_state") != "SCORED":
            continue
        k = _local_dt(s["end"], s.get("timezone_offset")).date().isoformat()
        sleep_day[s["id"]] = k
        if s.get("cycle_id") is not None:
            cycle_day[s["cycle_id"]] = k
        sc = s.get("score") or {}
        st = sc.get("stage_summary") or {}
        d = daily.setdefault(k, {}).setdefault("whoop", {})
        ms = lambda key: (num(st.get(key)) or 0) / 60000
        light, deep, rem = ms("total_light_sleep_time_milli"), ms("total_slow_wave_sleep_time_milli"), ms("total_rem_sleep_time_milli")
        if light + deep + rem > 0:
            put(d, "lightMin", light); put(d, "deepMin", deep); put(d, "remMin", rem)
            put(d, "awakeMin", ms("total_awake_time_milli")); put(d, "sleepMin", light + deep + rem)
        put(d, "resp", sc.get("respiratory_rate"))
        put(d, "sleepPerf", sc.get("sleep_performance_percentage"))
        put(d, "sleepEff", sc.get("sleep_efficiency_percentage"))
    for rec in raw["recovery"]:
        if rec.get("score_state") != "SCORED":
            continue
        k = sleep_day.get(rec.get("sleep_id")) or cycle_day.get(rec.get("cycle_id"))
        if not k:
            continue
        sc = rec.get("score") or {}
        d = daily.setdefault(k, {}).setdefault("whoop", {})
        put(d, "recovery", sc.get("recovery_score")); put(d, "rhr", sc.get("resting_heart_rate"))
        put(d, "hrv", sc.get("hrv_rmssd_milli")); put(d, "spo2", sc.get("spo2_percentage"))
        put(d, "skinTemp", sc.get("skin_temp_celsius"))
    for c in raw["cycles"]:
        sc = c.get("score") or {}
        if not sc or not c.get("end"):  # current cycle is still in progress
            continue
        k = cycle_day.get(c.get("id"))
        if not k and c.get("start"):
            k = (_local_dt(c["start"], c.get("timezone_offset")) + timedelta(hours=8)).date().isoformat()
        if not k:
            continue
        d = daily.setdefault(k, {}).setdefault("whoop", {})
        put(d, "strain", sc.get("strain"))
        if num(sc.get("kilojoule")) is not None:
            put(d, "kcal", num(sc["kilojoule"]) / 4.184)
    for w in raw["workouts"]:
        if not w.get("start"):
            continue
        sc = w.get("score") or {}
        s = datetime.fromisoformat(w["start"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(w["end"].replace("Z", "+00:00")) if w.get("end") else s
        kj = num(sc.get("kilojoule"))
        db["workouts"].append({
            "src": "whoop", "id": f"w-{w.get('id')}", "start": int(s.timestamp() * 1000), "end": int(e.timestamp() * 1000),
            "name": (w.get("sport_name") or "Workout").replace("_", " ").title(), "durMin": round((e - s).total_seconds() / 60, 1),
            "strain": num(sc.get("strain")), "kcal": round(kj / 4.184) if kj is not None else None,
            "avgHr": num(sc.get("average_heart_rate")), "maxHr": num(sc.get("max_heart_rate")), "distM": num(sc.get("distance_meter")),
        })


# ---------------------------------------------------------------- garmin
def garmin_client(tokens_json: str):
    from garminconnect import Garmin
    g = Garmin()
    g.login(tokens_json)
    return g


def _first(d: dict, *paths):
    for p in paths:
        cur = d
        for part in p.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if num(cur) is not None:
            return num(cur)
    return None


def fetch_garmin(g, days: list[date]) -> dict:
    raw = {"days": {}, "activities": []}
    for dd in days:
        k = dd.isoformat()
        entry = {}
        for name, fn in (("sleep", g.get_sleep_data), ("stats", g.get_stats), ("hrv", g.get_hrv_data)):
            try:
                entry[name] = fn(k) or {}
            except Exception as e:  # one bad day shouldn't sink the run
                print(f"  garmin {name} {k}: {type(e).__name__}: {str(e)[:120]}")
                entry[name] = {}
            time.sleep(0.4)
        raw["days"][k] = entry
    if days:
        try:
            raw["activities"] = g.get_activities_by_date(days[0].isoformat(), days[-1].isoformat()) or []
        except Exception as e:
            print(f"  garmin activities: {type(e).__name__}: {str(e)[:120]}")
    return raw


def normalise_garmin(raw: dict, db: dict):
    for k, e in raw["days"].items():
        d = {}
        sl = e.get("sleep") or {}
        dto = sl.get("dailySleepDTO") or sl
        deep, light, rem = num(dto.get("deepSleepSeconds")), num(dto.get("lightSleepSeconds")), num(dto.get("remSleepSeconds"))
        if (deep or 0) + (light or 0) + (rem or 0) > 0:
            put(d, "deepMin", (deep or 0) / 60); put(d, "lightMin", (light or 0) / 60); put(d, "remMin", (rem or 0) / 60)
            put(d, "awakeMin", (num(dto.get("awakeSleepSeconds")) or 0) / 60)
            put(d, "sleepMin", ((deep or 0) + (light or 0) + (rem or 0)) / 60)
        put(d, "sleepScore", _first(dto, "sleepScores.overall.value", "sleepScores.overallScore"))
        put(d, "resp", _first(dto, "averageRespirationValue", "averageRespiration"))
        put(d, "spo2", _first(dto, "averageSpO2Value", "averageSPO2"))
        st = e.get("stats") or {}
        put(d, "steps", st.get("totalSteps")); put(d, "rhr", st.get("restingHeartRate"))
        put(d, "kcal", st.get("totalKilocalories")); put(d, "activeKcal", st.get("activeKilocalories"))
        stress = num(st.get("averageStressLevel"))
        if stress is not None and stress >= 0:
            put(d, "stress", stress)
        put(d, "bodyBattery", _first(st, "bodyBatteryHighestValue", "maxBodyBattery"))
        hrv = _first(e.get("hrv") or {}, "hrvSummary.lastNightAvg") or _first(sl, "avgOvernightHrv") or _first(dto, "avgOvernightHrv")
        put(d, "hrv", hrv)
        if d:
            db["daily"].setdefault(k, {})["garmin"] = d
    for a in raw["activities"]:
        start = a.get("startTimeGMT")
        if not start:
            continue
        s = datetime.fromisoformat(start.replace(" ", "T")).replace(tzinfo=timezone.utc)
        dur = num(a.get("duration")) or num(a.get("elapsedDuration")) or 0
        at = a.get("activityType") or {}
        db["workouts"].append({
            "src": "garmin", "id": f"g-{a.get('activityId')}", "start": int(s.timestamp() * 1000),
            "end": int(s.timestamp() * 1000 + dur * 1000), "name": a.get("activityName") or at.get("typeKey", "Activity"),
            "type": at.get("typeKey"), "durMin": round(dur / 60, 1), "distM": num(a.get("distance")),
            "kcal": num(a.get("calories")), "avgHr": num(a.get("averageHR")), "maxHr": num(a.get("maxHR")),
        })


# ---------------------------------------------------------------- merge history
def merge_into(history: dict, fresh: dict) -> dict:
    """Fresh readings replace stored ones for the same day/device; workouts dedupe by id."""
    for k, devs in fresh["daily"].items():
        slot = history["daily"].setdefault(k, {})
        for dev, vals in devs.items():
            slot[dev] = vals
    fresh_ids = {w["id"] for w in fresh["workouts"]}
    history["workouts"] = [w for w in history["workouts"] if w.get("id") not in fresh_ids] + fresh["workouts"]
    history["workouts"].sort(key=lambda w: w["start"])
    return history
