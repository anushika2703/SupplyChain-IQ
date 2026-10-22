import sqlite3
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st
from src.pipeline import run_pipeline

ROOT=Path(__file__).resolve().parent
DB=ROOT/"data"/"processed"/"supplychain_iq.db"
st.set_page_config(page_title="SupplyChain IQ",page_icon="📦",layout="wide")
st.title("SupplyChain IQ | Supplier & Inventory Risk Analytics")
st.caption("Portfolio demo using synthetic procurement and inventory data. Not affiliated with Volvo Group.")
if st.sidebar.button("Run / refresh data pipeline",type="primary"):
    with st.spinner("Validating, joining and loading data..."): summary=run_pipeline()
    st.sidebar.success(f"Pipeline completed: {summary['clean_purchase_orders']:,} purchase orders.")
if not DB.exists():
    with st.spinner("Preparing synthetic data..."): run_pipeline()
with sqlite3.connect(DB) as c:
    po=pd.read_sql_query("SELECT * FROM purchase_orders_clean",c,parse_dates=["order_date"])
    suppliers=pd.read_sql_query("SELECT * FROM supplier_kpis",c)
    inv=pd.read_sql_query("SELECT * FROM inventory_risk",c)
    risk=pd.read_sql_query("SELECT * FROM purchase_order_risk",c)
    quality=pd.read_sql_query("SELECT * FROM pipeline_quality",c)
st.sidebar.header("Filters")
countries=sorted(po.supplier_country.dropna().unique())
country_sel=st.sidebar.multiselect("Supplier country",countries,default=countries)
tiers=sorted(po.supplier_tier.dropna().unique())
tier_sel=st.sidebar.multiselect("Supplier tier",tiers,default=tiers)
cats=sorted(inv.category.dropna().unique())
cat_sel=st.sidebar.multiselect("Product category",cats,default=cats)
f=po[po.supplier_country.isin(country_sel)&po.supplier_tier.isin(tier_sel)]
iv=inv[inv.category.isin(cat_sel)]
rf=risk[risk.supplier_country.isin(country_sel)&risk.supplier_tier.isin(tier_sel)]
n=len(f); ontime=(~f.is_late.astype(bool)).mean()*100 if n else 0
a,b,c,d=st.columns(4)
a.metric("Purchase orders",f"{n:,}"); b.metric("On-time delivery",f"{ontime:.1f}%")
c.metric("Late orders",f"{int(f.is_late.sum()):,}"); d.metric("Inventory alerts",f"{int((iv.inventory_status!='Healthy').sum()):,}")
e,g=st.columns(2)
e.metric("Purchase order value",f"${f.order_value_usd.sum():,.0f}")
g.metric("High-risk late orders",f"{int((rf.risk_reason=='High: 7+ Day Delay').sum()):,}")
st.header("Supplier performance")
left,right=st.columns(2)
with left:
    s=f.groupby(["supplier_id","supplier_name"],as_index=False).agg(on_time_delivery_pct=("is_late",lambda x:(~x.astype(bool)).mean()*100))
    fig=px.bar(s.sort_values("on_time_delivery_pct"),x="supplier_name",y="on_time_delivery_pct",color="on_time_delivery_pct",title="Supplier on-time delivery (%)")
    fig.update_layout(xaxis_tickangle=-45,yaxis_range=[0,100]); st.plotly_chart(fig,use_container_width=True)
with right:
    ct=f.groupby("supplier_country",as_index=False).agg(purchase_orders=("po_id","count"),avg_delay_days=("delay_days","mean"))
    st.plotly_chart(px.bar(ct,x="supplier_country",y="purchase_orders",color="avg_delay_days",title="Orders by supplier country"),use_container_width=True)
st.header("Inventory risk")
left,right=st.columns(2)
with left:
    status=iv.inventory_status.value_counts().rename_axis("status").reset_index(name="products")
    st.plotly_chart(px.pie(status,names="status",values="products",hole=.35,title="Inventory status distribution"),use_container_width=True)
with right:
    cat=iv.groupby("category",as_index=False).agg(inventory_value_usd=("inventory_value_usd","sum"),avg_days_cover=("days_of_cover","mean"))
    st.plotly_chart(px.bar(cat,x="category",y="inventory_value_usd",color="avg_days_cover",title="Inventory value by category"),use_container_width=True)
st.header("Operational worklists")
t1,t2,t3=st.tabs(["Inventory alerts","Late purchase orders","Supplier scorecard"])
with t1:
    alert=iv[iv.inventory_status!="Healthy"].sort_values("days_of_cover")
    cols=["product_id","product_name","category","stock_on_hand_units","reorder_point_units","safety_stock_units","avg_daily_demand_units","open_po_units","days_of_cover","inventory_status","inventory_value_usd"]
    st.dataframe(alert[cols],use_container_width=True,hide_index=True)
    st.download_button("Download inventory alerts CSV",alert[cols].to_csv(index=False).encode(),"inventory_alerts.csv","text/csv")
with t2:
    cols=["po_id","order_date","supplier_name","supplier_country","product_name","quantity_units","promised_lead_time_days","actual_lead_time_days","delay_days","order_value_usd","risk_reason"]
    st.dataframe(rf[cols].sort_values("delay_days",ascending=False),use_container_width=True,hide_index=True)
    st.download_button("Download late orders CSV",rf[cols].to_csv(index=False).encode(),"purchase_order_risk.csv","text/csv")
with t3:
    score=suppliers[suppliers.supplier_country.isin(country_sel)&suppliers.supplier_tier.isin(tier_sel)]
    st.dataframe(score.sort_values("on_time_delivery_pct"),use_container_width=True,hide_index=True)
    st.download_button("Download supplier scorecard CSV",score.to_csv(index=False).encode(),"supplier_scorecard.csv","text/csv")
with st.expander("Pipeline quality audit"):
    st.dataframe(quality,use_container_width=True,hide_index=True)
with st.expander("Metric definitions and assumptions"):
    st.markdown("- On-time: actual lead time <= promised lead time.\\n- Days of cover: (stock on hand + open PO units) / average daily demand.\\n- High risk: delivery delay of 7+ days.\\n- Inventory labels use configured safety-stock, reorder-point, cover, and excess-stock rules.\\n- All initial records are synthetic; outputs do not represent real supplier performance.")
