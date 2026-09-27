import re
from pathlib import Path

p = Path(r"D:\cursor\resources\app\out\vs\workbench\workbench.desktop.main.js")
s = p.read_text(encoding="utf-8", errors="ignore")

pat = re.compile(
    r'"((?:cursor|composer|chat|aipopup)\.[a-zA-Z0-9._-]{0,100}'
    r'(?:auto|approv|allow|yolo|sandbox|permission|terminal|runMode|runEverything|Allowlist)'
    r'[a-zA-Z0-9._-]{0,100})"',
    re.I,
)
keys = sorted(set(pat.findall(s)))
print("SETTING KEYS:")
for k in keys:
    print(k)
print("COUNT", len(keys))

print("\nPERMISSIONS PATH CTX:")
for m in re.finditer(r".{0,120}permissions\.json.{0,120}", s):
    print(m.group(0).replace("\n", " ")[:300])
    print("---")
    if sum(1 for _ in []) >= 0:
        pass
# limit
shown = 0
for m in re.finditer(r".{0,100}permissions\.json.{0,100}", s):
    print(m.group(0).replace("\n", " ")[:250])
    print("---")
    shown += 1
    if shown >= 15:
        break

print("\nUNRESTRICTED CTX:")
shown = 0
for m in re.finditer(r".{0,80}unrestricted.{0,80}", s, re.I):
    t = m.group(0).replace("\n", " ")
    if "approval" in t.lower() or "auto" in t.lower() or "permission" in t.lower():
        print(t[:250])
        print("---")
        shown += 1
        if shown >= 20:
            break

print("\nCONFIGURATION CONTRIB:")
# look for configuration properties ids
shown = 0
for m in re.finditer(r'id:"((?:cursor|composer|chat)\.[^"]+)"', s):
    k = m.group(1)
    if re.search(r"auto|approv|allow|yolo|sandbox|permission|terminal|run", k, re.I):
        print(k)
        shown += 1
        if shown >= 80:
            break
