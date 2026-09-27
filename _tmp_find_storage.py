import re, json, sqlite3
from pathlib import Path

# 1) Find how applicationUserPersistentStorage is keyed in ItemTable
p = Path(r"D:\cursor\resources\app\out\vs\workbench\workbench.desktop.main.js")
s = p.read_text(encoding="utf-8", errors="ignore")

print("=== storage key patterns ===")
for needle in [
    "applicationUserPersistentStorage",
    "aiprofile",
    "cursorAuth/",
    "composerState",
    "yoloEnableRunEverything",
    "yoloCommandAllowlist",
]:
    shown = 0
    print(f"\n-- {needle} --")
    for m in re.finditer(r".{0,40}" + re.escape(needle) + r".{0,80}", s):
        t = m.group(0).replace("\n", " ")
        if "ItemTable" in t or "storage" in t.lower() or "key" in t.lower() or "get" in t.lower():
            print(t[:200])
            shown += 1
            if shown >= 5:
                break

# 2) Inspect state DB for composerState / yolo keys
con = sqlite3.connect(r"C:\Users\18325\AppData\Roaming\Cursor\User\globalStorage\state.vscdb")
cur = con.cursor()
print("\n=== ItemTable keys matching ===")
for (k,) in cur.execute("SELECT key FROM ItemTable").fetchall():
    if re.search(r"composer|yolo|autoRun|permission|aiprofile|storage", k or "", re.I):
        print(k)

print("\n=== cursorDiskKV keys matching (non-content) ===")
for (k,) in cur.execute("SELECT key FROM cursorDiskKV").fetchall():
    if k and not k.startswith("composer.content.") and not k.startswith("agentKv:") and re.search(r"composerState|yolo|autoRun|permission|fullAuto|modes4|allowlist", k, re.I):
        print(k)

# Try common keys
for key in [
    "src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl.persistentStorage.applicationUser",
    "composer.composerData",
    "cursor/composerState",
]:
    row = cur.execute("SELECT key, length(value) FROM ItemTable WHERE key = ?", (key,)).fetchone()
    print("ItemTable", key, row)
    row2 = cur.execute("SELECT key, length(value) FROM cursorDiskKV WHERE key = ?", (key,)).fetchone()
    print("cursorDiskKV", key, row2)

# Search keys containing reactiveStorage or applicationUser
print("\n=== reactive / applicationUser keys ===")
for table in ["ItemTable", "cursorDiskKV"]:
    for (k,) in cur.execute(f"SELECT key FROM {table}").fetchall():
        if k and re.search(r"reactive|applicationUser|composerState|yoloCommand", k, re.I):
            print(table, k)
