import requests
import re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://codedew.com/cdn/ziptron.php/?w0uQXBUgiC7svgD",
}

session = requests.Session()
session.headers.update(HEADERS)

# POST to liptron.php
post_url = "https://codedew.com/cdn/liptron.php/"
data = {"rtiwatch": "w0uQXBUgiC7svgD"}

r = session.post(post_url, data=data, timeout=30, allow_redirects=True)

output = []
output.append(f"POST Status: {r.status_code}")
output.append(f"Final URL: {r.url}")
output.append(f"Content-Type: {r.headers.get('content-type', '')}")
output.append(f"Content-Length: {len(r.text)}")

# Save raw
with open('final_page.html', 'w', encoding='utf-8') as f:
    f.write(r.text)
output.append("Saved to final_page.html")

# Check for redirects
output.append(f"\nRedirect history: {[h.url for h in r.history]}")

# Look for download links
output.append("\n=== All links in final page ===")
for m in re.finditer(r'href=["\']([^"\']+)["\']', r.text):
    href = m.group(1)
    if 'codedew' not in href and 'cloudflare' not in href and 'wsrv' not in href:
        output.append(f"  {href[:200]}")

# Look for quality markers
output.append("\n=== Quality/download patterns ===")
for m in re.finditer(r'.{0,80}(480p|720p|1080p|360p|download|mp4|mkv|\.mp4|\.mkv).{0,80}', r.text, re.IGNORECASE):
    output.append(f"  ...{m.group(0)[:200]}...")

# Extract all URLs
output.append("\n=== All HTTP URLs ===")
urls = re.findall(r'https?://[^\s"\'<>]+', r.text)
for u in set(urls):
    if 'cloudflare' not in u and 'wsrv' not in u:
        output.append(f"  {u[:200]}")

# Get visible text
from bs4 import BeautifulSoup
soup = BeautifulSoup(r.text, "lxml")
text = soup.get_text(separator="\n", strip=True)
output.append(f"\n=== Visible text (first 3000 chars) ===\n{text[:3000]}")

with open('final_page_analysis.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(output))
