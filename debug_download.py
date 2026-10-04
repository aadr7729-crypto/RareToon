data = open('download_page.html', 'r', encoding='utf-8').read()
f = open('debug_download.txt', 'w', encoding='utf-8')

import re

# Find the button with "Scanning Link"
f.write("=== Button/anchor with 'Scanning' ===\n")
for m in re.finditer(r'(<button|<a).*?Scanning.*?(</button>|</a>)', data, re.DOTALL):
    f.write(f"{m.group(0)[:500]}\n\n")

# Find all onclick handlers
f.write("\n=== All onclick handlers ===\n")
for m in re.finditer(r'onclick="([^"]*)"', data):
    f.write(f"{m.group(1)[:300]}\n\n")
for m in re.finditer(r"onclick='([^']*)'", data):
    f.write(f"{m.group(1)[:300]}\n\n")

# Find iframes
f.write("\n=== Iframes ===\n")
for m in re.finditer(r'<iframe(.*?)></iframe>|<iframe(.*?)(?:/>|></iframe>)', data, re.DOTALL):
    f.write(f"Iframe: {m.group(0)[:300]}\n\n")

# Find all script sources and inline scripts
f.write("\n=== Script tags ===\n")
for m in re.finditer(r'<script[^>]*>(.*?)</script>', data, re.DOTALL):
    content = m.group(1).strip()
    if content:
        f.write(f"Script ({len(content)} chars): {content[:500]}\n\n")

# Look for URLs in the raw HTML
f.write("\n=== All HTTP URLs in raw HTML ===\n")
urls = re.findall(r'https?://[^\s"\'<>]+', data)
unique_urls = list(set(urls))
for u in unique_urls:
    f.write(f"  {u[:200]}\n")

# Look for the step progression logic - maybe there's a JS file
f.write("\n=== Looking for .js file references ===\n")
for m in re.finditer(r'(?:src|href)=["\']([^"\']+\.js[^"\']*)["\']', data):
    f.write(f"  {m.group(1)[:200]}\n")

f.close()
