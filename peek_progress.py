import json, sys
d = json.load(open("/root/chaoxing_auto/progress.json", encoding="utf-8"))
items = list(d.items())
print("total:", len(items))
for k, v in items[-12:]:
    print(k, str(v)[:19])