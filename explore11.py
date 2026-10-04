import requests
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://argon.razorshell.space/downlead/w0uQXBUgiC7svgD/",
}

session = requests.Session()
session.headers.update(HEADERS)

token = "X3ghYzLlZux2kGZjzOGFy51TDWYFoevEsFq06HT4"
links_route = "/api/videos/ZwN1MGtjBQuzAQyyAwR4A2H1ZmL2BTH0Zmx0ZTHmL2IzLJV1AwxkMGplZTL1ZwN3BTD5Zmt4ZwH4MJZ1BGp0MP5dZUuTHzuHJScEqxt0BUtlI3S4nGWOYaOaG3SkZ2qzK0b0I1DlpwAsGHjgq3p/LGx4BGD4MGEyZ2D5ZmEyAmp4AGHmZJL3ZJAuZQEuAGqwAzIvAQAvZzRkLmH2AJZmMQx4Amt2AQHmBGtmLGx1LF5PK19xYHyPI1O5MQqFBIt1K2M4pJWaYz5urKH3GUqMoUVgETAwq2x4I2WFqaIMqyIDDyyApwyjp3x5Ml16rTt2JSuJFyt2n2VgpKqOGQSfL2S5ESN0ZKV/links"
links_url = "https://argon.razorshell.space" + links_route

r = session.post(links_url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Referer": "https://argon.razorshell.space/downlead/w0uQXBUgiC7svgD/"}, data=json.dumps({"token": token}), timeout=15)

output = []
output.append(f"Status: {r.status_code}")
output.append(f"Full response:")
try:
    data = r.json()
    output.append(json.dumps(data, indent=2))
except:
    output.append(r.text)

with open('api_full_response.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(output))
print("Done")
