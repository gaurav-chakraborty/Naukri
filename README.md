# Naukri Daily Automation — Self-Healing Edition

Automates daily Naukri profile refresh and resume upload to stay visible to recruiters. Upgraded from the original with **self-healing selectors**, **AI-powered recovery**, and **UI-change detection**.

---

## What It Does

1. **Daily profile refresh** — logs in, triggers a profile save so your profile shows "Updated today" to recruiters
2. **Resume re-upload** — uploads your resume daily; optionally injects hidden random text so Naukri treats it as a fresh upload (bypasses duplicate detection)
3. **Self-healing** — if Naukri changes their UI, the script auto-detects the change and tries 4–5 fallback strategies to find the element without any manual fix
4. **Gemini recovery** — if all fallbacks fail (major redesign), sends the page HTML to Gemini and asks it to return a working CSS selector automatically

---

## Setup

```bash
pip install -r requirements.txt
```

Edit `constants.py` (or set environment variables):

```python
USERNAME  = "your_naukri_email@example.com"
PASSWORD  = "your_naukri_password"
ORIGINAL_RESUME_PATH = "/path/to/Resume.pdf"
MODIFIED_RESUME_PATH = "/path/to/Resume_modified.pdf"
UPDATE_PDF = True   # inject hidden text to force fresh upload
HEADLESS   = True   # run without visible Chrome window
```

Run:

```bash
python naukri.py
```

---

## Self-Healing Architecture

### How It Works

Every interactive element (login form, resume upload, profile edit button) has a **strategy chain** — an ordered list of 4–5 ways to find it:

```
ID → NAME → CSS selector → XPATH text-match → Gemini AI
```

On first run, the script tries each strategy and **caches the working one** in `selector_cache.json`. On subsequent runs it tries the cached selector first. If the cached selector stops working (Naukri UI update), it:

1. Logs `UI_CHANGE_DETECTED` with the old selector
2. Tries all fallback strategies in order
3. Updates the cache with the new working selector
4. Logs `SELECTOR_HEALED` with the new selector

### Gemini Recovery (Optional)

Set `GEMINI_API_KEY` in `constants.py` or as an env var. When all 4–5 fallback strategies fail (major redesign), the script sends the page HTML to Gemini Flash and asks:

> "Return ONLY a valid CSS selector that uniquely identifies: the email/username input field on the login form."

If Gemini returns a working selector, it's cached and used going forward.

---

## Scheduling (macOS cron)

Run daily at 9 AM:

```bash
crontab -e
# Add:
0 9 * * * cd /path/to/naukri && python naukri.py >> naukri_cron.log 2>&1
```

---

## Logs

| File | Contents |
|---|---|
| `naukri.log` | Human-readable timestamped log |
| `naukri_events.jsonl` | Structured JSON events (one per line) — useful for monitoring |
| `selector_cache.json` | Cached working selectors — auto-updated on UI changes |

Key event types in `naukri_events.jsonl`:

| Event | Meaning |
|---|---|
| `UI_CHANGE_DETECTED` | Cached selector stopped working — Naukri updated their UI |
| `SELECTOR_HEALED` | Found a new working selector automatically |
| `GEMINI_RECOVERY` | Gemini AI found a selector after all fallbacks failed |
| `LOGIN_SUCCESS` / `LOGIN_FAILED` | Login outcome |
| `RESUME_UPLOAD_SUCCESS` | Upload verified via date marker |
| `RESUME_UPLOAD_UNVERIFIED` | Upload ran but date marker not confirmed |

---

## PDF Watermarking Trick

When `UPDATE_PDF = True`, the script adds hidden random text (font size 1–5pt, positioned at x=700–1000 which is off the visible page) to the last page of your resume before uploading. This makes the MD5 hash different each time, so Naukri treats it as a new document and boosts your visibility.

Your original resume is never modified — the watermarked copy is saved to `MODIFIED_RESUME_PATH`.
