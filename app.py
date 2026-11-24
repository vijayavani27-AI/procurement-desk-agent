"""
The Procurement Desk — negotiation agent UI (Data Sprint 2.0 · Agentic AI track · team vijayavani135)

Run locally:   streamlit run app.py
"""
import io
import os
from datetime import date

import pandas as pd
import streamlit as st

import agent

DATA = os.environ.get("PD_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))

st.set_page_config(page_title="The Procurement Desk · Agent", page_icon="🤝", layout="wide")

ACTION_STYLE = {
    "ACCEPT": ("#1f9d55", "✅"),
    "COUNTER": ("#d9a400", "🔁"),
    "REJECT": ("#d64545", "⛔"),
    "ESCALATE": ("#7e57c2", "🚩"),
}

st.markdown(
    """
    <style>
      .label-card {border-radius: 14px; padding: 18px 22px; color: white; margin-bottom: 8px;}
      .label-card .big {font-size: 30px; font-weight: 800; letter-spacing: .5px;}
      .label-card .sub {font-size: 15px; opacity: .92; font-family: ui-monospace, Consolas, monospace;}
      .gate {padding: 8px 12px; border-radius: 10px; margin: 4px 0; border: 1px solid rgba(128,128,128,.25);}
      .gate.fired {border: 2px solid #d64545; background: rgba(214,69,69,.08);}
      .gate .name {font-weight: 700;}
      .gate .detail {font-size: 14px; opacity: .85;}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def load():
    cats, amends, sups, rates = agent.load_playbook(DATA)
    samples = pd.read_csv(os.path.join(DATA, "sample_threads.csv"), dtype=str)
    answers = pd.read_csv(os.path.join(DATA, "sample_answers.csv"), dtype=str)
    samples = samples.merge(answers, on="thread_id", how="left")
    return cats, amends, sups, rates, samples


cats, amends, sups, rates, samples = load()


def run_one(row):
    dbg = {}
    label = agent.decide(row, cats, amends, sups, rates, dbg)
    return label, dbg


def label_card(label):
    action, reason, price = (label.split(":") + ["", ""])[:3]
    color, icon = ACTION_STYLE.get(action, ("#555", "•"))
    extra = f" at ₹{int(price):,}" if price else ""
    st.markdown(
        f"""<div class="label-card" style="background:{color}">
              <div class="big">{icon} {action}{extra}</div>
              <div class="sub">{reason} &nbsp;·&nbsp; {label}</div>
            </div>""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------- header
st.title("🤝 The Procurement Desk — Negotiation Agent")
st.caption("Data Sprint 2.0 · Agentic AI track · team **vijayavani135** · reads Acme's playbook and decides: "
           "ACCEPT, COUNTER, REJECT or ESCALATE")

with st.sidebar:
    st.header("How it decides")
    st.markdown(
        """
Gates run in the playbook's order, **the first one that fires wins**:

1. **Missing info** → ESCALATE
2. **Blacklisted supplier** → REJECT
3. **Prompt injection** → ESCALATE
4. **Banned contract terms** → ESCALATE
5. **Price vs L(r)** → COUNTER / REJECT
6. **Approval limit** → ESCALATE / ACCEPT
        """
    )
    st.divider()
    st.metric("Worked examples", "40 / 40")
    st.metric("Kaggle public score", "1.00000")
    st.caption("Deterministic rule engine · exact fraction arithmetic · supplier text is parsed, never obeyed.")

tab1, tab2, tab3 = st.tabs(["🧾 Decide one thread", "📦 Batch (CSV)", "📘 Playbook in force"])

# ---------------------------------------------------------------------------- single thread
with tab1:
    ex_opts = ["— write your own —"] + [
        f"{r.thread_id} · {r.supplier} · {r.category} → {r.label}" for r in samples.itertuples()
    ]
    pick = st.selectbox("Load a worked example (from the competition's 40 samples)", ex_opts, index=3)
    ex = samples.iloc[ex_opts.index(pick) - 1] if pick != ex_opts[0] else None

    sup_names = sorted(sups)
    cat_slugs = sorted(cats)
    c1, c2, c3, c4, c5 = st.columns([2.2, 1.6, 1.2, 1, 1])
    supplier = c1.selectbox("Supplier", sup_names,
                            index=sup_names.index(ex.supplier) if ex is not None and ex.supplier in sup_names else 0)
    category = c2.selectbox("Category", cat_slugs,
                            index=cat_slugs.index(ex.category) if ex is not None else 0,
                            format_func=lambda s: cats[s]["name"])
    d = c3.date_input("Thread date", value=date.fromisoformat(ex.date) if ex is not None else date(2026, 11, 20))
    qty = c4.number_input("Quantity", min_value=1, value=int(float(ex.quantity)) if ex is not None else 50, step=1)
    rnd = c5.number_input(f"Round (R = {cats[category]['R']})", min_value=1, max_value=cats[category]["R"],
                          value=min(int(float(ex["round"])), cats[category]["R"]) if ex is not None else 1, step=1)
    msg = st.text_area("Supplier message (the whole thread)", height=230,
                       value=ex.supplier_message if ex is not None else
                       "Hello,\n\nFor the 50 laptops you requested, we can do INR 66,500 per laptop.\n\nRegards,\nLotus Hardware")

    if st.button("Decide", type="primary", use_container_width=True):
        row = {"thread_id": "UI", "date": d.isoformat(), "supplier": supplier, "category": category,
               "quantity": str(qty), "round": str(rnd), "our_last_counter": "", "supplier_message": msg}
        label, dbg = run_one(row)
        left, right = st.columns([1.1, 1])
        with left:
            label_card(label)
            if ex is not None and isinstance(ex.label, str) and pick != ex_opts[0]:
                ok = label == ex.label
                st.caption(("✅ matches" if ok else "❌ differs from") + f" the official answer: `{ex.label}`")
            st.subheader("Decision trace")
            color = ACTION_STYLE.get(label.split(":")[0], ("#555", ""))[0]
            for gate, fired, detail in dbg.get("trace", []):
                style = f' style="border:2px solid {color}; background:{color}1a"' if fired else ""
                mark = f"➜ decides <b>{label.split(':')[0]}</b>" if fired else "✔ passed"
                st.markdown(f'<div class="gate"{style}><span class="name">{gate}</span> — {mark}'
                            f'<div class="detail">{detail}</div></div>', unsafe_allow_html=True)
        with right:
            st.subheader("Numbers used")
            t = agent.terms_for(category, d.isoformat(), supplier, cats, amends, sups)
            s = sups.get(supplier, {})
            rows = [
                ("Normalized unit", cats[category]["unit"]),
                ("Offer price p", f"₹{dbg['p']:,.2f}" if "p" in dbg else "—"),
                ("Acceptable level L(r)", f"₹{dbg['L']:,.2f}" if "L" in dbg else "—"),
                ("Target (in force)", f"₹{float(t['target']):,.0f}"),
                ("Walk-away / cap (in force)", f"₹{float(t['walkaway']):,.0f}"),
                ("Rounds R · exponent β", f"{t['R']} · {int(t['beta'])}"),
                ("Approval limit (in force)", f"₹{float(t['limit']):,.0f}"),
                ("Supplier status", s.get("status", "APPROVED")),
                ("Contract FX rates", ", ".join(f"{k} ₹{float(v)}" for k, v in s.get("rates", {}).items()) or "—"),
            ]
            st.table(pd.DataFrame(rows, columns=["", "value"]).set_index(""))
            applied = [a for a in amends if a["cat"] == category and a["date"] <= d.isoformat()]
            if applied:
                st.caption("Amendments applied: " + "; ".join(f"{a['date']} {a['key']} → ₹{float(a['value']):,.0f}" for a in applied))

# ---------------------------------------------------------------------------- batch
with tab2:
    st.write("Upload a CSV with columns `thread_id, date, supplier, category, quantity, round, our_last_counter, "
             "supplier_message` and get a Kaggle-ready `submission.csv`.")
    up = st.file_uploader("threads.csv", type=["csv"])
    use_bundled = st.button("…or run the 40 worked examples")
    df = None
    if up is not None:
        df = pd.read_csv(up, dtype=str, keep_default_na=False)
    elif use_bundled:
        df = samples.drop(columns=["label"]).fillna("")
    if df is not None:
        need = {"thread_id", "date", "supplier", "category", "quantity", "round", "supplier_message"}
        missing = need - set(df.columns)
        if missing:
            st.error(f"Missing columns: {', '.join(sorted(missing))}")
        else:
            prog = st.progress(0.0, text="Deciding…")
            out = []
            for i, row in enumerate(df.to_dict("records")):
                label, _ = run_one(row)
                out.append({"thread_id": row["thread_id"], "label": label})
                if i % 50 == 0:
                    prog.progress((i + 1) / len(df), text=f"Deciding… {i + 1}/{len(df)}")
            prog.empty()
            res = pd.DataFrame(out)
            st.success(f"Decided {len(res):,} threads.")
            if use_bundled:
                gold = samples.set_index("thread_id")["label"]
                acc = (res.set_index("thread_id")["label"] == gold).mean()
                st.metric("Exact-match accuracy on worked examples", f"{acc:.0%}")
            dist = res["label"].str.split(":").str[:2].str.join(":").value_counts()
            c1, c2 = st.columns([1, 1.3])
            c1.bar_chart(dist, horizontal=True)
            c2.dataframe(res, use_container_width=True, height=360)
            buf = io.StringIO()
            res.to_csv(buf, index=False)
            st.download_button("⬇ Download submission.csv", buf.getvalue(), "submission.csv", "text/csv", type="primary")

# ---------------------------------------------------------------------------- playbook
with tab3:
    st.write("Everything below is read from the playbook markdown at start-up — change a file and the agent follows.")
    cdf = pd.DataFrame([{"category": c["name"], "unit": c["unit"].replace("INR excl. GST ", ""), "GST": f"{float(c['gst']):.0%}",
                         "target": float(c["target"]), "walk-away": float(c["walkaway"]), "R": c["R"],
                         "β": int(c["beta"]), "approval limit": float(c["limit"])} for c in cats.values()])
    st.subheader("Categories")
    st.dataframe(cdf.sort_values("category"), use_container_width=True, hide_index=True)
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Dated amendments")
        st.dataframe(pd.DataFrame([{"effective": a["date"], "category": cats[a["cat"]]["name"], "change": a["key"],
                                    "new value": float(a["value"])} for a in amends]), hide_index=True, use_container_width=True)
    with c2:
        st.subheader("Suppliers with special status or contracts")
        rows = []
        for n, s in sorted(sups.items()):
            if s["status"] != "APPROVED" or s["rates"] or s["caps"]:
                bits = [f"{k} at ₹{float(v)}" for k, v in s["rates"].items()]
                bits += [f"{cats[k]['name']} cap ₹{float(v):,.0f}" for k, v in s["caps"].items()]
                rows.append({"supplier": n, "status": s["status"], "contract": "; ".join(bits) or "—"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
