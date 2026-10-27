"""
EV Market Performance & Insights - complete single-file web project (no Tableau).
Run:  python ev_project_single.py   ->  open http://127.0.0.1:5000

Pipeline: collect data -> store in SQLite -> SQL operations & calculated fields ->
data preparation -> filters/visualizations/dashboard -> story -> data explorer ->
SQL lab -> performance testing -> documentation, all served by Flask.
"""
import sys, subprocess, importlib.util
if importlib.util.find_spec("flask") is None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "flask"])

import sqlite3, random, csv, os, time, re, pathlib
from flask import Flask, jsonify, request, send_file

BASE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else "."
DB, CSV = os.path.join(BASE, "ev.db"), os.path.join(BASE, "ev.csv")
CATS = ["Hatchback", "Sedan", "SUV", "Bus", "Truck", "Two-Wheeler"]
BRANDS = {"Tesla": "USA", "BYD": "China", "Tata": "India", "Hyundai": "Korea",
          "Nissan": "Japan", "BMW": "Germany", "MG": "UK"}
BATTS = ["LFP", "NMC", "NCA", "Sodium-Ion", "Solid-State"]
YEARS = list(range(2018, 2026))
app = Flask(__name__)

# ---------- 1-3. Collect data, clean it, store in DB ----------
def build_db(n=2000):
    random.seed(1)
    rows = []
    for i in range(n):
        b = random.choice(list(BRANDS))
        rows.append((i, b, random.choice(CATS), random.choice(BATTS), BRANDS[b],
                     random.choice(YEARS), random.randint(500, 50000),
                     random.randint(40, 90) * 5, round(random.uniform(15, 120), 1),
                     round(random.uniform(3, 5), 1), random.randint(2, 80) * 1000))
    con = sqlite3.connect(DB)
    con.execute("DROP TABLE IF EXISTS ev")
    con.execute("""CREATE TABLE ev(id INT, manufacturer TEXT, category TEXT, battery TEXT,
        country TEXT, year INT, sales INT, range_km INT, capacity_kwh REAL, rating REAL, price INT)""")
    con.executemany("INSERT INTO ev VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()
    with open(CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "manufacturer", "category", "battery", "country", "year",
                    "sales", "range_km", "capacity_kwh", "rating", "price"])
        w.writerows(rows)

# ---------- 4. SQL operations & calculated fields ----------
QUERIES = {
    "kpis": "SELECT SUM(sales), SUM(sales*price), AVG(range_km), AVG(rating), COUNT(DISTINCT manufacturer) FROM ev {w}",
    "sales_by_year": "SELECT year, SUM(sales) FROM ev {w} GROUP BY year ORDER BY year",
    "battery_share": "SELECT battery, ROUND(100.0*SUM(sales)/(SELECT SUM(sales) FROM ev {w}),1) FROM ev {w} GROUP BY battery",
    "sales_by_category": "SELECT category, SUM(sales) FROM ev {w} GROUP BY category ORDER BY 2 DESC",
    "sales_by_manufacturer": "SELECT manufacturer, SUM(sales) FROM ev {w} GROUP BY manufacturer ORDER BY 2 DESC",
    "revenue_by_country": "SELECT country, SUM(sales*price) FROM ev {w} GROUP BY country ORDER BY 2 DESC",
    "range_by_category": "SELECT category, ROUND(AVG(range_km),0) FROM ev {w} GROUP BY category",
    "rating_by_category": "SELECT category, ROUND(AVG(rating),2) FROM ev {w} GROUP BY category",
    "efficiency_by_battery": "SELECT battery, ROUND(AVG(range_km/capacity_kwh),2) FROM ev {w} GROUP BY battery",
}
COLS = ["id", "manufacturer", "category", "battery", "country", "year", "sales",
        "range_km", "capacity_kwh", "rating", "price", "revenue"]

def where(a, search=True):
    c, p = [], []
    for k in ("category", "manufacturer"):
        if a.get(k): c.append(k + "=?"); p.append(a[k])
    if a.get("year"): c.append("year=?"); p.append(int(a["year"]))
    if search and a.get("q"):
        c.append("(manufacturer LIKE ? OR category LIKE ? OR battery LIKE ? OR country LIKE ?)")
        p += ["%" + a["q"] + "%"] * 4
    return ("WHERE " + " AND ".join(c) if c else ""), p

def run(sql, params=()):
    con = sqlite3.connect(DB)
    try: return con.execute(sql, params).fetchall()
    finally: con.close()

def query(name, args):
    w, p = where(args, False)
    q = QUERIES[name]
    return run(q.replace("{w}", w), p * q.count("{w}"))

@app.route("/api/data/<name>")
def data(name):
    t = time.time()
    rows = query(name, request.args)
    ms = round((time.time() - t) * 1000, 2)
    if name == "kpis":
        return jsonify(values=[v or 0 for v in rows[0]], ms=ms)
    return jsonify(labels=[r[0] for r in rows], values=[r[1] for r in rows], ms=ms)

@app.route("/api/table")
def table():
    a = request.args
    w, p = where(a)
    sort = a.get("sort") if a.get("sort") in COLS else "id"
    d = "DESC" if a.get("dir") == "desc" else "ASC"
    page = max(int(a.get("page", 1)), 1)
    total = run("SELECT COUNT(*) FROM ev " + w, p)[0][0]
    rows = run("SELECT id,manufacturer,category,battery,country,year,sales,range_km,capacity_kwh,rating,price,"
               "sales*price AS revenue FROM ev " + w + f" ORDER BY {sort} {d} LIMIT 12 OFFSET ?", p + [(page - 1) * 12])
    return jsonify(cols=COLS, rows=rows, total=total, pages=max(-(-total // 12), 1))

@app.route("/api/sql", methods=["POST"])
def sql_lab():
    q = (request.json or {}).get("sql", "").strip().rstrip(";")
    if not re.match(r"(?is)^(select|with)\b", q) or ";" in q:
        return jsonify(error="Only a single SELECT query is allowed."), 400
    t = time.time()
    try:
        con = sqlite3.connect(pathlib.Path(DB).as_uri() + "?mode=ro", uri=True)
        cur = con.execute("SELECT * FROM (" + q + ") LIMIT 200")
        cols = [d[0] for d in cur.description]; rows = cur.fetchall(); con.close()
    except Exception as e:
        return jsonify(error=str(e)), 400
    return jsonify(cols=cols, rows=rows, ms=round((time.time() - t) * 1000, 2))

@app.route("/api/perf")
def perf():
    out = []
    for name in QUERIES:
        t = time.time()
        for _ in range(5): query(name, {})
        out.append([name, round((time.time() - t) * 200, 2)])
    n = run("SELECT COUNT(*) FROM ev")[0][0]
    return jsonify(rows=n, queries=out, charts=8, filters=4, calc=5)

@app.route("/api/story")
def story():
    a = {}
    def top(name): r = query(name, a); return r[0][0], r
    cat, rc = top("sales_by_category"); tot = sum(r[1] for r in rc)
    man, rm = top("sales_by_manufacturer")
    bat, rb = top("battery_share")
    ctry, rr = top("revenue_by_country")
    yr = query("sales_by_year", a); by = max(yr, key=lambda r: r[1])
    rate = max(query("rating_by_category", a), key=lambda r: r[1])
    rng = max(query("range_by_category", a), key=lambda r: r[1])
    eff = max(query("efficiency_by_battery", a), key=lambda r: r[1])
    def sc(t, h, txt, rows, ty="bar"):
        return dict(title=t, headline=h, text=txt, type=ty, labels=[r[0] for r in rows], values=[r[1] for r in rows])
    return jsonify([
        sc("Scene 1 - Market size", f"{tot/1e6:.1f}M units sold in total",
           f"Annual sales peaked in {by[0]} at {by[1]/1e6:.1f}M units, showing how quickly the EV market scales.", yr, "line"),
        sc("Scene 2 - Segments", f"{cat} leads the categories",
           f"{cat} contributes {100*rc[0][1]/tot:.0f}% of all units, so portfolios should weight this segment first.", rc),
        sc("Scene 3 - Manufacturers", f"{man} is the top seller",
           f"{man} leads with {100*rm[0][1]/tot:.0f}% market share; the gap to the rest shows where competition is tightest.", rm),
        sc("Scene 4 - Battery technology", f"{bat} dominates adoption",
           f"{bat} holds {rb[0][1]}% of sales. {eff[0]} delivers the best range per kWh at {eff[1]} km/kWh.", rb, "doughnut"),
        sc("Scene 5 - Range & satisfaction", f"{rng[0]} offers the longest range",
           f"{rng[0]} averages {rng[1]:.0f} km while {rate[0]} earns the highest rating ({rate[1]}/5).", query("range_by_category", a)),
        sc("Scene 6 - Where value is made", f"{ctry} produces the most revenue",
           f"Manufacturing in {ctry} generates the highest revenue, guiding where to expand production.", rr),
    ])

@app.route("/download.csv")
def dl(): return send_file(CSV, as_attachment=True, download_name="ev_data.csv")

# ---------- 5-9. Web UI ----------
PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>EV Insights</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#f1f5f9;--card:#fff;--ink:#0f172a;--mute:#64748b;--line:#e2e8f0;--brand:#10b981;--navy:#0b1f3a}
*{box-sizing:border-box;margin:0}
body{font-family:Inter,Segoe UI,system-ui,sans-serif;background:var(--bg);color:var(--ink);display:flex;min-height:100vh}
aside{width:236px;background:var(--navy);color:#cbd5e1;padding:24px 14px;position:sticky;top:0;height:100vh;flex-shrink:0}
.logo{color:#fff;font-weight:700;font-size:18px;padding:0 10px 22px}.logo small{display:block;font-weight:400;font-size:11px;color:#7dd3c0;margin-top:3px}
nav a{display:flex;gap:10px;align-items:center;padding:11px 12px;border-radius:10px;color:#cbd5e1;text-decoration:none;font-size:14px;cursor:pointer;margin-bottom:4px}
nav a:hover{background:rgba(255,255,255,.08)}nav a.on{background:var(--brand);color:#04251c;font-weight:600}
.wrap{flex:1;min-width:0}
.top{display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;align-items:center;padding:20px 30px;background:#fff;border-bottom:1px solid var(--line)}
.top h1{font-size:20px}.top p{font-size:13px;color:var(--mute);margin-top:2px}
.fl{display:flex;gap:8px}
select,input,textarea,button{font:inherit;font-size:14px}
select,input{padding:8px 11px;border:1px solid var(--line);border-radius:8px;background:#fff}
button{padding:8px 14px;border-radius:8px;border:0;background:var(--brand);color:#04251c;font-weight:600;cursor:pointer}
button.g{background:#e2e8f0;color:var(--ink)}button:hover{filter:brightness(.95)}
main{padding:24px 30px}section[hidden]{display:none}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:18px}
.kpi,.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.kpi{padding:16px 18px;border-left:5px solid var(--brand)}.kpi span{font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.06em}
.kpi b{display:block;font-size:26px;margin-top:6px}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}
.card{padding:18px}.card h3{font-size:14px;margin-bottom:12px}
.c4{grid-column:span 4}.c6{grid-column:span 6}.c8{grid-column:span 8}.c12{grid-column:span 12}
.box{position:relative;height:270px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#f8fafc;text-align:left;padding:10px;cursor:pointer;white-space:nowrap;color:var(--mute);font-size:12px;text-transform:uppercase}
td{padding:9px 10px;border-top:1px solid var(--line)}tr:hover td{background:#f8fafc}
.sc{overflow-x:auto}.pager{display:flex;gap:10px;align-items:center;justify-content:space-between;margin-top:14px;font-size:13px;color:var(--mute)}
textarea{width:100%;height:120px;padding:12px;border:1px solid var(--line);border-radius:10px;font-family:Consolas,monospace;margin-bottom:10px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}.err{color:#b91c1c;font-size:13px;margin:8px 0}
.story{display:grid;grid-template-columns:1fr 1.2fr;gap:24px;align-items:center}
.story h2{font-size:26px;margin:8px 0 12px}.story p{color:var(--mute);line-height:1.6}.tag{color:var(--brand);font-weight:700;font-size:13px;text-transform:uppercase}
.dots{display:flex;gap:8px;justify-content:center;margin-top:18px}.dot{width:10px;height:10px;border-radius:50%;background:#cbd5e1;cursor:pointer}.dot.on{background:var(--brand)}
.doc li{margin:0 0 10px 20px;line-height:1.55}.doc h3{margin:16px 0 8px}code{background:#eef2f7;padding:2px 6px;border-radius:5px}
@media(max-width:1000px){body{flex-direction:column}aside{width:100%;height:auto;position:static;padding:12px}nav{display:flex;overflow-x:auto;gap:6px}nav a{white-space:nowrap}.c4,.c6,.c8{grid-column:span 12}.story{grid-template-columns:1fr}main,.top{padding:16px}}
</style></head><body>
<aside><div class=logo>&#9889; EV Insights<small>Market Analytics Platform</small></div>
<nav id=nav><a data-s=dash>&#128202; Dashboard</a><a data-s=story>&#128214; Story</a><a data-s=explorer>&#128269; Data Explorer</a>
<a data-s=sql>&#128187; SQL Lab</a><a data-s=perf>&#9889; Performance</a><a data-s=docs>&#128196; Documentation</a></nav></aside>
<div class=wrap>
<div class=top><div><h1 id=ttl></h1><p id=sub></p></div>
<div class=fl id=filters>
<select id=fc><option value="">All categories</option>__CATS__</select>
<select id=fm><option value="">All manufacturers</option>__BRANDS__</select>
<select id=fy><option value="">All years</option>__YEARS__</select>
<button class=g id=rst>Reset</button></div></div>
<main>
<section id=dash>
<div class=kpis>
<div class=kpi><span>Total Units Sold</span><b id=k0>-</b></div><div class=kpi><span>Total Revenue</span><b id=k1>-</b></div>
<div class=kpi><span>Avg Range (km)</span><b id=k2>-</b></div><div class=kpi><span>Avg Rating</span><b id=k3>-</b></div>
<div class=kpi><span>Manufacturers</span><b id=k4>-</b></div></div>
<div class=grid>
<div class="card c8"><h3>Annual Sales Trend</h3><div class=box><canvas id=sales_by_year></canvas></div></div>
<div class="card c4"><h3>Battery Technology Share (%)</h3><div class=box><canvas id=battery_share></canvas></div></div>
<div class="card c4"><h3>Sales by Category</h3><div class=box><canvas id=sales_by_category></canvas></div></div>
<div class="card c4"><h3>Sales by Manufacturer</h3><div class=box><canvas id=sales_by_manufacturer></canvas></div></div>
<div class="card c4"><h3>Revenue by Manufacturing Country</h3><div class=box><canvas id=revenue_by_country></canvas></div></div>
<div class="card c4"><h3>Average Range by Category (km)</h3><div class=box><canvas id=range_by_category></canvas></div></div>
<div class="card c4"><h3>Customer Rating by Category</h3><div class=box><canvas id=rating_by_category></canvas></div></div>
<div class="card c4"><h3>Range per kWh by Battery (km/kWh)</h3><div class=box><canvas id=efficiency_by_battery></canvas></div></div>
</div></section>

<section id=story hidden><div class=card><div class=story><div><div class=tag id=st_t></div><h2 id=st_h></h2><p id=st_p></p>
<div style="margin-top:20px"><button class=g id=prev>&larr; Back</button> <button id=next>Next &rarr;</button></div></div>
<div class=box style="height:340px"><canvas id=st_c></canvas></div></div><div class=dots id=dots></div></div></section>

<section id=explorer hidden><div class=card>
<div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap"><input id=q placeholder="Search manufacturer, category, battery, country..." style="flex:1;min-width:220px">
<a href="/download.csv"><button>&#11015; Download CSV</button></a></div>
<div class=sc><table><thead id=th></thead><tbody id=tb></tbody></table></div>
<div class=pager><span id=info></span><span><button class=g id=pp>&larr;</button> <span id=pg></span> <button class=g id=pn>&rarr;</button></span></div></div></section>

<section id=sql hidden><div class=card><h3>Run read-only SQL on table <code>ev</code></h3>
<div class=chips id=chips></div><textarea id=sqlt></textarea>
<button id=runq>&#9654; Run query</button> <span id=sqlm style="color:var(--mute);font-size:13px"></span>
<div class=err id=sqle></div><div class=sc><table><thead id=sh></thead><tbody id=sb></tbody></table></div></div></section>

<section id=perf hidden><div class=kpis id=pk></div><div class=card><h3>Query response time in ms (average of 5 runs)</h3>
<div class=box><canvas id=pc></canvas></div></div></section>

<section id=docs hidden><div class="card doc"><h3>About the project</h3>
<p style="color:var(--mute);line-height:1.6">An end-to-end analytics website for the EV market, serving Market Strategy (Emily), Business Intelligence (David) and Research (Dr. Williams) needs: category and manufacturer performance, battery technology adoption, range, ratings and manufacturing geography.</p>
<h3>Development steps</h3><ol>
<li><b>Problem understanding</b> - which categories, manufacturers and battery technologies drive sales, range and satisfaction.</li>
<li><b>Data collection</b> - 2,000 EV records generated by <code>build_db()</code>; replace with a real CSV if available.</li>
<li><b>Storage</b> - SQLite database <code>ev.db</code>, table <code>ev</code>; CSV copy in <code>ev.csv</code>.</li>
<li><b>SQL operations</b> - GROUP BY, SUM, AVG, subqueries and calculated fields: revenue, market share %, range per kWh, average rating.</li>
<li><b>Data preparation</b> - typed columns, consistent categories, derived fields, parameterised filters.</li>
<li><b>Visualizations &amp; dashboard</b> - 8 charts and 5 KPI cards with category, manufacturer and year filters, responsive layout.</li>
<li><b>Story</b> - 6 data-driven scenes that update automatically from the database.</li>
<li><b>Data Explorer &amp; SQL Lab</b> - searchable, sortable, paged table; read-only SQL console.</li>
<li><b>Performance testing</b> - Performance tab measures rows, charts, filters, calculated fields and query timings.</li>
<li><b>Web integration</b> - Flask serves the REST APIs and this interface from a single file.</li></ol>
<h3>Run it</h3><p><code>python ev_project_single.py</code> then open <code>http://127.0.0.1:5000</code></p></div></section>
</main></div>
<script>
const $=id=>document.getElementById(id),nf=new Intl.NumberFormat('en',{notation:'compact',maximumFractionDigits:1});
Chart.defaults.font.family="Inter,Segoe UI,sans-serif";Chart.defaults.plugins.legend.display=false;
const P=['#10b981','#0ea5e9','#6366f1','#f59e0b','#ef4444','#14b8a6','#8b5cf6','#ec4899'];
const TITLES={dash:['Executive Dashboard','Market performance across categories, manufacturers and battery technologies'],
story:['Market Story','Six scenes that walk through the key insights'],explorer:['Data Explorer','Search, sort and download the underlying records'],
sql:['SQL Lab','Query the database directly'],perf:['Performance Testing','Data volume, features and query speed'],docs:['Documentation','Step-by-step project development procedure']};
const qs=()=>'?category='+encodeURIComponent($('fc').value)+'&manufacturer='+encodeURIComponent($('fm').value)+'&year='+$('fy').value;
const get=u=>fetch(u).then(r=>r.json()),ch={};
function chart(id,type,labels,values,opt={}){
  const pie=type=='doughnut',line=type=='line';ch[id]&&ch[id].destroy();
  ch[id]=new Chart($(id),{type,data:{labels,datasets:[{data:values,borderRadius:6,tension:.35,fill:line,
    backgroundColor:pie?P:(line?'rgba(16,185,129,.15)':(opt.multi?P:P[0])),borderColor:line?P[0]:'#fff',borderWidth:pie?2:(line?3:0),pointRadius:line?4:0}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:pie,position:'bottom'}},
    scales:pie?{}:{x:{grid:{display:false}},y:{grid:{color:'#eef2f7'},ticks:{callback:v=>nf.format(v)}}}}});}
const TYPE={sales_by_year:'line',battery_share:'doughnut'};
async function dash(){
  const q=qs(),k=await get('/api/data/kpis'+q);
  $('k0').textContent=nf.format(k.values[0]);$('k1').textContent='$'+nf.format(k.values[1]);
  $('k2').textContent=Math.round(k.values[2]);$('k3').textContent=k.values[3].toFixed(2)+' / 5';$('k4').textContent=k.values[4];
  for(const id of ['sales_by_year','battery_share','sales_by_category','sales_by_manufacturer','revenue_by_country','range_by_category','rating_by_category','efficiency_by_battery']){
    const r=await get('/api/data/'+id+q);chart(id,TYPE[id]||'bar',r.labels,r.values,{multi:id=='sales_by_manufacturer'});}}
let S=[],si=0;
async function story(){if(!S.length)S=await get('/api/story');scene(0);}
function scene(i){si=i;const s=S[i];$('st_t').textContent=s.title;$('st_h').textContent=s.headline;$('st_p').textContent=s.text;
  chart('st_c',s.type,s.labels,s.values,{multi:true});
  $('dots').innerHTML=S.map((_,j)=>'<span class="dot'+(j==i?' on':'')+'" onclick="scene('+j+')"></span>').join('');}
$('prev').onclick=()=>scene(Math.max(si-1,0));$('next').onclick=()=>scene(Math.min(si+1,S.length-1));
let pg=1,pages=1,sort='id',dir='asc';
async function explorer(){const r=await get('/api/table'+qs()+'&q='+encodeURIComponent($('q').value)+'&page='+pg+'&sort='+sort+'&dir='+dir);
  pages=r.pages;$('th').innerHTML='<tr>'+r.cols.map(c=>'<th onclick="srt(\''+c+'\')">'+c.replace('_',' ')+(c==sort?(dir=='asc'?' &#9650;':' &#9660;'):'')+'</th>').join('')+'</tr>';
  $('tb').innerHTML=r.rows.map(x=>'<tr>'+x.map(v=>'<td>'+(typeof v=='number'&&v>99999?v.toLocaleString():v)+'</td>').join('')+'</tr>').join('');
  $('info').textContent=r.total.toLocaleString()+' records';$('pg').textContent='Page '+pg+' of '+pages;}
function srt(c){dir=(sort==c&&dir=='asc')?'desc':'asc';sort=c;pg=1;explorer();}
$('pp').onclick=()=>{pg=Math.max(pg-1,1);explorer()};$('pn').onclick=()=>{pg=Math.min(pg+1,pages);explorer()};
$('q').oninput=()=>{pg=1;explorer()};
const PRE={'Top manufacturers':'SELECT manufacturer, SUM(sales) AS units, SUM(sales*price) AS revenue FROM ev GROUP BY manufacturer ORDER BY units DESC',
'Battery efficiency':'SELECT battery, ROUND(AVG(range_km/capacity_kwh),2) AS km_per_kwh FROM ev GROUP BY battery ORDER BY km_per_kwh DESC',
'Yearly sales':'SELECT year, SUM(sales) AS units FROM ev GROUP BY year ORDER BY year','Best rated':'SELECT category, ROUND(AVG(rating),2) AS rating FROM ev GROUP BY category ORDER BY rating DESC'};
$('chips').innerHTML=Object.keys(PRE).map(k=>'<button class=g onclick="$(\'sqlt\').value=PRE[\''+k+'\'];runq()">'+k+'</button>').join('');
$('sqlt').value=PRE['Top manufacturers'];
async function runq(){$('sqle').textContent='';const r=await fetch('/api/sql',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql:$('sqlt').value})});
  const d=await r.json();if(d.error){$('sqle').textContent=d.error;return;}
  $('sqlm').textContent=d.rows.length+' rows in '+d.ms+' ms';$('sh').innerHTML='<tr>'+d.cols.map(c=>'<th>'+c+'</th>').join('')+'</tr>';
  $('sb').innerHTML=d.rows.map(x=>'<tr>'+x.map(v=>'<td>'+v+'</td>').join('')+'</tr>').join('');}
$('runq').onclick=runq;
async function perf(){const d=await get('/api/perf');
  $('pk').innerHTML=[['Rows in DB',d.rows.toLocaleString()],['Visualizations',d.charts],['Filters',d.filters],['Calculated fields',d.calc]].map(x=>'<div class=kpi><span>'+x[0]+'</span><b>'+x[1]+'</b></div>').join('');
  chart('pc','bar',d.queries.map(x=>x[0]),d.queries.map(x=>x[1]));}
const LOAD={dash,story,explorer,perf,sql:runq};
function show(id){document.querySelectorAll('section').forEach(s=>s.hidden=s.id!=id);
  document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('on',a.dataset.s==id));
  $('ttl').textContent=TITLES[id][0];$('sub').textContent=TITLES[id][1];
  $('filters').style.display=(id=='dash'||id=='explorer')?'flex':'none';$('q').style.display=id=='explorer'?'':'none';
  location.hash=id;LOAD[id]&&LOAD[id]();}
document.querySelectorAll('nav a').forEach(a=>a.onclick=()=>show(a.dataset.s));
['fc','fm','fy'].forEach(i=>$(i).onchange=()=>{pg=1;location.hash=='#explorer'?explorer():dash()});
$('rst').onclick=()=>{$('fc').value=$('fm').value=$('fy').value='';$('fc').onchange()};
show((location.hash||'#dash').slice(1));
</script></body></html>"""

@app.route("/")
def home():
    opt = lambda xs: "".join(f"<option>{x}</option>" for x in xs)
    return PAGE.replace("__CATS__", opt(CATS)).replace("__BRANDS__", opt(BRANDS)).replace("__YEARS__", opt(YEARS))

if __name__ == "__main__":
    build_db()  # rebuild so schema/data always match this version
    print("Open http://127.0.0.1:5000 in your browser")
    app.run(host="0.0.0.0", port=5000, debug=False)
