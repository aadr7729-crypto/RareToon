import requests
import re
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

session = requests.Session()
session.headers.update(HEADERS)

output = []
def log(msg):
    output.append(str(msg))

# Fetch application.js to understand API call structure
js_url = "https://argon.razorshell.space/assets/front/scripts/application.js?cb=4274585359"
log(f"Fetching application.js...")
r = session.get(js_url, timeout=30)
log(f"Status: {r.status_code}, Length: {len(r.text)}")

# Save JS
with open('application.js', 'w', encoding='utf-8') as f:
    f.write(r.text)

# Look for API call patterns
log("\n=== fetch/axios/ajax calls in JS ===")
for m in re.finditer(r'(fetch|axios|\.ajax|XMLHttpRequest|\.get|\.post|open\()\s*\(?\s*["\']([^"\']+)["\']?', r.text):
    log(f"  {m.group(0)[:200]}")

log("\n=== Looking for 'token' usage ===")
for m in re.finditer(r'token.{0,100}', r.text):
    log(f"  ...{m.group(0)[:150]}...")

log("\n=== Looking for 'Authorization' or header patterns ===")
for m in re.finditer(r'(Authorization|Bearer|x-token|api.?key|X-){0,1}["\']?\s*[:=]["\']?\s*[^,\n]{0,100}', r.text, re.IGNORECASE):
    log(f"  ...{m.group(0)[:150]}...")

# Now try the API calls
log("\n\n=== Trying API calls ===")

ping_route = "/api/videos/AmNlZ2IuZQLkZGH5ZGSwMQt4LJMyAmZ4AGyyAJL0LmH0ZGAuLJHjLGMwBGqzZGD0LGDlMQLkAmVlBQAuZmL4MF5sGHyOMKOdImD0p09cEmSJJKN3ImqOYz92pRA1BRubq3qQn2HmpUqgn1EhnRR/ZQZ2ZwAwAQL2BQqyLwR5BJRmMGt2LmyvLwplL2WwLGZ0LGyyBTDjAmt2ZzV2AmH4AwZlAwMuBJH5AQMwMGtlZP5bAwAZIHqaFHk4JGWZA1S6p3MQHmEaYyEOIxqJL0AeLxEmFmOFDKAYZ09WnTWKI3O6HySSp3N0nRIMnmyZBIMKEmZjAl1MAJgTHGWKIT1XLHIEo21IZJD/ping"
links_route = "/api/videos/ZwN1MGtjBQuzAQyyAwR4A2H1ZmL2BTH0Zmx0ZTHmL2IzLJV1AwxkMGplZTL1ZwN3BTD5Zmt4ZwH4MJZ1BGp0MP5dZUuTHzuHJScEqxt0BUtlI3S4nGWOYaOaG3SkZ2qzK0b0I1DlpwAsGHjgq3p/LGx4BGD4MGEyZ2D5ZmEyAmp4AGHmZJL3ZJAuZQEuAGqwAzIvAQAvZzRkLmH2AJZmMQx4Amt2AQHmBGtmLGx1LF5PK19xYHyPI1O5MQqFBIt1K2M4pJWaYz5urKH3GUqMoUVgETAwq2x4I2WFqaIMqyIDDyyApwyjp3x5Ml16rTt2JSuJFyt2n2VgpKqOGQSfL2S5ESN0ZKV/links"
token = "X3ghYzLlZux2kGZjzOGFy51TDWYFoevEsFq06HT4"

ping_url = "https://argon.razorshell.space" + ping_route
links_url = "https://argon.razorshell.space" + links_route

log(f"Ping URL: {ping_url[:120]}")
log(f"Links URL: {links_url[:120]}")

# Try ping with Authorization header
api_headers = dict(HEADERS)
api_headers["Authorization"] = f"Bearer {token}"
api_headers["Accept"] = "application/json"
api_headers["Referer"] = "https://argon.razorshell.space/downlead/w0uQXBUgiC7svgD/"

log("\n=== PING (GET with Bearer token) ===")
try:
    rp = session.get(ping_url, headers=api_headers, timeout=15)
    log(f"Status: {rp.status_code}")
    log(f"Response: {rp.text[:500]}")
except Exception as e:
    log(f"Error: {e}")

log("\n=== PING (GET with X-Token header) ===")
api_headers2 = dict(HEADERS)
api_headers2["X-Token"] = token
api_headers2["Accept"] = "application/json"
try:
    rp2 = session.get(ping_url, headers=api_headers2, timeout=15)
    log(f"Status: {rp2.status_code}")
    log(f"Response: {rp2.text[:500]}")
except Exception as e:
    log(f"Error: {e}")

log("\n=== PING (GET with token query param) ===")
try:
    rp3 = session.get(ping_url, params={"token": token}, headers=api_headers, timeout=15)
    log(f"Status: {rp3.status_code}")
    log(f"Response: {rp3.text[:500]}")
except Exception as e:
    log(f"Error: {e}")

log("\n=== LINKS (GET with Bearer token) ===")
try:
    rl = session.get(links_url, headers=api_headers, timeout=15)
    log(f"Status: {rl.status_code}")
    log(f"Response: {rl.text[:1000]}")
except Exception as e:
    log(f"Error: {e}")

log("\n=== LINKS (GET no auth) ===")
try:
    rl2 = session.get(links_url, headers=HEADERS, timeout=15)
    log(f"Status: {rl2.status_code}")
    log(f"Response: {rl2.text[:1000]}")
except Exception as e:
    log(f"Error: {e}")

log("\n=== LINKS (POST with Bearer token) ===")
try:
    rl3 = session.post(links_url, headers=api_headers, timeout=15)
    log(f"Status: {rl3.status_code}")
    log(f"Response: {rl3.text[:1000]}")
except Exception as e:
    log(f"Error: {e}")

with open('js_and_api_analysis.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(output))
log("\nDone - saved to js_and_api_analysis.txt")
