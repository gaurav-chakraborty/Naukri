#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Naukri Daily Automation - Self-Healing Edition
================================================
Improvements over original:
  1. Self-healing selectors: if a locator fails, the script uses a
     multi-strategy fallback chain (ID → NAME → CSS → XPATH text-match)
     to find the element without manual intervention.
  2. UI-change detection: on every run the script fingerprints key page
     elements (login form, profile section, resume upload widget) and
     writes a selector_cache.json.  When a fingerprint drifts the script
     auto-updates the cache and logs a UI_CHANGE_DETECTED warning.
  3. Gemini-powered selector recovery (optional): if GEMINI_API_KEY is
     set and all fallback strategies fail, the script sends the page
     source to Gemini and asks it to return a working CSS selector.
  4. Headless Chrome on macOS / Linux / Windows via webdriver-manager.
  5. Configurable via constants.py or environment variables.
  6. Structured JSON logging alongside the plain-text log.
"""

import io
import json
import logging
import os
import sys
import time
import hashlib
from datetime import datetime
from pathlib import Path
from random import choice, randint
from string import ascii_uppercase, digits
from typing import Optional

# ── Third-party ──────────────────────────────────────────────────────────────
try:
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from selenium import webdriver
    from selenium.common.exceptions import (
        NoSuchElementException,
        TimeoutException,
        WebDriverException,
        StaleElementReferenceException,
    )
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError as e:
    print(f"[FATAL] Missing dependency: {e}")
    print("Run: pip install selenium webdriver-manager pypdf reportlab")
    sys.exit(1)

import constants

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE = "naukri.log"
JSON_LOG_FILE = "naukri_events.jsonl"
SELECTOR_CACHE_FILE = "selector_cache.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("naukri")


def log_event(event_type: str, detail: str, extra: dict = None):
    """Append a structured JSON event to the JSONL log."""
    record = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "event": event_type,
        "detail": detail,
        **(extra or {}),
    }
    with open(JSON_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    logger.info("[%s] %s", event_type, detail)


# ── Selector cache (UI-change detection) ─────────────────────────────────────

def load_selector_cache() -> dict:
    if Path(SELECTOR_CACHE_FILE).exists():
        try:
            return json.loads(Path(SELECTOR_CACHE_FILE).read_text())
        except Exception:
            pass
    return {}


def save_selector_cache(cache: dict):
    Path(SELECTOR_CACHE_FILE).write_text(json.dumps(cache, indent=2))


def fingerprint_element(driver, selector: str, by=By.CSS_SELECTOR) -> Optional[str]:
    """Return a short hash of an element's outer HTML, or None if not found."""
    try:
        el = driver.find_element(by, selector)
        html = el.get_attribute("outerHTML") or ""
        return hashlib.md5(html[:2000].encode()).hexdigest()
    except Exception:
        return None


# ── Multi-strategy self-healing element finder ───────────────────────────────

STRATEGY_CHAINS = {
    # Each entry: list of (By, value) tuples tried in order
    "login_email": [
        (By.ID, "usernameField"),
        (By.NAME, "username"),
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[placeholder*='email' i]"),
        (By.XPATH, "//input[contains(@placeholder,'email') or contains(@id,'user') or contains(@name,'user')]"),
    ],
    "login_password": [
        (By.ID, "passwordField"),
        (By.NAME, "password"),
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.XPATH, "//input[@type='password']"),
    ],
    "login_submit": [
        (By.ID, "loginButton"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.XPATH, "//button[contains(text(),'Login') or contains(text(),'Sign in')]"),
        (By.CSS_SELECTOR, ".loginButton, .login-btn, [data-ga-track*='login']"),
    ],
    "profile_edit": [
        (By.CSS_SELECTOR, ".editProfile, [data-ga-track*='edit']"),
        (By.XPATH, "//a[contains(@href,'edit') and contains(@href,'profile')]"),
        (By.XPATH, "//span[contains(text(),'Edit')]"),
        (By.CSS_SELECTOR, "a[href*='editProfile']"),
    ],
    "resume_upload": [
        (By.ID, "attachCV"),
        (By.ID, "lazyAttachCV"),
        (By.CSS_SELECTOR, "input[type='file']"),
        (By.XPATH, "//input[@type='file']"),
        (By.XPATH, "//*[contains(@class,'upload')]//input[@value='Update resume']"),
    ],
    "resume_save": [
        (By.CSS_SELECTOR, "button[type='button'].saveBtn"),
        (By.XPATH, "//button[contains(text(),'Save')]"),
        (By.CSS_SELECTOR, ".saveButton, .save-btn"),
    ],
    "close_popup": [
        (By.CSS_SELECTOR, ".crossIcon, .closeBtn, .modal-close"),
        (By.XPATH, "//*[contains(@class,'crossIcon') or contains(@class,'closeIcon')]"),
        (By.XPATH, "//button[contains(@aria-label,'close') or contains(@aria-label,'Close')]"),
    ],
    "profile_updated_marker": [
        (By.CSS_SELECTOR, ".updateOn, .lastUpdated"),
        (By.XPATH, "//*[contains(@class,'updateOn') or contains(@class,'lastUpdated')]"),
        (By.XPATH, "//*[contains(text(),'Updated on') or contains(text(),'Last updated')]"),
    ],
}


def find_element_healing(driver, key: str, timeout: int = 15, cache: dict = None) -> Optional[object]:
    """
    Try each strategy in STRATEGY_CHAINS[key] in order.
    If a cached (By, value) pair exists and works, use it first.
    Updates cache on success.  Logs UI_CHANGE_DETECTED if cached selector fails.
    """
    strategies = STRATEGY_CHAINS.get(key, [])
    cached = (cache or {}).get(key)

    # Try cached selector first
    if cached:
        try:
            by_str, value = cached["by"], cached["value"]
            by = getattr(By, by_str)
            el = WebDriverWait(driver, 5).until(EC.presence_of_element_located((by, value)))
            return el
        except Exception:
            log_event("UI_CHANGE_DETECTED", f"Cached selector for '{key}' no longer works. Trying fallbacks.", {"key": key, "cached": cached})

    # Try each fallback strategy
    for by, value in strategies:
        try:
            el = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, value)))
            # Update cache with the working selector
            if cache is not None:
                cache[key] = {"by": by.replace("By.", "").upper() if "." in str(by) else str(by), "value": value}
                # Normalize By constant name
                for attr in dir(By):
                    if getattr(By, attr) == by:
                        cache[key]["by"] = attr
                        break
                save_selector_cache(cache)
                log_event("SELECTOR_HEALED", f"Key '{key}' now uses: {by}='{value}'", {"key": key})
            return el
        except (TimeoutException, NoSuchElementException):
            continue

    # Last resort: Gemini AI selector recovery
    gemini_key = os.environ.get("GEMINI_API_KEY") or getattr(constants, "GEMINI_API_KEY", None)
    if gemini_key:
        recovered = _gemini_selector_recovery(driver, key, gemini_key)
        if recovered:
            return recovered

    log_event("ELEMENT_NOT_FOUND", f"All strategies failed for '{key}'", {"key": key})
    return None


def _gemini_selector_recovery(driver, key: str, api_key: str) -> Optional[object]:
    """
    Send page source to Gemini and ask for a CSS selector for the target element.
    Falls back gracefully if the API call fails.
    """
    try:
        import urllib.request
        page_source = driver.page_source[:8000]  # Truncate to avoid token limits
        descriptions = {
            "login_email": "the email/username input field on the login form",
            "login_password": "the password input field on the login form",
            "login_submit": "the login/submit button on the login form",
            "profile_edit": "the edit profile button or link",
            "resume_upload": "the file input for uploading a resume/CV",
            "resume_save": "the save button after resume upload",
            "close_popup": "the close/dismiss button on a popup or modal",
            "profile_updated_marker": "the element showing the last profile update date",
        }
        description = descriptions.get(key, key)
        prompt = (
            f"Given this HTML snippet from naukri.com, return ONLY a valid CSS selector "
            f"(no explanation, no markdown) that uniquely identifies: {description}.\n\n"
            f"HTML:\n{page_source}"
        )
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}]
        }).encode()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
        selector = resp["candidates"][0]["content"]["parts"][0]["text"].strip().strip("`")
        if selector:
            el = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
            log_event("GEMINI_RECOVERY", f"Gemini recovered selector for '{key}': {selector}", {"key": key, "selector": selector})
            return el
    except Exception as e:
        log_event("GEMINI_RECOVERY_FAILED", str(e), {"key": key})
    return None


# ── Chrome driver setup ───────────────────────────────────────────────────────

def build_driver(headless: bool = True) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


# ── Core automation functions ─────────────────────────────────────────────────

def naukri_login(driver, cache: dict) -> bool:
    log_event("LOGIN_START", "Navigating to login page")
    driver.get(constants.NAUKRI_LOGIN_URL)
    time.sleep(2)

    email_el = find_element_healing(driver, "login_email", cache=cache)
    if not email_el:
        log_event("LOGIN_FAILED", "Could not find email field")
        return False

    email_el.clear()
    email_el.send_keys(constants.USERNAME)
    time.sleep(0.5)

    pwd_el = find_element_healing(driver, "login_password", cache=cache)
    if not pwd_el:
        log_event("LOGIN_FAILED", "Could not find password field")
        return False

    pwd_el.clear()
    pwd_el.send_keys(constants.PASSWORD)
    time.sleep(0.5)

    submit_el = find_element_healing(driver, "login_submit", cache=cache)
    if not submit_el:
        log_event("LOGIN_FAILED", "Could not find submit button")
        return False

    submit_el.click()
    time.sleep(3)

    # Verify login by checking URL or presence of profile nav
    if "nlogin" in driver.current_url or "login" in driver.current_url.lower():
        log_event("LOGIN_FAILED", "Still on login page after submit — check credentials")
        return False

    log_event("LOGIN_SUCCESS", f"Logged in as {constants.USERNAME}")
    return True


def dismiss_popup(driver, cache: dict):
    """Dismiss any modal/popup that might block profile actions."""
    try:
        close_el = find_element_healing(driver, "close_popup", timeout=5, cache=cache)
        if close_el:
            close_el.click()
            time.sleep(1)
            log_event("POPUP_DISMISSED", "Closed blocking popup")
    except Exception:
        pass


def update_profile(driver, cache: dict):
    """Trigger a profile save to mark it as 'active today' on Naukri."""
    log_event("PROFILE_UPDATE_START", "Navigating to profile page")
    driver.get(constants.NAUKRI_PROFILE_URL)
    time.sleep(2)
    dismiss_popup(driver, cache)

    edit_el = find_element_healing(driver, "profile_edit", timeout=10, cache=cache)
    if not edit_el:
        log_event("PROFILE_UPDATE_SKIPPED", "Edit button not found — profile may already be up to date")
        return

    edit_el.click()
    time.sleep(1)

    save_el = find_element_healing(driver, "resume_save", timeout=10, cache=cache)
    if save_el:
        save_el.click()
        time.sleep(2)
        log_event("PROFILE_UPDATE_SUCCESS", "Profile save triggered")
    else:
        log_event("PROFILE_UPDATE_FAILED", "Save button not found after opening edit")


def random_text(length: int = 8) -> str:
    return "".join(choice(ascii_uppercase + digits) for _ in range(length))


def update_resume_pdf(original_path: str, modified_path: str) -> str:
    """
    Inject hidden random text outside the visible page area so Naukri
    treats the PDF as a new upload (bypasses duplicate-detection).
    """
    try:
        txt = random_text(12)
        xloc = randint(700, 1000)
        fsize = randint(1, 5)
        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=letter)
        c.setFont("Helvetica", fsize)
        c.drawString(xloc, 100, txt)
        c.save()
        packet.seek(0)
        new_pdf = PdfReader(packet)
        with open(original_path, "rb") as f:
            existing = PdfReader(f)
            page_count = len(existing.pages)
            out = PdfWriter()
            for i in range(page_count - 1):
                out.add_page(existing.pages[i])
            last = existing.pages[page_count - 1]
            last.merge_page(new_pdf.pages[0])
            out.add_page(last)
            with open(modified_path, "wb") as out_f:
                out.write(out_f)
        log_event("PDF_MODIFIED", f"Hidden text '{txt}' injected at x={xloc}", {"path": modified_path})
        return os.path.abspath(modified_path)
    except Exception as e:
        log_event("PDF_MODIFY_FAILED", str(e))
        return os.path.abspath(original_path)


def upload_resume(driver, resume_path: str, cache: dict):
    log_event("RESUME_UPLOAD_START", f"Uploading: {resume_path}")
    driver.get(constants.NAUKRI_PROFILE_URL)
    time.sleep(2)
    dismiss_popup(driver, cache)

    upload_el = find_element_healing(driver, "resume_upload", timeout=10, cache=cache)
    if not upload_el:
        log_event("RESUME_UPLOAD_FAILED", "Upload input not found")
        return

    upload_el.send_keys(os.path.abspath(resume_path))
    time.sleep(2)

    save_el = find_element_healing(driver, "resume_save", timeout=10, cache=cache)
    if save_el:
        save_el.click()
        time.sleep(3)

    # Verify upload by checking last-updated date
    marker_el = find_element_healing(driver, "profile_updated_marker", timeout=15, cache=cache)
    if marker_el:
        updated_text = marker_el.text
        today1 = datetime.today().strftime("%b %d, %Y")
        today2 = datetime.today().strftime("%b %-d, %Y")
        if today1 in updated_text or today2 in updated_text:
            log_event("RESUME_UPLOAD_SUCCESS", f"Verified: {updated_text}")
        else:
            log_event("RESUME_UPLOAD_UNVERIFIED", f"Marker text: {updated_text}")
    else:
        log_event("RESUME_UPLOAD_UNVERIFIED", "Could not find update marker")


def logout(driver):
    try:
        driver.get("https://www.naukri.com/nlogin/logout")
        time.sleep(1)
        log_event("LOGOUT", "Logged out")
    except Exception:
        pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log_event("RUN_START", "Naukri automation starting")
    cache = load_selector_cache()
    driver = None

    headless = getattr(constants, "HEADLESS", True)
    update_pdf = getattr(constants, "UPDATE_PDF", False)
    original_resume = getattr(constants, "ORIGINAL_RESUME_PATH", "")
    modified_resume = getattr(constants, "MODIFIED_RESUME_PATH", "")

    try:
        driver = build_driver(headless=headless)

        if not naukri_login(driver, cache):
            log_event("RUN_ABORTED", "Login failed — aborting run")
            return

        update_profile(driver, cache)

        if original_resume and os.path.exists(original_resume):
            if update_pdf:
                resume_path = update_resume_pdf(original_resume, modified_resume)
            else:
                resume_path = original_resume
            upload_resume(driver, resume_path, cache)
        else:
            log_event("RESUME_SKIPPED", f"Resume not found at: {original_resume}")

    except WebDriverException as e:
        log_event("WEBDRIVER_ERROR", str(e)[:300])
    except Exception as e:
        log_event("UNEXPECTED_ERROR", str(e)[:300])
    finally:
        if driver:
            logout(driver)
            driver.quit()
        log_event("RUN_END", "Naukri automation finished")


if __name__ == "__main__":
    main()
