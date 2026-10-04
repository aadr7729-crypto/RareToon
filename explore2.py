import requests
from bs4 import BeautifulSoup
import re

BASE_URL = "https://www.rareanimes.mov"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

def fetch_and_parse(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    print(f"Status: {r.status_code}, URL: {r.url}")
    soup = BeautifulSoup(r.text, "lxml")
    return r, soup

# Fetch a season page - try Naruto Shippuden Season 16
print("=" * 60)
print("Fetching Naruto Shippuden Season 16 page")
r, soup = fetch_and_parse("https://www.rareanimes.mov/hindi/naruto-shippuden-season-16-episodes-hindi-dubbed-download-hd/")

# Find all external links
print("\n--- External links (codedew) ---")
for a in soup.find_all("a", href=True):
    href = a["href"]
    if "codedew" in href:
        print(f"  Text: {a.get_text(strip=True)[:80]} -> {href[:120]}")

# Find all links with "Watch" or "Episode" or "S1" or "E1"
print("\n--- Links with Watch/Episode/S1/E1 ---")
for a in soup.find_all("a", href=True):
    text = a.get_text(strip=True)
    href = a["href"]
    if any(kw in text.lower() for kw in ["watch", "episode", "s1", "e1", "mult", "quality", "download", "720", "1080", "480"]):
        print(f"  Text: {text[:80]} -> {href[:150]}")

# Look for the article content
article = soup.find("article")
if article:
    print("\n--- Article content links ---")
    for a in article.find_all("a", href=True):
        print(f"  Text: {a.get_text(strip=True)[:80]} -> {a['href'][:150]}")
else:
    print("\nNo article tag found. Looking for content divs...")
    for div in soup.find_all(class_=re.compile(r"content|entry|post", re.I)):
        links = div.find_all("a", href=True)
        if len(links) > 2:
            print(f"  Div class={div.get('class')} has {len(links)} links:")
            for a in links[:20]:
                print(f"    Text: {a.get_text(strip=True)[:60]} -> {a['href'][:120]}")

# Also look for iframe or video players
print("\n--- iframes ---")
for iframe in soup.find_all("iframe"):
    print(f"  src: {iframe.get('src', '')[:200]}")

# Look for any div containing download links
print("\n--- Elements with 'download' in text ---")
for el in soup.find_all(string=re.compile(r"download", re.I)):
    parent = el.parent
    href = parent.get("href", "") if parent.name == "a" else parent.parent.get("href", "") if parent.parent and parent.parent.name == "a" else ""
    text = parent.get_text(strip=True)[:100]
    if href:
        print(f"  Text: {text} -> {href[:150]}")
