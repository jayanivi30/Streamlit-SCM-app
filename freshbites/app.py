import streamlit as st
import pandas as pd
import numpy as np
import altair as alt

suppliers = pd.read_csv("suppliers.csv")
inventory = pd.read_csv("inventory.csv")

st.set_page_config(page_title="FreshBites – Supply Health", page_icon="🍞", layout="wide")

@st.cache_data
def load_csv(path):
    return pd.read_csv(path)

st.title("🍞 FreshBites – Supplier Reliability & Raw Material Availability")
st.caption("One-click health check for key ingredients and suppliers")

with st.sidebar:
    st.header("Upload Data (optional)")
    up_sup = st.file_uploader("suppliers.csv", type=["csv"], help="Columns: supplier_id, supplier_name, total_deliveries, on_time_deliveries, avg_lead_time_days, price_index, priority, is_backup")
    up_inv = st.file_uploader("inventory.csv", type=["csv"], help="Columns: material, current_stock, safety_stock, avg_daily_usage, primary_supplier, backup_supplier, lead_time_days")
    st.markdown("---")
    st.subheader("Simulation")
    sim_delay = st.checkbox("Simulate delay for Supplier B (reduce on-time by 2)")
    sim_demand_spike = st.checkbox("Simulate demand spike for Flour (+30% usage)")

# Load defaults from /data if user didn't upload
try:
    default_suppliers = load_csv("data/suppliers.csv")
    default_inventory = load_csv("data/inventory.csv")
except Exception:
    default_suppliers = pd.DataFrame(columns=["supplier_id","supplier_name","total_deliveries","on_time_deliveries","avg_lead_time_days","price_index","priority","is_backup"])
    default_inventory = pd.DataFrame(columns=["material","current_stock","safety_stock","avg_daily_usage","primary_supplier","backup_supplier","lead_time_days"])

suppliers = load_csv(up_sup) if up_sup else default_suppliers.copy()
inventory = load_csv(up_inv) if up_inv else default_inventory.copy()

# Basic validations
required_sup_cols = {"supplier_id","supplier_name","total_deliveries","on_time_deliveries","avg_lead_time_days","price_index","priority","is_backup"}
required_inv_cols = {"material","current_stock","safety_stock","avg_daily_usage","primary_supplier","backup_supplier","lead_time_days"}

if not required_sup_cols.issubset(set(suppliers.columns)) or not required_inv_cols.issubset(set(inventory.columns)):
    st.error("Please provide valid CSVs with required columns (see sidebar).")
    st.stop()

# Apply simulations
if sim_delay and "Supplier B" in suppliers["supplier_name"].values:
    idx = suppliers.index[suppliers["supplier_name"]=="Supplier B"]
    suppliers.loc[idx, "on_time_deliveries"] = (suppliers.loc[idx, "on_time_deliveries"] - 2).clip(lower=0)

if sim_demand_spike and "Flour" in inventory["material"].values:
    idx = inventory.index[inventory["material"]=="Flour"]
    inventory.loc[idx, "avg_daily_usage"] = (inventory.loc[idx, "avg_daily_usage"] * 1.3).round(2)

# Compute reliability
suppliers = suppliers.copy()
suppliers["reliability_pct"] = (suppliers["on_time_deliveries"] / suppliers["total_deliveries"]).replace([np.inf, -np.inf], np.nan).fillna(0.0) * 100
suppliers["reliability_band"] = pd.cut(
    suppliers["reliability_pct"],
    bins=[-0.1, 74.99, 89.99, 100],
    labels=["Risk (<75%)", "Watch (75-89%)", "Reliable (90%+)"]
)

# Join supplier reliability into inventory (primary & backup)
rel_map = dict(zip(suppliers["supplier_name"], suppliers["reliability_pct"]))
lt_map = dict(zip(suppliers["supplier_name"], suppliers["avg_lead_time_days"]))

inv = inventory.copy()
inv["primary_reliability"] = inv["primary_supplier"].map(rel_map).fillna(0.0)
inv["backup_reliability"] = inv["backup_supplier"].map(rel_map).fillna(0.0)

# If you want lead time per material, prefer column. Else fallback to supplier lead time
inv["lead_time_days"] = np.where(inv["lead_time_days"].isna() | (inv["lead_time_days"]<=0),
                                 inv["primary_supplier"].map(lt_map).fillna(5),
                                 inv["lead_time_days"])

# Health metrics
inv["days_of_cover"] = (inv["current_stock"] / inv["avg_daily_usage"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)
inv["reorder_point"] = inv["safety_stock"] + inv["lead_time_days"] * inv["avg_daily_usage"]
# Ensure numeric values
inv["reorder_point"] = pd.to_numeric(inv["reorder_point"], errors="coerce")
inv["current_stock"] = pd.to_numeric(inv["current_stock"], errors="coerce")

# Fill NaNs (in case of invalid/missing values)
inv["reorder_point"].fillna(0, inplace=True)
inv["current_stock"].fillna(0, inplace=True)

# Calculate reorder quantity
inv["reorder_qty"] = (inv["reorder_point"] - inv["current_stock"]).clip(lower=0).round(0).astype(int)


# Risk rules
inv["stock_risk"] = np.where(inv["current_stock"] < inv["safety_stock"], "Low Stock", "OK")
inv["supply_risk"] = np.where(inv["primary_reliability"] < 75, "Unreliable Supplier", "OK")
inv["overall_risk"] = np.where((inv["stock_risk"]!="OK") | (inv["supply_risk"]!="OK"), "⚠ Risk", "✅ OK")

# Recommendation logic
def recommend(row):
    recs = []
    if row["current_stock"] < row["reorder_point"]:
        qty = int(row["reorder_qty"])
        target = row["primary_supplier"]
        # switch to backup if backup more reliable
        if row["backup_reliability"] > row["primary_reliability"]:
            target = f"{row['backup_supplier']} (backup)"
        recs.append(f"Reorder {qty} units of {row['material']} from {target}.")
    if row["primary_reliability"] < 75:
        if row["backup_reliability"] > row["primary_reliability"]:
            recs.append(f"Switch supplier for {row['material']} to {row['backup_supplier']} (more reliable).")
        else:
            recs.append(f"Keep primary for {row['material']} but increase safety stock temporarily.")
    if row["days_of_cover"] < row["lead_time_days"]:
        recs.append(f"Increase safety stock for {row['material']} (days of cover {row['days_of_cover']:.1f} < lead time {row['lead_time_days']}).")
    return " ".join(recs) if recs else "No action needed."

inv["recommendation"] = inv.apply(recommend, axis=1)

# KPIs
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Suppliers", len(suppliers))
with col2:
    st.metric("Avg Reliability", f"{suppliers['reliability_pct'].mean():.1f}%")
with col3:
    st.metric("At-Risk Materials", int((inv['overall_risk']!='✅ OK').sum()))
with col4:
    st.metric("Total Suggested Reorder", int(inv["reorder_qty"].sum()))

st.subheader("Supplier Reliability")
reliability_chart = alt.Chart(suppliers).mark_bar().encode(
    x=alt.X('supplier_name:N', title='Supplier'),
    y=alt.Y('reliability_pct:Q', title='Reliability %', scale=alt.Scale(domain=[0,100])),
    color=alt.condition(
    alt.datum.reliability_pct >= 75, alt.value('#2ca02c'), alt.value('#d62728')
),
    tooltip=['supplier_name','reliability_pct','avg_lead_time_days','total_deliveries','on_time_deliveries']
).properties(height=300)
st.altair_chart(reliability_chart, use_container_width=True)

st.subheader("Inventory Health")
show_cols = ["material","current_stock","safety_stock","avg_daily_usage","days_of_cover","lead_time_days",
             "primary_supplier","primary_reliability","backup_supplier","backup_reliability",
             "reorder_point","reorder_qty","overall_risk","recommendation"]
st.dataframe(inv[show_cols], use_container_width=True, hide_index=True)

st.markdown("### Recommendations")
for _, r in inv.iterrows():
    st.write(f"- **{r['material']}**: {r['recommendation']}")

st.success("Tip: Use the sidebar to simulate delays or demand spikes for a live demo.")
