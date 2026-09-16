# Body log: automatic Whoop + Garmin dashboard

Every morning a free GitHub Actions job pulls your latest Whoop and Garmin data, merges it with your history, encrypts it, and publishes your dashboard at `https://YOUR-USERNAME.github.io/REPO-NAME/`. You open that link, and it's already up to date.

- Runs at 7:30am and 11am Denver time in summer, 6:30am and 10am in winter. GitHub may start these runs 5–30 minutes late.
- Whoop data comes from Whoop's official developer API.
- Garmin data comes through `python-garminconnect`, an unofficial library. Garmin has no personal API, so this can break when Garmin changes its login. If that happens, the dashboard keeps showing your existing data and tells you the pull failed.
- The repo is public so GitHub Pages is free, but it holds no secrets and no readable data. Your data is published only as AES-256 encrypted text, and only your passphrase opens it.

Setup takes about 30–45 minutes, once.

---

## 1. Create the repo

1. Sign in at github.com (create a free account if needed). Click **+ → New repository**.
2. Name it something like `body-log`. Choose **Public** and create it.
3. Click **uploading an existing file** and drag in everything from this folder, then commit. Your file browser may hide the `.github` folder or skip it on upload. If it does, click **Add file → Create new file** and type `.github/workflows/sync.yml` as the name; the slashes create the folders. Paste in that file's contents and commit.
4. Go to **Settings → Pages**. Under *Build and deployment → Source*, choose **GitHub Actions**.

## 2. Create a Whoop developer app

1. Go to **developer.whoop.com** and sign in with your normal Whoop account. Open the Developer Dashboard and create a team if asked, then **Create App**.
2. Fill in:
   - **Name:** anything, e.g. "My body log"
   - **Redirect URL:** `http://localhost:8765/callback` (exactly)
   - **Scopes:** tick `offline`, `read:recovery`, `read:cycles`, `read:sleep`, `read:workout`, `read:profile`
   - **Privacy policy URL**, if required: your repo URL is fine
3. Save, then keep the **Client ID** and **Client Secret** handy.

## 3. Create a token that lets the job update its own logins

Whoop replaces your login token every time it's used, so the job needs permission to save the new one.

1. Go to github.com → your avatar → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Set **Repository access** to *Only select repositories* and pick your `body-log` repo.
3. Under **Permissions → Repository permissions**, set **Secrets** to *Read and write*.
4. Set an expiration, up to a year, and put a reminder in your calendar to renew it. Generate the token and copy it.
5. In your repo, go to **Settings → Secrets and variables → Actions → New repository secret**. Name it `SECRETS_PAT` and paste the token.

## 4. Run the one-time setup on your computer

You need Python 3.10 or newer (python.org). In a terminal, from this folder:

```bash
pip install -r requirements.txt
python setup_local.py
```

It will:
1. Ask for your Whoop Client ID and Secret, then open your browser so you can approve access.
2. Ask for your Garmin email, password and two-factor code, if you use one. Your password is never saved; only a login token is.
3. Do a quick test pull of the last 3 days and show you the counts.
4. Suggest a passphrase for the dashboard. **Save it in your password manager.**
5. Save everything as GitHub secrets:
   - If you have the GitHub CLI (`gh`) installed and logged in, it sets the secrets for you.
   - Otherwise it prints each secret. Add them one by one under **Settings → Secrets and variables → Actions**: `WHOOP_CLIENT_ID`, `WHOOP_CLIENT_SECRET`, `WHOOP_REFRESH_TOKEN`, `GARMIN_TOKENS`, `DASH_PASSPHRASE`.

> Don't run `setup_local.py` a second time without re-saving the secrets. Each run uses up the Whoop token stored in GitHub.

## 5. First run and backfill

1. In your repo, open the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. Choose **Sync Whoop + Garmin → Run workflow**. Enter `365` for days to backfill a year, then run it. A long backfill takes about 10–20 minutes, mostly waiting on Garmin.
3. When both jobs show green ticks, open `https://YOUR-USERNAME.github.io/body-log/`. Enter your passphrase and leave *Stay unlocked on this device* ticked.
4. On your phone, open the same link and use **Share → Add to Home Screen**.

From then on it updates itself every morning.

---

## How the numbers are merged

These are the same rules as before, and each one can be changed in the dashboard under **Source rules**:

- **Whoop first:** HRV, resting heart rate, sleep and respiratory rate, all measured during sleep.
- **Averaged:** calories and blood oxygen. Both devices are imprecise here, so averaging dampens each one's error.
- **Garmin only:** steps, Body Battery and stress.
- **Filling gaps:** when the main device misses a night, the other device fills in, shifted by the typical gap between them on nights both recorded. Filled days show as hollow dots.
- **Workouts on both devices:** merged into one row, with distance from Garmin, strain from Whoop, and calories averaged.
- **Today's steps and calories:** left out until the day is over, so a morning check doesn't show a half-finished day.

## If something breaks

The dashboard shows a yellow banner when the last pull failed. Open **Actions**, click the failed run, and expand *Pull data and build dashboard*.

| Error mentions | Fix |
|---|---|
| `Whoop refused the refresh token` | Run `python setup_local.py` again and let it re-save the secrets. This happens if the token-saving step failed, e.g. `SECRETS_PAT` expired. |
| Garmin `Authentication` / `401` / `429` | Run `setup_local.py` again to get a fresh Garmin token. If it keeps failing, Garmin probably changed its login. Run `pip install -U garminconnect`, and check the library's GitHub issues for a fix. |
| `Save rotated login tokens` step failed | `SECRETS_PAT` is missing, expired or lacks *Secrets: Read and write*. Fix it, then re-run setup. |
| Dashboard says no data published yet | The first workflow run hasn't finished, or Pages isn't set to *GitHub Actions* (step 1.4). |
| Wrong passphrase | It must match the `DASH_PASSPHRASE` secret. If you change that secret, the next run starts a fresh history, so run a backfill again. |

**Schedule pauses:** GitHub pauses scheduled workflows on public repos after 60 days without commits. The job makes an empty "keep-alive" commit every 45 days to prevent that.

## Files

- `sync.py`: the daily job
- `bodylog.py`: Whoop and Garmin fetching, normalising and encryption
- `setup_local.py`: one-time login helper
- `site/index.html`: the dashboard
- `.github/workflows/sync.yml`: the schedule
