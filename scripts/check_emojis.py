import re, unicodedata
from pathlib import Path

f = Path('static/index.html').read_text(encoding='utf-8')
lines = f.splitlines()

ep = re.compile(r'[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\u203c-\u2049]|[\u25aa-\u25fe]')

found = []
for i, l in enumerate(lines, 1):
    m = ep.findall(l)
    if m:
        for c in m:
            found.append((i, f'U+{ord(c):04X}', unicodedata.name(c, '?'), l.strip()))

print(f"REMAINING EMOJIS COUNT: {len(found)}")
for i, code, name, text in found:
    safe_text = text[:80].encode('ascii', 'replace').decode('ascii')
    print(f"L{i}: {code} ({name}) | {safe_text}")
