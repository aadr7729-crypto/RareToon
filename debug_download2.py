data = open('download_page.html', 'r', encoding='utf-8').read()
output = []

def log(msg):
    output.append(str(msg))

idx = data.find('goBtn')
log(f"goBtn found at index {idx}")
log(data[max(0,idx-200):idx+1500])
log("\n---\n")

idx2 = data.find('handleClick')
log(f"handleClick found at index {idx2}")
log(data[max(0,idx2-500):idx2+2500])

with open('debug_download2.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(output))
