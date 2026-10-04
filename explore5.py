import requests
from bs4 import BeautifulSoup
import re
import base64
import urllib.parse

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

output = []
def log(msg):
    output.append(str(msg))

session = requests.Session()
session.headers.update(HEADERS)

# The download link for S16 E11
dl_url = "https://codedew.com/zipper/?url=lYoP1KHWD2nCqSnCKSxNPJc0oTx0%2BwtMUhwWRsVfL44ygqvMVIQaFjm1khNpipqS%2BHCYm6bU4tJidvWO91XwLWckv%2BG%2BWGuTmm35YPFh8Oc%3D"

log(f"Fetching download link...")
log(f"URL: {dl_url[:120]}")

r = session.get(dl_url, timeout=30, allow_redirects=True)
log(f"Status: {r.status_code}")
log(f"Final URL: {r.url}")
log(f"Content length: {len(r.text)}")

# Save full HTML
with open('download_page.html', 'w', encoding='utf-8') as f:
    f.write(r.text)
log("Saved to download_page.html")

# Try to decode the url param
url_param = "lYoP1KHWD2nCqSnCKSxNPJc0oTx0%2BwtMUhwWRsVfL44ygqvMVIQaFjm1khNpipqS%2BHCYm6bU4tJidvWO91XwLWckv%2BG%2BWGuTmm35YPFh8Oc%3D"
decoded = urllib.parse.unquote(url_param)
log(f"\nURL param (URL-decoded): {decoded}")
try:
    raw = base64.b64decode(decoded + "==")  # add padding
    log(f"Base64 decoded: {raw}")
    raw2 = base64.b64decode(decoded)
    log(f"Base64 decoded (no extra pad): {raw2}")
except Exception as e:
    log(f"Base64 decode failed: {e}")
    # Try with different padding
    for pad in ["", "=", "==", "==="]:
        try:
            raw = base64.b64decode(decoded + pad)
            log(f"Base64 decoded (pad={pad!r}): {raw[:200]}")
            break
        except:
            pass

# Parse the download page
soup = BeautifulSoup(r.text, "lxml")

log("\n=== All links ===")
for a in soup.find_all("a", href=True):
    text = a.get_text(strip=True)[:80]
    href = a["href"][:200]
    log(f"  Text: '{text}' -> {href}")

log("\n=== Forms ===")
for form in soup.find_all("form"):
    log(f"  Form action: {form.get('action', '')}, method: {form.get('method', '')}")
    for inp in form.find_all("input"):
        log(f"    Input: name={inp.get('name','')}, value={inp.get('value','')[:80]}, type={inp.get('type','')}")

log("\n=== Buttons ===")
for btn in soup.find_all("button"):
    text = btn.get_text(strip=True)[:80]
    onclick = btn.get("onclick", "")[:200]
    log(f"  Button: '{text}' onclick={onclick}")

log("\n=== Script src ===")
for script in soup.find_all("script", src=True):
    log(f"  {script.get('src', '')[:200]}")

log("\n=== Hidden inputs ===")
for inp in soup.find_all("input", type="hidden"):
    log(f"  name={inp.get('name','')} value={inp.get('value','')[:80]}")

# Check for download links at common CDNs
log("\n=== Looking for video file links / CDN URLs ===")
for m in re.finditer(r'https?://[^\s"\'<>]+', r.text):
    url = m.group(0)[:200]
    if any(kw in url.lower() for kw in [".mp4", ".mkv", ".mp3", ".m4a", "stream", "cdn", "download", "file"]):
        log(f"  {url}")

# Look for step-like text
log("\n=== Text containing 'step', 'generate', 'download', '480', '720', '1080' ===")
for m in re.finditer(r'.{0,50}(step|generate|download|480|720|1080|quality\s*link).{0,50}', r.text, re.IGNORECASE):
    log(f"  ...{m.group(0)[:150]}...")

# Save raw for manual review
log(f"\n=== Raw text (visible, first 2000 chars) ===")
text = soup.get_text(separator="\n", strip=True)
log(text[:2000])

with open('download_page_analysis.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(output))
log("\nDone - analysis saved to download_page_analysis.txt")
