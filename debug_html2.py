data = open('multiquality_raw.html', 'r', encoding='utf-8').read()
f = open('debug_output2.txt', 'w', encoding='utf-8')

# Find context around "S16 E11"
idx = data.find('S16 E11')
if idx >= 0:
    f.write(f"Found 'S16 E11' at index {idx}\n")
    f.write(f"Context (500 chars before, 500 chars after):\n")
    f.write(data[max(0,idx-500):idx+500])
    f.write("\n\n")
else:
    f.write("'S16 E11' not found in raw HTML\n")
    # Search for variations
    for term in ["S16", "E11", "Episode", "zipper", "multiquality"]:
        idx = data.find(term)
        if idx >= 0:
            f.write(f"Found '{term}' at index {idx}\n")
            f.write(data[max(0,idx-100):idx+200])
            f.write("\n\n")

# Also look for Vue/React bindings
f.write("\n=== Looking for Vue/JS directives ===\n")
import re
for m in re.finditer(r':href=|v-bind:href=|\[@click\]|data-v-|x-bind', data):
    f.write(f"Found directive at {m.start()}: {data[max(0,m.start()-50):m.start()+100]}\n")

# Look at the link structure more carefully
f.write("\n=== All <a tags ===\n")
for m in re.finditer(r'<a\b[^>]*>', data):
    f.write(f"{m.group(0)[:200]}\n")

f.close()
