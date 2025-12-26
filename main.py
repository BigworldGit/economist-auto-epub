import os
import re
import requests
import time
import smtplib
from email.message import EmailMessage

# ========= Secrets =========
API_KEY = os.getenv("API2CONVERT_KEY")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

# ========= 常量 =========
BASE_REPO_API = "https://api.github.com/repos/hehonghui/awesome-english-ebooks/contents/01_economist"
API_BASE = "https://api.api2convert.com/v2"
OUTPUT_FILE = "Economist_fixed.epub"

HEADERS = {
    "X-Oc-Api-Key": API_KEY,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0"
}

# ================== GitHub 部分 ==================

def get_latest_epub():
    resp = requests.get(BASE_REPO_API, timeout=30)
    resp.raise_for_status()
    folders = [x for x in resp.json() if x["type"] == "dir"]

    folders.sort(key=lambda x: x["name"])
    latest_folder = folders[-1]

    resp = requests.get(latest_folder["url"], timeout=30)
    resp.raise_for_status()

    epubs = []
    for f in resp.json():
        if f["name"].lower().endswith(".epub"):
            m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", f["name"])
            if m:
                date = datetime.strptime(m.group(0), "%Y.%m.%d")
                epubs.append((date, f))

    if not epubs:
        raise RuntimeError("未找到可解析日期的 epub 文件")

    epubs.sort(key=lambda x: x[0])
    latest = epubs[-1][1]

    return latest["download_url"], latest["name"]

# ================== 转换部分 ==================

def convert_epub(input_url):
    payload = {
        "input": [{"type": "remote", "source": input_url}],
        "conversion": [{"category": "ebook", "target": "epub"}]
    }

    r = requests.post(f"{API_BASE}/jobs", headers=HEADERS, json=payload)
    r.raise_for_status()
    job_id = r.json()["id"]

    for _ in range(100):
        time.sleep(3)
        r = requests.get(f"{API_BASE}/jobs/{job_id}", headers=HEADERS)
        r.raise_for_status()
        job = r.json()
        status = job["status"]["code"]
        if status in ("finished", "completed"):
            return job["output"][0]["uri"]
        if status == "error":
            raise RuntimeError("转换失败")

    raise TimeoutError("转换超时")

def download_file(url, filename):
    r = requests.get(url, stream=True)
    r.raise_for_status()
    with open(filename, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)

# ================== 邮件部分 ==================

def send_mail(filename):
    msg = EmailMessage()
    msg["Subject"] = "Economist Weekly EPUB"
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg.set_content("本周 Economist 已自动生成（保持原始文件名）")

    with open(filename, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="epub+zip",
            filename=filename
        )

    with smtplib.SMTP_SSL("smtp.126.com", 465) as s:
        s.login(EMAIL_USER, EMAIL_PASS)
        s.send_message(msg)

# ================== main ==================

def main():
    epub_url, epub_name = get_latest_epub()
    output_url = convert_epub(epub_url)
    download_file(output_url, epub_name)
    send_mail(epub_name)

if __name__ == "__main__":
    main()