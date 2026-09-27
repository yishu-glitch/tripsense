import re
from pathlib import Path

p = Path(r"D:\cursor\resources\app\out\vs\workbench\workbench.desktop.main.js")
s = p.read_text(encoding="utf-8", errors="ignore")

# Find configuration schema entries with auto-run related titles/descriptions
patterns = [
    r'["\']([a-zA-Z0-9._-]*(?:AutoRun|autoRun|yolo|Yolo|approvalMode|terminalAllowlist|auto.?approve|runEverything|fullAuto)[a-zA-Z0-9._-]*)["\']',
    r'key:"([^"]*(?:autoRun|AutoRun|yolo|approval|Allowlist|sandbox)[^"]*)"',
]

found = set()
for pat in patterns:
    for m in re.finditer(pat, s):
        found.add(m.group(1))
print("FOUND KEYS:")
for k in sorted(found):
    if len(k) < 120:
        print(k)

print("\n==== applicationUserPersistentStorage / autoRun fields ====")
for needle in ["fullAutoRun", "autoRun", "useYoloMode", "yoloCommandAllowlist", "composerState", "enableRunEverything", "isYoloMode"]:
    shown = 0
    print(f"\n-- {needle} --")
    for m in re.finditer(r".{0,60}" + re.escape(needle) + r".{0,100}", s):
        t = m.group(0).replace("\n", " ")
        print(t[:220])
        shown += 1
        if shown >= 8:
            break

print("\n==== storage keys ====")
for m in re.finditer(r'["\'](composer\.[^"\']*auto[^"\']*)["\']', s, re.I):
    found.add(m.group(1))
for k in sorted(x for x in found if "composer" in x.lower() or "cursor" in x.lower()):
    print(k)
