import requests
import re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

output_lines = []
def log(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(repr(msg))
    output_lines.append(str(msg))

session = requests.Session()
session.headers.update(HEADERS)

# Fetch the multiquality page directly
multiquality_url = "https://codedew.com/multiquality/?url=w0uQXBUgiC7svgD"
log(f"Fetching: {multiquality_url}")
r = session.get(multiquality_url, timeout=30, allow_redirects=True)
log(f"Status: {r.status_code}, Final URL: {r.url}, Length: {len(r.text)}")

# Save raw HTML
with open("multiquality_raw.html", "w", encoding="utf-8") as f:
    f.write(r.text)
log("Raw HTML saved to multiquality_raw.html")

# Look for JSON data, API endpoints, embedded data
log("\n=== Looking for API endpoints ===")
for m in re.finditer(r'["\']?(\/api\/[^ "\']+)["\']?', r.text):
    log(f"  API: {m.group(1)}")

log("\n=== Looking for URLs with zipper ===")
for m in re.finditer(r'https?://codedew\.com/zipper/\?url=[^ "\'<]+', r.text):
    log(f"  Zipper URL: {m.group(0)[:120]}")

log("\n=== Looking for URLs with multiquality ===")
for m in re.finditer(r'https?://codedew\.com/multiquality/\?url=[^ "\'<]+', r.text):
    log(f"  Multiquality URL: {m.group(0)[:120]}")

log("\n=== Looking for fetch/XHR/AJAX calls ===")
for m in re.finditer(r'(fetch|XMLHttpRequest|axios|\.ajax)\s*\(?\s*["\']([^"\']+)["\']?', r.text):
    log(f"  XHR: {m.group(0)[:150]}")

log("\n=== Looking for data-url or data-id attributes ===")
for m in re.finditer(r'data-(?:url|id|episode|link)=["\']([^"\']+)["\']', r.text):
    log(f"  data-attr: {m.group(0)[:120]}")

log("\n=== Looking for script src ===")
for m in re.finditer(r'<script[^>]*src=["\']([^"\']+)["\']', r.text):
    log(f"  Script src: {m.group(1)[:150]}")

log("\n=== Looking for base64 or encoded strings ===")
# The url param seems to be a short string like "w0uQXBUgiC7svgD"
log(f"  url param value: w0uQXBUgiC7svgD (from original)")
# Check if there's a pattern in the url values
for m in re.finditer(r'url=([A-Za-z0-9%+/=_-]+)', r.text):
    val = m.group(1)
    log(f"  url param: {val[:60]}")

log("\n=== Inline JS blocks (first 5) ===")
scripts = re.findall(r'<script[^>]*>(.*?)</script>', r.text, re.DOTALL)
for i, script in enumerate(scripts):
    script = script.strip()
    if script and len(script) > 20:
        log(f"  Script {i}: {script[:300]}")

log("\n=== Meta tags ===")
for m in re.finditer(r'<meta[^>]+>', r.text):
    log(f"  {m.group(0)[:200]}")

# Look for the episode listing structure - maybe in JS variables
log("\n=== Looking for episode data in JS ===")
for m in re.finditer(r'(season|episode|eps|S\d\d)\s*[=:]\s*["\']?(\d+)', r.text, re.IGNORECASE):
    log(f"  JS var: {m.group(0)[:100]}")

# Check if there's a Next.js or Vue/React data object
log("\n=== Looking for __NEXT_DATA__ or similar ===")
for m in re.finditer(r'window\.__([A-Z_]+)=', r.text):
    log(f"  Window var: {m.group(0)[:100]}")

log("\n=== Looking for JSON-LD or application/json ===")
for m in re.finditer(r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>', r.text, re.DOTALL):
    log(f"  JSON script: {m.group(1)[:300]}")
