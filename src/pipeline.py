from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW, PROCESSED, REPORTS = ROOT/"data"/"raw", ROOT/"data"/"processed", ROOT/"reports"
DB = PROCESSED/"supplychain_iq.db"

def generate_demo_data(seed=21, n_products=90, n_suppliers=24, n_orders=950):
    rng = np.random.default_rng(seed)
    today = pd.Timestamp.today().normalize()
    suppliers = pd.DataFrame({
        "supplier_id":[f"SUP-{i:03d}" for i in range(1,n_suppliers+1)],
        "supplier_name":[f"Supplier {i:02d}" for i in range(1,n_suppliers+1)],
        "supplier_country":rng.choice(["India","Germany","Sweden","Poland","China","Mexico"],n_suppliers),
        "supplier_tier":rng.choice(["Strategic","Preferred","Standard"],n_suppliers,p=[.2,.35,.45]),
        "contracted_lead_time_days":rng.integers(3,22,n_suppliers)})
    products = pd.DataFrame({
        "product_id":[f"PRD-{i:04d}" for i in range(1,n_products+1)],
        "product_name":[f"Component {i:03d}" for i in range(1,n_products+1)],
        "category":rng.choice(["Powertrain","Electrical","Hydraulics","Fasteners","Packaging"],n_products),
        "unit_cost_usd":np.round(np.exp(rng.normal(3.7,.65,n_products)),2),
        "reorder_point_units":rng.integers(25,140,n_products),
        "safety_stock_units":rng.integers(10,70,n_products)})
    inventory = products[["product_id"]].copy()
    inventory["stock_on_hand_units"] = rng.integers(0,260,n_products)
    inventory["avg_daily_demand_units"] = np.round(rng.uniform(1,12,n_products),2)
    inventory["open_po_units"] = rng.integers(0,180,n_products)
    inventory["last_updated"] = today-pd.to_timedelta(rng.integers(0,21,n_products),unit="D")
    dates = today-pd.to_timedelta(rng.integers(0,180,n_orders),unit="D")
    si, pi = rng.integers(0,n_suppliers,n_orders), rng.integers(0,n_products,n_orders)
    qty = rng.integers(10,350,n_orders)
    promised = np.maximum(1,np.array([int(suppliers.iloc[i].contracted_lead_time_days) for i in si])+rng.integers(-2,5,n_orders))
    actual = np.maximum(1,promised+rng.choice([-4,-2,-1,0,1,2,4,7,12],n_orders,p=[.025,.05,.10,.24,.18,.16,.12,.08,.045])).astype(float)
    orders = pd.DataFrame({
        "po_id":[f"PO-{i:06d}" for i in range(1,n_orders+1)],"order_date":dates,
        "supplier_id":[suppliers.iloc[i].supplier_id for i in si],
        "product_id":[products.iloc[i].product_id for i in pi],"quantity_units":qty,
        "unit_cost_usd":[products.iloc[i].unit_cost_usd for i in pi],
        "promised_lead_time_days":promised,"actual_lead_time_days":actual})
    orders.loc[6,"quantity_units"]=-15
    orders.loc[14,"unit_cost_usd"]=np.nan
    orders.loc[21,"po_id"]=orders.loc[20,"po_id"]
    orders.loc[29,"actual_lead_time_days"]=np.nan
    return {"suppliers":suppliers,"products":products,"inventory":inventory,"purchase_orders":orders}

def load_raw():
    paths={n:RAW/f"{n}.csv" for n in ["suppliers","products","inventory","purchase_orders"]}
    if all(p.exists() for p in paths.values()):
        return {n:pd.read_csv(p) for n,p in paths.items()}, "user-provided raw CSV files"
    data=generate_demo_data()
    RAW.mkdir(parents=True,exist_ok=True)
    for n,df in data.items(): df.to_csv(paths[n],index=False)
    return data, "generated synthetic demo data"

def run_pipeline():
    for p in [RAW,PROCESSED,REPORTS]: p.mkdir(parents=True,exist_ok=True)
    data,source=load_raw()
    sup,prod,inv,po=(data[k].copy() for k in ["suppliers","products","inventory","purchase_orders"])
    raw_n=len(po)
    po["order_date"]=pd.to_datetime(po["order_date"],errors="coerce")
    for c in ["quantity_units","unit_cost_usd","promised_lead_time_days","actual_lead_time_days"]:
        po[c]=pd.to_numeric(po[c],errors="coerce")
    dup=int(po["po_id"].duplicated().sum())
    bad_qty=int((po.quantity_units<=0).sum())
    bad_cost=int((po.unit_cost_usd<=0).sum())
    po=po.dropna(subset=["po_id","order_date","supplier_id","product_id"])
    po=po[(po.quantity_units>0)&(po.unit_cost_usd>0)].drop_duplicates("po_id",keep="first")
    missing=int(po.actual_lead_time_days.isna().sum())
    po["actual_lead_time_days"]=po.actual_lead_time_days.fillna(po.promised_lead_time_days)
    po["promised_lead_time_days"]=po.promised_lead_time_days.fillna(po.promised_lead_time_days.median())
    po["delay_days"]=(po.actual_lead_time_days-po.promised_lead_time_days).round(1)
    po["is_late"]=po.delay_days>0
    po["order_value_usd"]=(po.quantity_units*po.unit_cost_usd).round(2)
    po=po.merge(sup,on="supplier_id",how="left",validate="many_to_one")
    po=po.merge(prod[["product_id","product_name","category","reorder_point_units","safety_stock_units"]],on="product_id",how="left",validate="many_to_one")
    sk=po.groupby(["supplier_id","supplier_name","supplier_country","supplier_tier"],as_index=False).agg(
        purchase_orders=("po_id","count"),on_time_delivery_pct=("is_late",lambda s:round((~s).mean()*100,2)),
        avg_lead_time_days=("actual_lead_time_days","mean"),avg_delay_days=("delay_days","mean"),spend_usd=("order_value_usd","sum"))
    sk[["avg_lead_time_days","avg_delay_days","spend_usd"]]=sk[["avg_lead_time_days","avg_delay_days","spend_usd"]].round(2)
    inv=inv.merge(prod,on="product_id",how="left",validate="one_to_one")
    inv["days_of_cover"]=(inv.stock_on_hand_units+inv.open_po_units)/inv.avg_daily_demand_units.replace(0,np.nan)
    inv["inventory_status"]=np.select([
        inv.stock_on_hand_units<=inv.safety_stock_units,
        inv.stock_on_hand_units<=inv.reorder_point_units,
        inv.days_of_cover<7,
        inv.stock_on_hand_units>inv.reorder_point_units*2.5],
        ["Critical: Safety Stock","Reorder Now","Low Cover","Potential Excess"],default="Healthy")
    inv["inventory_value_usd"]=(inv.stock_on_hand_units*inv.unit_cost_usd).round(2)
    risk=po.copy()
    risk["risk_reason"]=np.select([risk.delay_days>=7,risk.delay_days>0],["High: 7+ Day Delay","Medium: Late Delivery"],default="On Track")
    risk=risk[risk.risk_reason!="On Track"].copy()
    quality=pd.DataFrame([{"raw_purchase_orders":raw_n,"clean_purchase_orders":len(po),
        "removed_purchase_orders":raw_n-len(po),"duplicate_po_ids_detected":dup,
        "invalid_quantities_detected":bad_qty,"invalid_costs_detected":bad_cost,
        "missing_actual_lead_times_imputed":missing}])
    with sqlite3.connect(DB) as c:
        for name,df in {"suppliers":sup,"products":prod,"inventory_risk":inv,"purchase_orders_clean":po,
                        "supplier_kpis":sk,"purchase_order_risk":risk,"pipeline_quality":quality}.items():
            df.to_sql(name,c,if_exists="replace",index=False)
    inv.to_csv(PROCESSED/"inventory_risk.csv",index=False)
    po.to_csv(PROCESSED/"purchase_orders_clean.csv",index=False)
    sk.to_csv(REPORTS/"supplier_scorecard.csv",index=False)
    risk.to_csv(REPORTS/"purchase_order_risk.csv",index=False)
    inv[inv.inventory_status!="Healthy"].to_csv(REPORTS/"inventory_alerts.csv",index=False)
    summary={"source":source,"raw_purchase_orders":raw_n,"clean_purchase_orders":len(po),
        "suppliers_analyzed":int(po.supplier_id.nunique()),"on_time_delivery_pct":round(float((~po.is_late).mean()*100),2),
        "late_purchase_orders":int(po.is_late.sum()),"inventory_alerts":int((inv.inventory_status!="Healthy").sum()),
        "high_risk_purchase_orders":int((risk.risk_reason=="High: 7+ Day Delay").sum()),**quality.iloc[0].to_dict()}
    pd.DataFrame([summary]).to_csv(REPORTS/"pipeline_summary.csv",index=False)
    return summary
