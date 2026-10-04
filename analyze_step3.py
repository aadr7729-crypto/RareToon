data = open('step3.html', 'r', encoding='utf-8').read()
output = []
output.append(f"Length: {len(data)}")
output.append(f"\nFull raw content:")
output.append(data)

with open('step3_analysis.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(output))
