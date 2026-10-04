import requests
import re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://codedew.com/cdn/liptron.php/",
}

session = requests.Session()
session.headers.update(HEADERS)

url = "https://argon.razorshell.space/downlead/w0uQXBUgiC7svgD/"
r = session.get(url, timeout=30, allow_redirects=True)

output = []
output.append(f"Status: {r.status_code}")
output.append(f"Final URL: {r.url}")
output.append(f"Content-Type: {r.headers.get('content-type', '')}")
output.append(f"Length: {len(r.text)}")

with open('argon_page.html', 'w', encoding='utf-8') as f:
    f.write(r.text)
output.append("Saved to argon_page.html")

output.append(f"\nRedirect history: {[h.url for h in r.history]}")

# Look for download links with quality
output.append("\n=== Links with quality/file patterns ===")
for m in re.finditer(r'.{0,100}(480p|720p|1080p|360p|\.mp4|\.mkv|download|file=|video).{0,100}', r.text, re.IGNORECASE):
    output.append(f"  ...{m.group(0)[:250]}...")

# All links
output.append("\n=== All <a href> links ===")
for m in re.finditer(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*(?:>(.*?)</a>)?', r.text, re.DOTALL):
    href = m.group(1)[:250]
    text = m.group(2)
    if text:
        text = re.sub(r'<[^>]+>', '', text).strip()[:60]
    output.append(f"  Href: {href}")
    if text:
        output.append(f"    Text: {text}")

# All HTTP URLs
output.append("\n=== All HTTP URLs ===")
urls = re.findall(r'https?://[^\s"\'<>]+', r.text)
for u in set(urls):
    output.append(f"  {u[:250]}")

# Look for iframes, video tags
output.append("\n=== iframes/video/audio ===")
for m in re.finditer(r'<(?:iframe|video|audio|source)[^>]*/?>', r.text):
    output.append(f"  {m.group(0)[:250]}")

# Visible text
from bs4 import BeautifulSoup
soup = BeautifulSoup(r.text, "lxml")
text = soup.get_text(separator="\n", strip=True)
output.append(f"\n=== Visible text ===\n{text[:3000]}")

with open('argon_analysis.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(output))
