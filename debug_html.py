import re

data = open('multiquality_raw.html', 'r', encoding='utf-8').read()
f = open('debug_output.txt', 'w', encoding='utf-8')

f.write(f"File length: {len(data)}\n")
f.write(f"First 1000 chars:\n{data[:1000]}\n\n")

# Find all hrefs
links = re.findall(r'href=["\']([^"\']+)["\']', data)
f.write(f"Total hrefs: {len(links)}\n")

codedew_links = [l for l in links if 'codedew' in l]
f.write(f"Codedew links: {len(codedew_links)}\n")
for l in codedew_links:
    f.write(f"  {l[:200]}\n")

# Find the url param values in the page
url_params = re.findall(r'url=([A-Za-z0-9%+/=_-]+)', data)
unique_params = list(set(url_params))
f.write(f"\nUnique url params: {len(unique_params)}\n")
for p in unique_params[:20]:
    f.write(f"  {p[:80]}\n")

# Look for script content
scripts = re.findall(r'<script[^>]*>(.*?)</script>', data, re.DOTALL)
f.write(f"\nScripts found: {len(scripts)}\n")
for i, s in enumerate(scripts):
    s_clean = s.strip()
    if len(s_clean) > 10:
        f.write(f"Script {i} ({len(s_clean)} chars): {s_clean[:300]}\n\n")

f.close()
print("Done - output saved to debug_output.txt")
