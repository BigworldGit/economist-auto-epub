import os
import re
import smtplib
import time
from datetime import datetime, time as clock_time, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ========= Secrets =========
API_KEY = os.getenv("API2CONVERT_KEY")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")
ISSUE_DATE = os.getenv("ISSUE_DATE", "").strip()

# ========= Constants =========
SOURCE_REPO = "hehonghui/awesome-english-ebooks"
SOURCE_PATH = "01_economist"
BASE_REPO_API = f"https://api.github.com/repos/{SOURCE_REPO}/contents/{SOURCE_PATH}"
COMMITS_API = f"https://api.github.com/repos/{SOURCE_REPO}/commits"
API_BASE = "https://api.api2convert.com/v2"
ISSUE_FOLDER_PATTERN = re.compile(r"^te_(\d{4}\.\d{2}\.\d{2})$")
SHANGHAI = ZoneInfo("Asia/Shanghai")

HEADERS = {
    "X-Oc-Api-Key": API_KEY,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0",
}


# ================== Issue discovery ==================

def parse_issue_date(issue_date):
    normalized = issue_date.replace("-", ".").strip()
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", normalized):
        raise ValueError("ISSUE_DATE must be YYYY.MM.DD or YYYY-MM-DD")
    return datetime.strptime(normalized, "%Y.%m.%d").date()


def determine_target_issue_date(issue_date=""):
    if issue_date:
        return parse_issue_date(issue_date)

    today = datetime.now(SHANGHAI).date()
    days_until_saturday = (5 - today.weekday()) % 7
    return today + timedelta(days=days_until_saturday)


def format_issue_date(issue_date):
    return issue_date.strftime("%Y.%m.%d")


def scheduled_trigger_time(issue_date):
    days_since_friday = (issue_date.weekday() - 4) % 7
    trigger_date = issue_date - timedelta(days=days_since_friday)
    return datetime.combine(trigger_date, clock_time(18, 0), tzinfo=SHANGHAI)


def list_issue_folders():
    response = requests.get(BASE_REPO_API, timeout=30)
    response.raise_for_status()

    folders = []
    for item in response.json():
        match = ISSUE_FOLDER_PATTERN.fullmatch(item["name"])
        if item["type"] == "dir" and match:
            folders.append({
                "issue_date": parse_issue_date(match.group(1)),
                "url": item["url"],
            })

    return sorted(folders, key=lambda item: item["issue_date"])


def get_issue_publication_time(issue_date):
    path = f"{SOURCE_PATH}/te_{format_issue_date(issue_date)}"
    response = requests.get(
        COMMITS_API,
        params={"path": path, "per_page": 100},
        timeout=30,
    )
    response.raise_for_status()

    commits = response.json()
    while response.links.get("next"):
        response = requests.get(response.links["next"]["url"], timeout=30)
        response.raise_for_status()
        commits.extend(response.json())

    if not commits:
        raise RuntimeError(f"No publication commit found for {format_issue_date(issue_date)}")

    oldest_commit_time = commits[-1]["commit"]["committer"]["date"]
    return datetime.fromisoformat(oldest_commit_time.replace("Z", "+00:00"))


def select_issue_folders_for_run(folders, target_issue_date, publication_time_loader):
    folders_by_date = {folder["issue_date"]: folder for folder in folders}
    previous_issue_date = target_issue_date - timedelta(days=7)
    selected = []

    previous_folder = folders_by_date.get(previous_issue_date)
    if previous_folder:
        published_at = publication_time_loader(previous_issue_date)
        if published_at > scheduled_trigger_time(previous_issue_date):
            selected.append(previous_folder)

    current_folder = folders_by_date.get(target_issue_date)
    if current_folder:
        selected.append(current_folder)

    return selected


def get_epub_from_folder(folder):
    response = requests.get(folder["url"], timeout=30)
    response.raise_for_status()

    epubs = []
    for item in response.json():
        if not item["name"].lower().endswith(".epub"):
            continue

        match = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", item["name"])
        if match:
            epub_date = datetime.strptime(match.group(0), "%Y.%m.%d")
            epubs.append((epub_date, item))

    if not epubs:
        raise RuntimeError(
            f"No dated EPUB found for issue {format_issue_date(folder['issue_date'])}"
        )

    epubs.sort(key=lambda item: item[0])
    latest = epubs[-1][1]
    return latest["download_url"], latest["name"]


# ================== Conversion ==================

def convert_epub(input_url):
    payload = {
        "input": [{"type": "remote", "source": input_url}],
        "conversion": [{"category": "ebook", "target": "epub"}],
    }

    response = requests.post(f"{API_BASE}/jobs", headers=HEADERS, json=payload)
    response.raise_for_status()
    job_id = response.json()["id"]

    for _ in range(100):
        time.sleep(3)
        response = requests.get(f"{API_BASE}/jobs/{job_id}", headers=HEADERS)
        response.raise_for_status()
        job = response.json()
        status = job["status"]["code"]
        if status in ("finished", "completed"):
            return job["output"][0]["uri"]
        if status == "error":
            raise RuntimeError("EPUB conversion failed")

    raise TimeoutError("EPUB conversion timed out")


def download_file(url, filename):
    response = requests.get(url, stream=True)
    response.raise_for_status()
    with open(filename, "wb") as output:
        for chunk in response.iter_content(8192):
            output.write(chunk)


# ================== Email ==================

def send_mail(attachments, issue_dates):
    msg = EmailMessage()
    formatted_dates = [format_issue_date(issue_date) for issue_date in issue_dates]
    suffix = ", ".join(formatted_dates)
    msg["Subject"] = f"Economist Weekly File - {suffix}"
    msg["From"] = EMAIL_USER

    valid_emails = []
    if EMAIL_TO:
        valid_emails = [
            email.strip()
            for email in re.split(r"[,\n\r]+", EMAIL_TO)
            if email.strip()
        ]
    msg["To"] = ", ".join(valid_emails)

    if len(formatted_dates) == 1:
        body = f"The Economist issue {formatted_dates[0]} is attached."
    else:
        body = (
            "The latest Economist issues are attached, including the issue that "
            f"was published after last week's check: {suffix}."
        )
    msg.set_content(body)

    for filename in attachments:
        with open(filename, "rb") as attachment:
            msg.add_attachment(
                attachment.read(),
                maintype="application",
                subtype="epub+zip",
                filename=Path(filename).name,
            )

    with smtplib.SMTP_SSL("smtp.126.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)


# ================== Main ==================

def main():
    target_issue_date = determine_target_issue_date(ISSUE_DATE)
    folders = list_issue_folders()
    selected_folders = select_issue_folders_for_run(
        folders,
        target_issue_date,
        get_issue_publication_time,
    )

    if not selected_folders:
        print(
            "The current issue is not available and there is no late-published "
            "issue from last week to catch up."
        )
        return

    attachments = []
    issue_dates = []
    for folder in selected_folders:
        issue_date = folder["issue_date"]
        epub_url, epub_name = get_epub_from_folder(folder)
        converted_url = convert_epub(epub_url)
        download_file(converted_url, epub_name)
        attachments.append(epub_name)
        issue_dates.append(issue_date)

    send_mail(attachments, issue_dates)
    print(f"Sent Economist issues: {', '.join(map(format_issue_date, issue_dates))}")


if __name__ == "__main__":
    main()
