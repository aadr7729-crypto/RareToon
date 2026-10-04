import requests
import re
import base64
import json

data = open('argon_page.html', 'r', encoding='utf-8').read()
output = []

# Find window.juicyData
m = re.search(r'window\.juicyData\s*=\s*(\{.*?\});', data, re.DOTALL)
if not m:
    # Try without semicolon
    m = re.search(r'window\.juicyData\s*=\s*(\{.*?\})', data, re.DOTALL)

if m:
    raw = m.group(1)
    output.append(f"Raw juicyData JSON (first 1000 chars):")
    output.append(raw[:1000])
    
    # Try to parse - might have escaped slashes
    try:
        fixed = raw.replace('\\/', '/')
        parsed = json.loads(fixed)
        output.append(f"\nParsed JSON keys: {list(parsed.keys())}")
        if 'data' in parsed:
            output.append(f"Data keys: {list(parsed['data'].keys())}")
            output.append(f"Token: {parsed['data'].get('token', 'N/A')}")
            output.append(f"Video: {parsed['data'].get('video', 'N/A')}")
            routes = parsed['data'].get('routes', {})
            output.append(f"Routes: {json.dumps(routes, indent=2)}")
            
            # Try to decode the route values
            for route_name, route_path in routes.items():
                output.append(f"\n  Route '{route_name}': {route_path}")
                # Try base64 decode
                try:
                    decoded = base64.b64decode(route_path + "==").decode('utf-8', errors='replace')
                    output.append(f"    Base64 decoded: {decoded}")
                except:
                    pass
    except Exception as e:
        output.append(f"Parse error: {e}")
        output.append(f"Raw text: {raw[:500]}")
else:
    output.append("juicyData not found")

# Look for the full HTML structure around juicyData
output.append(f"\n\n=== Context around juicyData (1000 chars) ===")
idx = data.find('juicyData')
if idx >= 0:
    output.append(data[max(0,idx-100):idx+1000])

# Look for any other API URLs or scripts
output.append(f"\n=== API/script references ===")
for m in re.finditer(r'(api|endpoint|download|links|ping)["\']?\s*[:=]["\']?([^"\',\s}]+)', data, re.IGNORECASE):
    output.append(f"  {m.group(0)[:150]}")

with open('argon_decode.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(output))
