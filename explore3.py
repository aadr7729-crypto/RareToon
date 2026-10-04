import requests
from bs4 import BeautifulSoup
import re
import sys

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# Redirect stdout to handle unicode
output_lines = []

def log(msg):
    output_lines.append(str(msg))
    # Also print with error handling
    try:
        print(msg)
    except:
        pass

# From the previous exploration, get a sample codedew link
test_url = "https://codedew.com/zipper/?url=eCltViTs0eZK5Pq806AmzjCdZUpHbuXpKD7yxjHHOMq9MS4fXUR3wCe3bPjYHlJxH9RjwXqGpK9rPc5NvUCq2hV3btKPBsFYfgZUNTpj1Z1BTne4"

log(f"Fetching codedew link: {test_url[:100]}...")
session = requests.Session()
session.headers.update(HEADERS)
r = session.get(test_url, timeout=30, allow_redirects=True)
log(f"Status: {r.status_code}")
log(f"Final URL: {r.url}")
log(f"Content length: {len(r.text)}")

soup = BeautifulSoup(r.text, "lxml")

log("\n=== All links with href ===")
for a in soup.find_all("a", href=True):
    text = a.get_text(strip=True)[:60]
    href = a["href"][:150]
    log(f"  Text: '{text}' -> {href}")

log("\n=== Forms ===")
for form in soup.find_all("form"):
    log(f"  Form action: {form.get('action', '')}, method: {form.get('method', '')}")
    for input_el in form.find_all("input"):
        log(f"    Input: name={input_el.get('name','')}, value={input_el.get('value','')[:50]}, type={input_el.get('type','')}")

log("\n=== Buttons (button tags) ===")
for btn in soup.find_all("button"):
    text = btn.get_text(strip=True)[:80]
    onclick = btn.get("onclick", "")[:100]
    log(f"  Button: '{text}' onclick={onclick}")

log("\n=== Script src URLs ===")
for script in soup.find_all("script", src=True):
    log(f"  Script: {script['src'][:150]}")

log("\n=== Inline scripts (non-empty, short) ===")
for script in soup.find_all("script"):
    if not script.get("src"):
        txt = script.get_text().strip()
        if txt and len(txt) < 500:
            log(f"  Inline: {txt[:300]}")

log("\n=== divs with class containing 'episode', 'download', 'quality' ===")
for div in soup.find_all(class_=re.compile(r"episode|download|quality|link", re.I)):
    text = div.get_text(strip=True)[:150]
    log(f"  Class={div.get('class')} Text: {text}")

# Check for direct video file links
log("\n=== Possible video/download links (extensions) ===")
for a in soup.find_all("a", href=True):
    href = a["href"]
    if any(ext in href.lower() for ext in [".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4a", "file="]):
        log(f"  {a.get_text(strip=True)[:60]} -> {href[:200]}")

# Look for data attributes
log("\n=== Elements with data-* attributes ===")
for el in soup.find_all(attrs={re.compile(r"data-")}):
    for k, v in el.attrs.items():
        if k.startswith("data-") and v:
            log(f"  {el.name}[{k}] = {str(v)[:100]}")

# Get page text
log("\n=== Visible text (first 80 lines) ===")
text = soup.get_text(separator="\n", strip=True)
lines = [l for l in text.split("\n") if l.strip()]
for line in lines[:80]:
    log(f"  {line[:150]}")

# Write output to file
with open("explore3_output.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(output_lines))
log("\n=== Output written to explore3_output.txt ===")
