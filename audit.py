"""
Label-free audit of the agent on the 2,000 test threads (see README section 7).

  python audit.py --data data

Prints: the label distribution, why each MISSING_INFO / PROMPT_INJECTION / BANNED_TERM fired,
and a census of every distinct non-price line template in the latest messages, so that each detector
count can be compared with the number of matching templates.
"""
import argparse
import collections
import csv
import os
import re
import sys

import agent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cats, amends, sups, rates = agent.load_playbook(a.data)
    with open(os.path.join(a.data, "threads.csv"), encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    labels, why = collections.Counter(), collections.defaultdict(collections.Counter)
    for row in rows:
        dbg = {}
        lab = agent.decide(row, cats, amends, sups, rates, dbg)
        key = ":".join(lab.split(":")[:2])
        labels[key] += 1
        if key.split(":")[1] in ("MISSING_INFO", "PROMPT_INJECTION", "BANNED_TERM"):
            why[key][dbg.get("why")] += 1

    print(f"== label distribution ({len(rows)} threads)")
    for k, v in labels.most_common():
        print(f"{v:5d}  {k}")
    for k, c in why.items():
        print(f"\n== {k}: what fired")
        for w, v in c.most_common():
            print(f"{v:5d}  {w}")

    # census of non-price lines in the latest message (numbers masked)
    sup_names = {r["supplier"] for r in rows}
    census = collections.Counter()
    for row in rows:
        latest = agent.split_thread(row["supplier_message"])[-1][2]
        for line in latest.split("\n"):
            line = line.strip()
            if not line or line in sup_names or agent.AMOUNT_RE.search(line):
                continue
            census[re.sub(r"\d+", "#", line)] += 1
    print("\n== census of non-price line templates in latest messages")
    for t, v in census.most_common():
        print(f"{v:5d}  {t}")


if __name__ == "__main__":
    main()
