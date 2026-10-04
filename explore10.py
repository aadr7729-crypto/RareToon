import requests
import json
import re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

session = requests.Session()
session.headers.update(HEADERS)

output = []
def log(msg):
    output.append(str(msg))

# The juicyData
token = "X3ghYzLlZux2kGZjzOGFy51TDWYFoevEsFq06HT4"
video = "w0uQXBUgiC7svgD"
links_route = "/api/videos/ZwN1MGtjBQuzAQyyAwR4A2H1ZmL2BTH0Zmx0ZTHmL2IzLJV1AwxkMGplZTL1ZwN3BTD5Zmt4ZwH4MJZ1BGp0MP5dZUuTHzuHJScEqxt0BUtlI3S4nGWOYaOaG3SkZ2qzK0b0I1DlpwAsGHjgq3p/LGx4BGD4MGEyZ2D5ZmEyAmp4AGHmZJL3ZJAuZQEuAGqwAzIvAQAvZzRkLmH2AJZmMQx4Amt2AQHmBGtmLGx1LF5PK19xYHyPI1O5MQqFBIt1K2M4pJWaYz5urKH3GUqMoUVgETAwq2x4I2WFqaIMqyIDDyyApwyjp3x5Ml16rTt2JSuJFyt2n2VgpKqOGQSfL2S5ESN0ZKV/links"
ping_route = "/api/videos/AmNlZ2IuZQLkZGH5ZGSwMQt4LJMyAmZ4AGyyAJL0LmH0ZGAuLJHjLGMwBGqzZGD0LGDlMQLkAmVlBQAuZmL4MF5sGHyOMKOdImD0p09cEmSJJKN3ImqOYz92pRA1BRubq3qQn2HmpUqgn1EhnRR/ZQZ2ZwAwAQL2BQqyLwR5BJRmMGt2LmyvLwplL2WwLGZ0LGyyBTDjAmt2ZzV2AmH4AwZlAwMuBJH5AQMwMGtlZP5bAwAZIHqaFHk4JGWZA1S6p3MQHmEaYyEOIxqJL0AeLxEmFmOFDKAYZ09WnTWKI3O6HySSp3N0nRIMnmyZBIMKEmZjAl1MAJgTHGWKIT1XLHIEo21IZJD/ping"

links_url = "https://argon.razorshell.space" + links_route
ping_url = "https://argon.razorshell.space" + ping_route

# First, try ping (GET)
log("=== PING (GET) ===")
for headers_set in [
    {"Authorization": f"Bearer {token}"},
    {"X-Token": token},
    {},
]:
    h = dict(HEADERS)
    h.update(headers_set)
    h["Referer"] = "https://argon.razorshell.space/downlead/w0uQXBUgiC7svgD/"
    try:
        rp = session.get(ping_url, headers=h, timeout=15)
        log(f"  Headers: {headers_set}")
        log(f"  Status: {rp.status_code}, Length: {len(rp.text)}")
        log(f"  Response: {rp.text[:300]}")
        if rp.status_code == 200 and len(rp.text) > 0:
            log(f"  ^--- SUCCESS!")
    except Exception as e:
        log(f"  Error: {e}")

# Now try links (POST) - various approaches
log("\n=== LINKS (POST) attempts ===")

# Attempt 1: POST with Authorization header, JSON body with token
attempts = [
    ("Authorization Bearer + JSON body token", {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json.dumps({"token": token})),
    ("Authorization Bearer + empty body", {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, ""),
    ("X-Token header + JSON body", {"X-Token": token, "Content-Type": "application/json"}, json.dumps({"token": token})),
    ("No auth + JSON body", {"Content-Type": "application/json"}, json.dumps({"token": token})),
    ("No auth + form data", {"Content-Type": "application/x-www-form-urlencoded"}, f"token={token}"),
    ("Authorization Bearer + video in body", {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json.dumps({"token": token, "video": video})),
]

for desc, headers, body in attempts:
    h = dict(HEADERS)
    h.update(headers)
    h["Referer"] = "https://argon.razorshell.space/downlead/w0uQXBUgiC7svgD/"
    try:
        if body:
            rl = session.post(links_url, headers=h, data=body, timeout=15)
        else:
            rl = session.post(links_url, headers=h, timeout=15)
        log(f"\n  Attempt: {desc}")
        log(f"  Status: {rl.status_code}, Length: {len(rl.text)}")
        log(f"  Response: {rl.text[:500]}")
    except Exception as e:
        log(f"\n  Attempt: {desc}")
        log(f"  Error: {e}")

# Also try GET on links route
log("\n=== LINKS (GET) ===")
for headers_set in [
    {"Authorization": f"Bearer {token}"},
    {},
]:
    h = dict(HEADERS)
    h.update(headers_set)
    h["Referer"] = "https://argon.razorshell.space/downlead/w0uQXBUgiC7svgD/"
    try:
        rl = session.get(links_url, headers=h, timeout=15)
        log(f"  Headers: {headers_set}")
        log(f"  Status: {rl.status_code}, Length: {len(rl.text)}")
        log(f"  Response: {rl.text[:500]}")
    except Exception as e:
        log(f"  Error: {e}")

with open('api_test.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(output))
log("\nDone - saved to api_test.txt")
