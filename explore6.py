import requests
from bs4 import BeautifulSoup
import re

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

# Step 1: The download link from multiquality page
step1_url = "https://codedew.com/zipper/?url=lYoP1KHWD2nCqSnCKSxNPJc0oTx0%2BwtMUhwWRsVfL44ygqvMVIQaFjm1khNpipqS%2BHCYm6bU4tJidvWO91XwLWckv%2BG%2BWGuTmm35YPFh8Oc%3D"

log("=== STEP 1: Initial download link ===")
r = session.get(step1_url, timeout=30, allow_redirects=True)
log(f"Status: {r.status_code}, Final URL: {r.url}, Length: {len(r.text)}")

# Parse the page to find data-href (the next URL)
soup = BeautifulSoup(r.text, "lxml")
btn = soup.find("a", id="goBtn") or soup.find("a", {"data-href": True})
if btn:
    data_href = btn.get("data-href", "")
    log(f"data-href: {data_href}")
    
    # Follow to step 2
    step2_url = "https://codedew.com" + data_href
    # But check if it's already absolute
    if not data_href.startswith("http"):
        step2_url = "https://codedew.com" + data_href
    
    log(f"\n=== STEP 2: Following data-href ===")
    log(f"URL: {step2_url[:150]}")
    r2 = session.get(step2_url, timeout=30, allow_redirects=True)
    log(f"Status: {r2.status_code}, Final URL: {r2.url}, Length: {len(r2.text)}")
    
    # Save step 2 page
    with open('step2.html', 'w', encoding='utf-8') as f:
        f.write(r2.text)
    
    soup2 = BeautifulSoup(r2.text, "lxml")
    
    # Find next button/data-href
    btn2 = soup2.find("a", {"data-href": True})
    if btn2:
        data_href2 = btn2.get("data-href", "")
        log(f"step2 data-href: {data_href2}")
        step3_url = "https://codedew.com" + data_href2 if not data_href2.startswith("http") else data_href2
        
        log(f"\n=== STEP 3: Following step2 data-href ===")
        log(f"URL: {step3_url[:150]}")
        r3 = session.get(step3_url, timeout=30, allow_redirects=True)
        log(f"Status: {r3.status_code}, Final URL: {r3.url}, Length: {len(r3.text)}")
        
        with open('step3.html', 'w', encoding='utf-8') as f:
            f.write(r3.text)
        
        soup3 = BeautifulSoup(r3.text, "lxml")
        
        log("\n=== STEP 3: All links ===")
        for a in soup3.find_all("a", href=True):
            text = a.get_text(strip=True)[:80]
            href = a["href"][:200]
            log(f"  Text: '{text}' -> {href}")
        
        log("\n=== STEP 3: Looking for download/quality links ===")
        for m in re.finditer(r'.{0,30}(480p|720p|1080p|360p|download|mp4|mkv).{0,30}', r3.text, re.IGNORECASE):
            log(f"  ...{m.group(0)[:150]}...")
    else:
        log("No data-href found in step 2")
        log("\n=== STEP 2: All data-href attributes ===")
        for el in soup2.find_all(attrs={"data-href": True}):
            dh = el.get("data-href", "")
            log(f"  data-href: {dh[:200]}")
        
        log("\n=== STEP 2: All links ===")
        for a in soup2.find_all("a", href=True):
            text = a.get_text(strip=True)[:60]
            href = a["href"][:200]
            log(f"  Text: '{text}' -> {href}")
        
        log("\n=== STEP 2: All <a> with onclick ===")
        for a in soup2.find_all("a", onclick=True):
            log(f"  onclick: {a.get('onclick','')[:200]}")
            log(f"  href: {a.get('href','')[:200]}")
            log(f"  text: {a.get_text(strip=True)[:60]}")
else:
    log("No goBtn found in step 1")
    log("\n=== All data-href attributes ===")
    for el in soup.find_all(attrs={"data-href": True}):
        log(f"  {el.get('data-href', '')[:200]}")

with open('download_flow.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(output))
log("\nDone - output saved to download_flow.txt")
