import requests
from bs4 import BeautifulSoup
import json

BASE_URL = "https://www.rareanimes.mov"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

def search(q):
    url = f"{BASE_URL}/?s={q}"
    r = requests.get(url, headers=HEADERS, timeout=30)
    print(f"Search status: {r.status_code}, URL: {r.url}")
    soup = BeautifulSoup(r.text, "lxml")
    # Find post links
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if href.startswith(BASE_URL) and len(text) > 5:
            links.append((text[:80], href))
    # Deduplicate
    seen = set()
    unique = []
    for text, href in links:
        if href not in seen:
            seen.add(href)
            unique.append((text, href))
    print(f"\nFound {len(unique)} unique links")
    for text, href in unique[:30]:
        print(f"  {text} -> {href}")
    return unique

def fetch_page(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    print(f"\nPage status: {r.status_code}, URL: {r.url}")
    soup = BeautifulSoup(r.text, "lxml")
    
    # Check for ongoing/completed
    if "ongoing" in r.text.lower():
        print("Page mentions 'ongoing'")
    if "completed" in r.text.lower():
        print("Page mentions 'completed'")
    
    # Look for WatchMultQuality
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if "Watch" in text or "watch" in text.lower() or "quality" in text.lower() or "mult" in text.lower():
            print(f"  Watch/Quality link: {text} -> {href}")
    
    # Look for codedew links
    codedew_links = []
    for a in soup.find_all("a", href=True):
        if "codedew" in a["href"]:
            codedew_links.append((a.get_text(strip=True), a["href"]))
    for text, href in codedew_links:
        print(f"  Codedew link: {text} -> {href[:120]}")
    
    return soup

# Search for naruto
print("=" * 60)
print("Searching for 'naruto'")
results = search("naruto")

# Try fetching one of the post links
for text, href in results:
    if "shippuden" in text.lower() and "season 09" in text.lower():
        print(f"\n{'='*60}")
        print(f"Fetching: {href}")
        fetch_page(href)
        break
