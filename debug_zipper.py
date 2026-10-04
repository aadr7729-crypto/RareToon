import requests
from scraper import _session, CODEDEW_BASE

s = _session()
zipper_url = "https://codedew.com/zipper/?url=HQGt0w9e4pRt7Tp2SeWiFZeQARhKZ2ijEJQp8DsgVRo6NCLT"

print(f"Fetching: {zipper_url}")
r = s.get(zipper_url, timeout=30, allow_redirects=True)
print(f"Final URL: {r.url}")
print(f"Status: {r.status_code}")
print(f"Response length: {len(r.text)}")

print("\n=== Raw HTML (first 5000 chars) ===")
print(r.text[:5000])
print("\n=== Last 2000 chars ===")
print(r.text[-2000:])
