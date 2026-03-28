# ─────────────────────────────────────────────────────────────────────────────
# Naukri Automation - Configuration
# ─────────────────────────────────────────────────────────────────────────────
# Fill in your credentials and paths below.
# All values can also be overridden via environment variables with the same name.
# ─────────────────────────────────────────────────────────────────────────────
import os

# ── Credentials ───────────────────────────────────────────────────────────────
USERNAME  = os.environ.get("NAUKRI_USERNAME", "your_naukri_email@example.com")
PASSWORD  = os.environ.get("NAUKRI_PASSWORD", "your_naukri_password")
MOBILE    = os.environ.get("NAUKRI_MOBILE",   "your_10_digit_mobile")

# ── Resume paths ──────────────────────────────────────────────────────────────
# ORIGINAL_RESUME_PATH: your actual resume PDF (never modified)
# MODIFIED_RESUME_PATH: where the watermarked copy is saved before upload
ORIGINAL_RESUME_PATH = os.environ.get("ORIGINAL_RESUME_PATH", "/path/to/your/Resume.pdf")
MODIFIED_RESUME_PATH = os.environ.get("MODIFIED_RESUME_PATH", "/path/to/modified/Resume_modified.pdf")

# ── Behaviour flags ───────────────────────────────────────────────────────────
# UPDATE_PDF: inject hidden random text to force Naukri to treat it as a new upload
UPDATE_PDF = os.environ.get("UPDATE_PDF", "false").lower() == "true"

# HEADLESS: run Chrome without a visible window (True recommended for cron/automation)
HEADLESS   = os.environ.get("HEADLESS", "true").lower() == "true"

# ── Naukri URLs ───────────────────────────────────────────────────────────────
NAUKRI_LOGIN_URL   = "https://www.naukri.com/nlogin/login"
NAUKRI_PROFILE_URL = "https://www.naukri.com/mnjuser/profile"

# ── Optional: Gemini API key for AI-powered selector recovery ─────────────────
# When all fallback strategies fail to find a page element (e.g. after a Naukri
# UI redesign), the script sends the page HTML to Gemini and asks it to return
# a working CSS selector automatically.
# Leave empty to disable this feature.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
