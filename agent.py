"""
The Procurement Desk — rule-based negotiation agent (Data Sprint 2.0, Agentic track).

Reads the playbook (markdown) at runtime, parses each supplier thread, and applies
policies/decision-procedure.md in order:
  1 MISSING_INFO  2 BLACKLISTED_SUPPLIER  3 PROMPT_INJECTION  4 BANNED_TERM
  5 price decision (ACCEPT / COUNTER / REJECT)  6 OVER_APPROVAL_LIMIT  (+7 probation)

Usage:
  python agent.py --data data --check      # score on the 40 worked examples
  python agent.py --data data              # write submission.csv for threads.csv
"""
import argparse
import csv
import glob
import os
import re
import sys
from fractions import Fraction as F

# ----------------------------------------------------------------------------
# Playbook loading
# ----------------------------------------------------------------------------

def money(s):
    """'₹1,15,000' -> Fraction(115000)"""
    return F(re.sub(r"[^\d.]", "", s))


def load_playbook(root):
    pb = os.path.join(root, "playbook", "playbook")
    if not os.path.isdir(pb):
        pb = os.path.join(root, "playbook")

    def read(p):
        with open(p, encoding="utf-8") as f:
            return f.read()

    def field(text, name):
        m = re.search(r"\*\*" + re.escape(name) + r"[^*]*:\*\*\s*(.+)", text)
        return m.group(1).strip() if m else None

    cats = {}
    for p in glob.glob(os.path.join(pb, "categories", "*.md")):
        t = read(p)
        slug = os.path.splitext(os.path.basename(p))[0]
        unit = field(t, "Normalized unit")
        cats[slug] = {
            "name": re.search(r"# Category:\s*(.+)", t).group(1).strip(),
            "unit": unit,
            "period": "year" if "per year" in unit else ("month" if "per month" in unit else None),
            "gst": F(re.search(r"(\d+(?:\.\d+)?)", field(t, "GST rate")).group(1)) / 100,
            "target": money(field(t, "Target price")),
            "walkaway": money(field(t, "Walk-away price")),
            "R": int(re.search(r"\d+", field(t, "Negotiation rounds")).group(0)),
            "beta": F(re.search(r"\d+(?:\.\d+)?", field(t, "Concession exponent")).group(0)),
            "limit": money(field(t, "Approval limit")),
        }
        pc = field(t, "Pack conversion")
        cats[slug]["pack"] = pc
    name2slug = {c["name"].lower(): s for s, c in cats.items()}

    def cat_from_name(n):
        n = n.lower().strip()
        if n in name2slug:
            return name2slug[n]
        for full, s in name2slug.items():
            if full.startswith(n) or n.startswith(full):
                return s
        raise KeyError(n)

    amends = []
    for p in sorted(glob.glob(os.path.join(pb, "amendments", "*.md"))):
        t = read(p)
        cat = cat_from_name(re.search(r"# AMD-\d+:\s*(.+)", t).group(1))
        date = field(t, "Effective from")
        ch = field(t, "Change")
        m = re.search(r"the (walk-away price|target price|approval limit) for .+? is now (₹[\d,]+)", ch)
        key = {"walk-away price": "walkaway", "target price": "target", "approval limit": "limit"}[m.group(1)]
        amends.append({"cat": cat, "date": date, "key": key, "value": money(m.group(2))})
    amends.sort(key=lambda a: a["date"])

    sups = {}
    for p in glob.glob(os.path.join(pb, "suppliers", "*.md")):
        t = read(p)
        name = re.search(r"# Supplier:\s*(.+)", t).group(1).strip()
        status = field(t, "Status").upper()
        s = {"status": "BLACKLISTED" if "BLACKLIST" in status else ("PROBATION" if "PROBATION" in status else "APPROVED"),
             "rates": {}, "caps": {}}
        contract = field(t, "Contract") or ""
        m = re.search(r"invoices in ([A-Z]{3}) are converted at ₹([\d.,]+)", contract)
        if m:
            s["rates"][m.group(1)] = money(m.group(2))
        m = re.search(r"price cap for (.+?) of (₹[\d,]+)", contract)
        if m:
            s["caps"][cat_from_name(m.group(1))] = money(m.group(2))
        sups[name] = s

    t = read(os.path.join(pb, "policies", "units-and-currency.md"))
    rates = {m.group(1): F(m.group(2)) for m in re.finditer(r"\|\s*([A-Z]{3})\s*\|\s*([\d.]+)\s*\|", t)}
    return cats, amends, sups, rates


# ----------------------------------------------------------------------------
# Number parsing
# ----------------------------------------------------------------------------

UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100000, "lakhs": 100000, "crore": 10 ** 7}


def words_to_int(text):
    total, cur = 0, 0
    for w in re.split(r"[\s-]+", text.lower().strip()):
        if not w or w == "and":
            continue
        if w in UNITS:
            cur += UNITS[w]
        elif w in TENS:
            cur += TENS[w]
        elif w == "hundred":
            cur *= 100
        elif w in SCALES:
            total += cur * SCALES[w]
            cur = 0
        else:
            raise ValueError(w)
    return total + cur


CUR = r"(?P<cur>₹|Rs\.?|INR|USD|EUR|GBP|JPY|SGD|AED|AUD|CAD|CHF|CNY|\$|€|£)"
NUM = r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<mult>k\b|lakh\b|lakhs\b|crore\b)?"
WORDNUM = r"(?P<words>(?:(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakhs?|crore|and)[\s-]*)+)\s*rupees"
AMOUNT_RE = re.compile(CUR + r"\s*" + NUM + "|" + WORDNUM, re.I)
SYM = {"₹": "INR", "RS": "INR", "RS.": "INR", "INR": "INR", "$": "USD", "€": "EUR", "£": "GBP"}


def amount_at(m):
    """Return (Fraction value, currency code) for an AMOUNT_RE match."""
    if m.group("words"):
        return F(words_to_int(m.group("words"))), "INR"
    v = F(m.group("num").replace(",", ""))
    mult = (m.group("mult") or "").lower()
    v *= {"": 1, "k": 1000, "lakh": 100000, "lakhs": 100000, "crore": 10 ** 7}[mult]
    c = m.group("cur").upper()
    return v, SYM.get(c, c)


# ----------------------------------------------------------------------------
# Thread splitting
# ----------------------------------------------------------------------------

HDR = re.compile(r"^\[(Earlier|Latest) message — ([^,\]]+)(?:, [^\]]*)?\]\s*$", re.M)


def split_thread(msg):
    """-> list of (kind, author, body). Unstructured messages are one 'Latest' block."""
    hs = list(HDR.finditer(msg))
    if not hs:
        return [("Latest", None, msg)]
    out = []
    for i, h in enumerate(hs):
        end = hs[i + 1].start() if i + 1 < len(hs) else len(msg)
        out.append((h.group(1), h.group(2).strip(), msg[h.end():end]))
    return out


# ----------------------------------------------------------------------------
# Security / contract-term checks
# ----------------------------------------------------------------------------

INJECTION_PATTERNS = [
    r"\blanguage model\b", r"\bLLM\b", r"\bAI (agent|assistant|model|system|reviewer)\b",
    r"\bto (the |any |whichever )?(AI|bot|model|assistant)\b", r"\bbots?\b(?! *-)",
    r"\bautomated (system|agent|reviewer|screen)", r"\badmin override\b", r"\boverride\b",
    r"ignore (all |your |any )?(previous|prior|above) instructions", r"<!--", r"\bsystem (note|prompt|message)\b",
    r"reviewing assistant", r"\bassistant:", r"pre-?approved", r"already (been )?(signed off|approved|granted)",
    r"approval (has been |was )?already granted", r"signed off", r"skip the playbook",
    r"\bOutput ACCEPT\b", r"set action\s*=", r"mark (it|this)( thread)? (as )?(accept|approved)",
    r"record the decision as", r"AI सहायक", r"निर्देश", r"सहायक",
]
INJ_RE = re.compile("|".join(INJECTION_PATTERNS), re.I)
# legitimate business phrases that must NOT count
SAFE_RE = re.compile(r"AI-powered|system integration team|ERP system|assistant manager", re.I)


def find_injection(text):
    for line in text.split("\n"):
        clean = SAFE_RE.sub("", line)
        m = INJ_RE.search(clean)
        if m:
            return line.strip()
    return None


WORD_PCT = {"half": 50, "one third": F(100, 3), "a third": F(100, 3), "two thirds": F(200, 3), "a quarter": 25,
            "one quarter": 25, "three quarters": 75}


def find_banned(text):
    t = text
    # automatic renewal
    if re.search(r"no automatic renewal|not auto-?renew|does not renew|renewal only (if|by) (both|mutual)", t, re.I):
        t_ren = re.sub(r"[^\n]*(no automatic renewal|not auto-?renew|does not renew)[^\n]*", "", t, flags=re.I)
    else:
        t_ren = t
    if re.search(r"auto-?renew|automatic(ally)? renew|renews? on its own|year to year unless|continues? (from )?year to year|"
                 r"renews? (automatically|every|each)|rolls? over automatically", t_ren, re.I):
        return "AUTO_RENEWAL"
    # unlimited liability
    if re.search(r"unlimited liability|liability[^.\n]*unlimited|uncapped liability", t, re.I):
        return "UNLIMITED_LIABILITY"
    # exclusivity
    for line in t.split("\n"):
        if re.search(r"not (asking|requiring|seeking) (for )?exclusivity|no exclusivity", line, re.I):
            continue
        if re.search(r"exclusiv|only from us|solely from us|sole supplier|exclusively", line, re.I):
            return "EXCLUSIVITY"
    # advance / upfront payment above 30%
    for line in t.split("\n"):
        if not re.search(r"advance|upfront|up-front|up front|before delivery|in advance|prepay", line, re.I):
            continue
        pcts = [F(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)", line, re.I)]
        for w, v in WORD_PCT.items():
            if re.search(r"\b" + w + r"\b", line, re.I):
                pcts.append(F(v))
        for wm in re.finditer(r"\b((?:[a-z]+[\s-]+){0,3}[a-z]+)\s+(?:percent|per cent)\b", line, re.I):
            words = wm.group(1).split()
            for k in range(len(words)):  # longest numeric suffix, e.g. "need forty percent"
                try:
                    pcts.append(F(words_to_int(" ".join(words[k:]))))
                    break
                except ValueError:
                    continue
        # "Payment terms: 20% advance, balance net-30" -> only the advance pct matters (first)
        if pcts and pcts[0] > 30:
            return "ADVANCE>30"
    return None


# ----------------------------------------------------------------------------
# Offer parsing
# ----------------------------------------------------------------------------




class Missing(Exception):
    pass


def parse_price_expr(text, cat, sup, rates, qty, cats):
    """Parse one price statement (a sentence) into p: INR excl GST per normalized unit.
    Returns Fraction or raises Missing."""
    c = cats[cat]
    s = text
    ams = list(AMOUNT_RE.finditer(s))
    if not ams:
        raise Missing("no amount")

    def to_inr(m):
        v, cur = amount_at(m)
        if cur == "INR":
            return v
        if cur in sup["rates"]:
            return v * sup["rates"][cur]
        if cur in rates:
            return v * rates[cur]
        raise Missing("currency " + cur)

    # --- choose base amount -------------------------------------------------
    rng = re.search(r"between\s+(.+?)\s+and\s+(.+?)\s+per\b", s, re.I)
    tier = re.search(r"for orders of up to (\d[\d,]*)", s, re.I)
    total = re.search(r"in total for all (\d[\d,]*)", s, re.I)
    listp = re.search(r"(\d+(?:\.\d+)?)\s*% (?:discount )?(?:off )?(?:on )?our list price", s, re.I)

    if rng:
        vals = [to_inr(m) for m in AMOUNT_RE.finditer(rng.group(0))]
        base = max(vals)  # P-01/P-04: never assume the cheaper end
        rest = s[rng.end() - len("per"):]
    elif tier:
        limit = int(tier.group(1).replace(",", ""))
        vals = [to_inr(m) for m in ams[:2]]
        base = vals[0] if qty <= limit else vals[1]
        rest = s[ams[0].end():]
    else:
        # amount: for list price use the list amount; else first amount
        base = to_inr(ams[0])
        rest = s[ams[0].end():]

    if listp:
        base = base * (1 - F(listp.group(1)) / 100)

    # --- unit conversion ----------------------------------------------------
    unit_div = F(1)
    if total:
        unit_div = F(int(total.group(1).replace(",", "")))
    else:
        mp = re.search(r"per (dozen|pack of (\d+)|(\d[\d,]*)|pack|ream|reams)\b", rest, re.I)
        if mp:
            w = mp.group(1).lower()
            if w == "dozen":
                unit_div = F(12)
            elif w.startswith("pack of"):
                unit_div = F(int(mp.group(2)))
            elif mp.group(3):
                unit_div = F(int(mp.group(3).replace(",", "")))
            elif w == "pack":
                pk = re.search(r"pack holds (\d+)", c.get("pack") or "")
                unit_div = F(int(pk.group(1))) if pk else F(1)
            elif w.startswith("ream"):
                pk = re.search(r"one box = (\d+) reams", c.get("pack") or "")
                unit_div = 1 / F(int(pk.group(1))) if pk else F(1)
    base = base / unit_div

    # --- billing period -----------------------------------------------------
    if c["period"]:
        per = re.search(r"per (year|annum|month)|/\s*(year|month)|\b(yearly|annually|monthly|per yr|p\.a\.)\b", rest, re.I)
        if not per:
            raise Missing("no billing period")
        word = (per.group(1) or per.group(2) or per.group(3)).lower()
        got = "month" if word.startswith("month") else "year"
        if got != c["period"]:
            base = base * 12 if c["period"] == "year" else base / 12

    # --- GST ----------------------------------------------------------------
    if re.search(r"incl(?:\.|usive|uding)? (?:of )?GST|GST incl|\(GST included\)|including GST|GST-inclusive|with GST", s, re.I):
        base = base / (1 + c["gst"])

    # --- free units ---------------------------------------------------------
    fr = re.search(r"plus (\d+) free \w+ for every (\d+) you pay for", s, re.I)
    if fr:
        f, n = int(fr.group(1)), int(fr.group(2))
        base = base * n / (n + f)

    # --- volume discount ----------------------------------------------------
    vd = re.search(r"further (\d+(?:\.\d+)?)% off for orders of (\d[\d,]*)\s*\w* or more", s, re.I)
    if vd and qty >= int(vd.group(2).replace(",", "")):
        base = base * (1 - F(vd.group(1)) / 100)
    return base


def price_sentence(body):
    """Find the sentence in a message body carrying the supplier's price."""
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    corr = [l for l in lines if re.search(r"correction|should read", l, re.I)]
    if corr:
        m = re.search(r"should read (.+?),\s*not\b", corr[-1], re.I)
        return ("abs", m.group(1) if m else corr[-1])
    for l in lines:
        if re.search(r"further (\d+(?:\.\d+)?)% off (our|my) previous quote", l, re.I):
            return ("rel", l)
    cands = [l for l in lines if AMOUNT_RE.search(l)]
    if cands:
        return ("abs", cands[0])
    return (None, None)


def offer_price(blocks, cat, sup, rates, qty, cats):
    latest = blocks[-1][2]
    kind, sent = price_sentence(latest)
    if kind is None:
        raise Missing("no price in latest")
    if kind == "abs":
        return parse_price_expr(sent, cat, sup, rates, qty, cats)
    # relative: D% off supplier's previous quote
    d = F(re.search(r"further (\d+(?:\.\d+)?)% off", sent, re.I).group(1))
    prev = None
    for k, author, body in blocks[:-1]:
        if author and author.lower().startswith("acme"):
            continue
        k2, s2 = price_sentence(body)
        if k2 == "abs":
            prev = parse_price_expr(s2, cat, sup, rates, qty, cats)
        elif k2 == "rel" and prev is not None:
            prev = prev * (1 - F(re.search(r"further (\d+(?:\.\d+)?)% off", s2).group(1)) / 100)
    if prev is None:
        raise Missing("no previous quote")
    return prev * (1 - d / 100)


# ----------------------------------------------------------------------------
# Decision
# ----------------------------------------------------------------------------

def round_half_up(x):
    return int((x * 2 + 1) // 2)


def _name_key(name):
    """Normalize a supplier / trading name: case, '&' vs 'and', punctuation, legal suffixes."""
    n = name.lower().replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(r"\b(pvt|private|ltd|limited|llp|inc|co|company|the)\b", " ", n)
    return " ".join(n.split())


def find_supplier(name, sups):
    """Exact playbook name first, then normalized match. Returns (canonical name or None, record)."""
    name = name.strip()
    if name in sups:
        return name, sups[name]
    key = _name_key(name)
    for n, s in sups.items():
        if _name_key(n) == key:
            return n, s
    return None, {"status": "APPROVED", "rates": {}, "caps": {}}


def terms_for(cat, date, supplier, cats, amends, sups):
    c = dict(cats[cat])
    for a in amends:  # sorted by effective date, so a later amendment replaces an earlier one
        if a["cat"] == cat and a["date"] <= date:
            c[a["key"]] = a["value"]
    _, s = find_supplier(supplier, sups)
    if cat in s["caps"]:
        c["walkaway"] = s["caps"][cat]
    return c


def decide(row, cats, amends, sups, rates, debug=None):
    cat = row["category"]
    canon, sup = find_supplier(row["supplier"], sups)
    sup_name = canon or row["supplier"].strip()
    qty, r = int(float(row["quantity"])), int(float(row["round"]))
    msg = row["supplier_message"]
    blocks = split_thread(msg)
    latest = blocks[-1][2]
    info = debug if debug is not None else {}
    trace = info.setdefault("trace", [])  # list of (gate, fired: bool, detail)

    def fire(gate, detail, label):
        trace.append((gate, True, detail))
        return label

    # 1. missing info
    try:
        p = offer_price(blocks, cat, sup, rates, qty, cats)
    except Missing as e:
        info["why"] = str(e)
        return fire("1. Missing information", str(e), "ESCALATE:MISSING_INFO:")
    info["p"] = float(p)
    trace.append(("1. Missing information", False, f"usable price found: p = ₹{float(p):,.2f} per {cats[cat]['unit'].replace('INR excl. GST per ', '')}"))
    # 2. blacklist
    if sup["status"] == "BLACKLISTED":
        return fire("2. Blacklisted supplier", f"{sup_name} is BLACKLISTED", "REJECT:BLACKLISTED_SUPPLIER:")
    trace.append(("2. Blacklisted supplier", False, f"{sup_name} status: {sup['status']}"))
    # 3. prompt injection — anywhere in thread
    inj = find_injection(msg)
    if inj:
        info["why"] = inj
        return fire("3. Prompt injection", f'found: "{inj}"', "ESCALATE:PROMPT_INJECTION:")
    trace.append(("3. Prompt injection", False, "no text aimed at an AI / bot / reviewer"))
    # 4. banned terms — latest message
    b = find_banned(latest)
    if b:
        info["why"] = b
        return fire("4. Banned contract terms", b, "ESCALATE:BANNED_TERM:")
    trace.append(("4. Banned contract terms", False, "none in the latest message"))
    # 5. price
    t = terms_for(cat, row["date"], sup_name, cats, amends, sups)
    R = t["R"]
    info["terms"] = {k: (float(v) if isinstance(v, F) else v) for k, v in t.items() if k in ("target", "walkaway", "R", "beta", "limit")}
    if sup["status"] == "PROBATION":
        L = t["target"]
        how = f"probation supplier → L = target = ₹{float(L):,.2f}"
    else:
        L = t["target"] + (t["walkaway"] - t["target"]) * (F(r - 1, R - 1) ** int(t["beta"]))
        how = (f"L({r}) = {float(t['target']):,.0f} + ({float(t['walkaway']):,.0f} − {float(t['target']):,.0f}) × "
               f"(({r}−1)/({R}−1))^{int(t['beta'])} = ₹{float(L):,.2f}")
    info["L"] = float(L)
    if p > L:
        if r >= R:
            return fire("5. Price decision", f"{how}; p = ₹{float(p):,.2f} > L on the final round ({r}/{R})", "REJECT:ABOVE_WALKAWAY:")
        return fire("5. Price decision", f"{how}; p = ₹{float(p):,.2f} > L, round {r}/{R} → counter at ₹{round_half_up(L):,}",
                    f"COUNTER:COUNTER_OFFER:{round_half_up(L)}")
    trace.append(("5. Price decision", False, f"{how}; p = ₹{float(p):,.2f} ≤ L → acceptable"))
    # 6. approval limit
    total = p * qty
    if total > t["limit"]:
        return fire("6. Approval limit", f"p × {qty} = ₹{float(total):,.0f} > limit ₹{float(t['limit']):,.0f}", "ESCALATE:OVER_APPROVAL_LIMIT:")
    return fire("6. Approval limit", f"p × {qty} = ₹{float(total):,.0f} ≤ limit ₹{float(t['limit']):,.0f} → accept", "ACCEPT:ACCEPTABLE_PRICE:")


def read_csv(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default="submission.csv")
    ap.add_argument("--debug", default=None, help="write per-thread debug csv")
    a = ap.parse_args()
    cats, amends, sups, rates = load_playbook(a.data)

    if a.check:
        rows = read_csv(os.path.join(a.data, "sample_threads.csv"))
        ans = {r["thread_id"]: r["label"] for r in read_csv(os.path.join(a.data, "sample_answers.csv"))}
        ok = 0
        for row in rows:
            dbg = {}
            pred = decide(row, cats, amends, sups, rates, dbg)
            if pred == ans[row["thread_id"]]:
                ok += 1
            else:
                print("MISMATCH", row["thread_id"], "pred", pred, "gold", ans[row["thread_id"]], dbg)
        print(f"sample accuracy: {ok}/{len(rows)}")
        return

    rows = read_csv(os.path.join(a.data, "threads.csv"))
    dbg_rows = []
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["thread_id", "label"])
        for row in rows:
            dbg = {}
            lab = decide(row, cats, amends, sups, rates, dbg)
            w.writerow([row["thread_id"], lab])
            dbg_rows.append({"thread_id": row["thread_id"], "label": lab, **{k: dbg.get(k) for k in ("p", "L", "why")}})
    if a.debug:
        with open(a.debug, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["thread_id", "label", "p", "L", "why"])
            w.writeheader()
            w.writerows(dbg_rows)
    print(f"wrote {len(rows)} rows to {a.out}")


if __name__ == "__main__":
    main()
