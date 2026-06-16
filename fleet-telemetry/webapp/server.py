#!/usr/bin/env python3
"""Tiny stdlib web dashboard for the Tesla Fleet Telemetry add-on.

Tails the fleet-telemetry logger output (JSON lines written to RECORDS_FILE by `tee`),
keeps the latest value per telemetry field per VIN plus a little history, and serves an
ingress dashboard. Read-only and isolated: if this process dies, the telemetry server is
unaffected.
"""

import json
import os
import re
import subprocess
import threading
import time

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECORDS_FILE = os.environ.get("FT_RECORDS_FILE", "/tmp/ft-records.jsonl")
CERT_FILE = os.environ.get("FT_CERT_FILE", "/data/certs/server.crt")
PORT = int(os.environ.get("FT_WEB_PORT", "8099"))
NAMESPACE = os.environ.get("FT_NAMESPACE", "tesla_telemetry")
ADDON_VERSION = os.environ.get("FT_ADDON_VERSION", "")
HISTORY_MAX = 600  # ~ last N samples kept per series for sparklines

START_TIME = time.time()
_META = {"CreatedAt", "IsResend", "Vin"}

# ---------------------------------------------------------------------------
# Shared state (guarded by _lock)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
# vin -> { field -> {value, created_at, received_at} }
_latest = defaultdict(dict)
# vin -> { "soc": deque[(ts, val)], "speed": deque[(ts, val)] }
_history = defaultdict(lambda: {"soc": deque(maxlen=HISTORY_MAX),
                                "speed": deque(maxlen=HISTORY_MAX)})
_record_times = deque(maxlen=5000)   # epoch seconds of every record, for rate stats
_total_records = 0
_client_versions = {}                # vin -> device_client_version
_last_record_epoch = 0.0


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_location(val):
    """Return (lat, lon) from a Location field value in whatever shape it arrives."""
    if isinstance(val, dict):
        lat = val.get("latitude", val.get("Latitude"))
        lon = val.get("longitude", val.get("Longitude"))
        if lat is not None and lon is not None:
            return _num(lat), _num(lon)
    if isinstance(val, str) and "," in val:
        parts = val.split(",")
        if len(parts) == 2:
            return _num(parts[0]), _num(parts[1])
    return None, None


def _ingest(obj):
    global _total_records, _last_record_epoch
    if obj.get("msg") != "record_payload":
        return
    data = obj.get("data") or {}
    vin = obj.get("vin") or data.get("Vin") or "unknown"
    # Validate VIN format; fall back to a safe label rather than trusting arbitrary input.
    if not (isinstance(vin, str) and _VIN_RE.match(vin)):
        vin = "unknown"
    created = data.get("CreatedAt", "")
    now = time.time()
    meta = obj.get("metadata") or {}
    with _lock:
        _total_records += 1
        _last_record_epoch = now
        _record_times.append(now)
        if meta.get("device_client_version"):
            _client_versions[vin] = meta["device_client_version"]
        for key, value in data.items():
            if key in _META:
                continue
            _latest[vin][key] = {"value": value, "created_at": created, "received_at": now}
            if key == "Soc":
                n = _num(value)
                if n is not None:
                    _history[vin]["soc"].append((now, n))
            elif key == "VehicleSpeed":
                n = _num(value)
                if n is not None:
                    _history[vin]["speed"].append((now, n))


def _tail_records():
    """Follow RECORDS_FILE, tolerating truncation/rotation and the file not existing yet."""
    pos = 0
    while True:
        try:
            if not os.path.exists(RECORDS_FILE):
                time.sleep(1.0)
                continue
            with open(RECORDS_FILE, "r", errors="replace") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                if size < pos:           # truncated/rotated -> restart from top
                    pos = 0
                fh.seek(pos)
                while True:
                    line = fh.readline()
                    if not line:
                        pos = fh.tell()
                        # detect rotation: file shrank
                        try:
                            if os.path.getsize(RECORDS_FILE) < pos:
                                break
                        except OSError:
                            break
                        time.sleep(0.5)
                        continue
                    line = line.strip()
                    if not line or line[0] != "{":
                        continue
                    try:
                        _ingest(json.loads(line))
                    except (ValueError, KeyError):
                        pass
        except OSError:
            time.sleep(1.0)


def _cert_expiry():
    try:
        out = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", CERT_FILE],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and "notAfter=" in out.stdout:
            s = out.stdout.strip().split("notAfter=", 1)[1]
            exp = time.mktime(time.strptime(s, "%b %d %H:%M:%S %Y %Z"))
            days = (exp - time.time()) / 86400.0
            return {"not_after": s, "days_left": round(days, 1)}
    except Exception:
        pass
    return {"not_after": None, "days_left": None}


def _rate_per_min():
    now = time.time()
    with _lock:
        recent = [t for t in _record_times if now - t <= 600]
    if not recent:
        return 0.0
    span = max(now - recent[0], 1.0)
    return round(len(recent) / span * 60.0, 1)


def build_state():
    now = time.time()
    with _lock:
        vins = list(_latest.keys())
        latest = {v: dict(f) for v, f in _latest.items()}
        history = {v: {"soc": list(h["soc"]), "speed": list(h["speed"])}
                   for v, h in _history.items()}
        total = _total_records
        client_versions = dict(_client_versions)
        last_epoch = _last_record_epoch
    vehicles = []
    for vin in vins:
        fields = latest[vin]
        loc_lat = loc_lon = None
        if "Location" in fields:
            loc_lat, loc_lon = _parse_location(fields["Location"]["value"])
        last_seen = max((f["received_at"] for f in fields.values()), default=0)
        # A vehicle can appear in _latest (e.g. a Location-only record) before it has any
        # Soc/VehicleSpeed history, so _history may not have this vin yet — default safely.
        hist = history.get(vin) or {"soc": [], "speed": []}
        vehicles.append({
            "vin": vin,
            "fields": fields,
            "location": {"lat": loc_lat, "lon": loc_lon},
            "soc_history": [round(v, 2) for _, v in hist["soc"]],
            "speed_history": [round(v, 2) for _, v in hist["speed"]],
            "client_version": client_versions.get(vin),
            "last_seen_epoch": last_seen,
            "online": (now - last_seen) < 600 if last_seen else False,
        })
    return {
        "now": now,
        "uptime_seconds": int(now - START_TIME),
        "total_records": total,
        "records_per_min": _rate_per_min(),
        "last_record_epoch": last_epoch,
        "namespace": NAMESPACE,
        "version": ADDON_VERSION,
        "cert": _cert_expiry(),
        "vehicles": vehicles,
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.endswith("/api/state") or path == "/api/state":
            self._send(200, json.dumps(build_state()))
        elif path == "" or path.endswith("/index.html") or path.endswith("/"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        else:
            self._send(200, PAGE, "text/html; charset=utf-8")

    do_HEAD = do_GET


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fleet Telemetry</title>
<style>
:root{--bg:#0b0f17;--card:#141b29;--card2:#1b2435;--line:#26314a;--txt:#e7edf7;--mut:#8a98b3;--accent:#3ea6ff;--good:#3ddc97;--warn:#ffb454;--bad:#ff5d5d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:18px}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px}
h1{font-size:18px;margin:0;font-weight:650}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.pill{display:inline-flex;align-items:center;gap:7px;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:5px 12px;font-size:12.5px;color:var(--mut)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;min-height:120px}
.card h3{margin:0 0 10px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);font-weight:600}
.big{font-size:34px;font-weight:680;line-height:1}
.unit{font-size:15px;color:var(--mut);font-weight:500;margin-left:4px}
.sub{color:var(--mut);font-size:12.5px;margin-top:8px}
.battery{position:relative;height:14px;background:var(--card2);border-radius:7px;overflow:hidden;margin:12px 0 4px}
.battery>span{position:absolute;left:0;top:0;bottom:0;border-radius:7px;transition:width .6s}
.gear{display:flex;gap:6px;margin-top:6px}
.gear b{width:34px;height:38px;display:flex;align-items:center;justify-content:center;border-radius:8px;background:var(--card2);color:var(--mut);font-weight:700;font-size:16px}
.gear b.on{background:var(--accent);color:#04121f}
.kv{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--line);font-size:13px}
.kv:last-child{border-bottom:0}.kv span:first-child{color:var(--mut)}
.kv i{font-style:normal;color:var(--mut);font-size:11px;margin-left:3px}
h2{font-size:15px;margin:18px 0 10px}
.spark{width:100%;height:46px;display:block;margin-top:8px}
a{color:var(--accent)}
iframe{width:100%;height:200px;border:0;border-radius:10px;margin-top:6px;background:var(--card2)}
.foot{color:var(--mut);font-size:12px;margin-top:18px;text-align:center}
.muted{color:var(--mut)}
</style></head>
<body><div class="wrap">
<header>
  <h1>⚡ Tesla Telemetry</h1>
  <span class="pill" id="verPill">v—</span>
  <span class="pill"><span class="dot" id="statusDot"></span><span id="statusTxt">connecting…</span></span>
  <span class="pill" id="ratePill">— rec/min</span>
  <span class="pill" id="totalPill">— records</span>
  <span style="flex:1"></span>
  <span class="pill" id="updatedPill">updated —</span>
</header>
<div id="content">
  <p id="empty" class="muted">Waiting for the first telemetry record… (the vehicle streams every few minutes when awake)</p>
  <div id="vehicles"></div>
</div>
<div class="foot" id="foot"></div>
</div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function ago(epoch){if(!epoch)return"never";const s=Math.max(0,Date.now()/1000-epoch);
 if(s<60)return Math.round(s)+"s ago";if(s<3600)return Math.round(s/60)+"m ago";
 if(s<86400)return Math.round(s/3600)+"h ago";return Math.round(s/86400)+"d ago";}
function dur(s){const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
 return (d?d+"d ":"")+(h?h+"h ":"")+m+"m";}
function fmt(n,dp=0){return n==null?"—":Number(n).toLocaleString(undefined,{maximumFractionDigits:dp});}
function spark(vals,color){if(!vals||vals.length<2)return"";const w=240,h=46,mn=Math.min(...vals),mx=Math.max(...vals),rg=(mx-mn)||1;
 const pts=vals.map((v,i)=>[i/(vals.length-1)*w,h-4-((v-mn)/rg)*(h-8)]);
 const d="M"+pts.map(p=>p[0].toFixed(1)+","+p[1].toFixed(1)).join(" L");
 return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="${color}" stroke-width="2"/></svg>`;}
function batColor(p){return p>50?"var(--good)":p>20?"var(--warn)":"var(--bad)";}
const GEARS=["P","R","N","D"];
function gearName(v){if(v==null)return null;const s=String(v).toUpperCase();const last=s.slice(-1);
 if("PRND".includes(last))return last; // handles "DriveGearP", "P", etc.
 if(s.includes("PARK"))return"P";if(s.includes("REV"))return"R";
 if(s.includes("NEUT"))return"N";if(s.includes("DRIVE"))return"D";return s;}
function card(t,inner){return `<div class="card"><h3>${t}</h3>${inner}</div>`;}

// Persistent per-VIN map state: the last lat,lon we pointed the iframe at, so we only
// reload the OpenStreetMap embed when the vehicle actually moves (not every refresh).
const mapKeys={};
// Telemetry enums arrive verbose ("DetailedChargeStateDisconnected", "WindowStateClosed",
// "SettingTemperatureUnitFahrenheit"). Strip the prefix to the meaningful suffix, and treat the
// "<invalid>" sentinel (field not applicable right now) as absent.
function pretty(field,val){
 if(val==null)return null;
 if(typeof val!=="string")return val;
 if(val==="<invalid>"||val==="invalid"||val==="")return null;
 let s=val;const i=s.lastIndexOf("State");
 if(i>=0&&i+5<s.length){s=s.slice(i+5);}
 else{for(const p of [field,field.replace(/^Setting/,"")]){if(p&&s.startsWith(p)&&s.length>p.length){s=s.slice(p.length);break;}}}
 return s;
}
function buildCards(v){
 const f=v.fields||{};
 const raw=k=>f[k]?f[k].value:undefined;
 const N=k=>{const x=raw(k);if(x==null||x==="<invalid>")return null;const n=Number(x);return Number.isNaN(n)?null:n;};
 const S=k=>pretty(k,raw(k));
 const B=k=>{const x=raw(k);return (x===true||x===false)?x:null;};
 const nf=(x,dp=0)=>x==null?null:Number(x).toLocaleString(undefined,{maximumFractionDigits:dp});
 const row=(label,val,unit)=>{if(val==null||val===""||val==="<invalid>")return"";
   return `<div class="kv"><span>${esc(label)}</span><span>${esc(String(val))}${unit?` <i>${esc(unit)}</i>`:""}</span></div>`;};
 const mcard=(t,inner)=>inner&&inner.trim()?card(t,inner):"";
 const fahr=String(raw('SettingTemperatureUnit')||"").includes("Fahrenheit");
 const tc=k=>{const n=N(k);return n==null?null:(fahr?Math.round(n*9/5+32):Math.round(n*10)/10);};
 const tu=fahr?"°F":"°C";
 const onoff=k=>{const b=B(k);return b==null?null:(b?"on":"off");};
 let cards="";

 // Battery (headline + range/energy)
 const soc=N('Soc')!=null?N('Soc'):N('BatteryLevel');
 const sh=v.soc_history||[];
 const charging=S('ChargeState')==="Charging"||(N('ACChargingPower')||0)>0||(N('DCChargingPower')||0)>0;
 cards+=card("Battery"+(charging?" ⚡":""),
   `<div class="big">${soc==null?"—":fmt(soc,0)}<span class="unit">%</span></div>`
   +`<div class="battery"><span style="width:${soc==null?0:Math.max(2,soc)}%;background:${batColor(soc||0)}"></span></div>`
   +spark(sh,batColor(soc||0))
   +row("Range",nf(N('RatedRange')!=null?N('RatedRange'):N('IdealBatteryRange')),"mi")
   +row("Energy left",nf(N('EnergyRemaining'),1),"kWh")
   +row("Charge limit",nf(N('ChargeLimitSoc')),"%"));

 // Charging — full detail only while actually charging; otherwise compact state.
 const chState=S('DetailedChargeState')||S('ChargeState');
 let chInner=row("State",chState);
 if(charging){
   chInner+=row("Power",nf((N('ACChargingPower')||0)+(N('DCChargingPower')||0),1),"kW")
     +row("Rate",nf(N('ChargeRateMilePerHour')),"mi/h")
     +row("Current",nf(N('ChargeAmps')),"A")
     +row("Voltage",nf(N('ChargerVoltage')),"V")
     +row("Added (AC)",nf(N('ACChargingEnergyIn'),1),"kWh")
     +row("Added (DC)",nf(N('DCChargingEnergyIn'),1),"kWh")
     +row("Time to full",nf(N('TimeToFullCharge'),1),"h")
     +row("Cable",S('ChargingCableType'))
     +row("Fast charger",S('FastChargerType'));
 }
 chInner+=row("Port door",B('ChargePortDoorOpen')==null?null:(B('ChargePortDoorOpen')?"open":"closed"))
   +row("Port latch",S('ChargePortLatch'));
 cards+=mcard("Charging",chInner);

 // Drive
 const speed=N('VehicleSpeed');const g=raw('Gear');const gear=(g==null||g==="<invalid>")?null:gearName(g);
 cards+=card("Drive",
   `<div class="big">${speed==null?'<span style="font-size:16px;color:var(--mut)">parked / idle</span>':fmt(speed,0)+'<span class="unit">mph</span>'}</div>`
   +((v.speed_history&&v.speed_history.length>1)?spark(v.speed_history,"var(--accent)"):"")
   +row("Gear",gear)
   +row("Heading",nf(N('GpsHeading')),"°")
   +row("Odometer",nf(N('Odometer')),"mi"));

 // Climate
 cards+=mcard("Climate",
   row("Inside",tc('InsideTemp'),tu)
   +row("Outside",tc('OutsideTemp'),tu)
   +row("A/C",onoff('HvacACEnabled'))
   +row("HVAC",S('HvacPower'))
   +row("Climate keeper",S('ClimateKeeperMode'))
   +row("Cabin overheat",S('CabinOverheatProtectionMode')));

 // Security & access
 const DOORLBL={DriverFront:"Driver front",DriverRear:"Driver rear",PassengerFront:"Passenger front",PassengerRear:"Passenger rear",TrunkFront:"Frunk",TrunkRear:"Trunk"};
 const dsraw=raw('DoorState');let doors=null;
 if(dsraw&&typeof dsraw==="object"){
   const open=Object.keys(dsraw).filter(k=>dsraw[k]).map(k=>DOORLBL[k]||k);
   doors=open.length?open.join(", "):"all closed";
 }
 const win=[["FdWindow","FL"],["FpWindow","FR"],["RdWindow","RL"],["RpWindow","RR"]];
 const winOpen=win.filter(([k])=>{const s=S(k);return s&&s!=="Closed";}).map(([k,l])=>`${l} ${S(k)}`).join(", ");
 const anyWin=win.some(([k])=>S(k)!=null);
 cards+=mcard("Security & access",
   row("Locked",B('Locked')==null?null:(B('Locked')?"locked":"unlocked"))
   +row("Sentry",S('SentryMode'))
   +row("Doors",doors)
   +row("Windows",anyWin?(winOpen||"all closed"):null));

 // Tire pressure (bar -> psi)
 const tp=k=>{const n=N(k);return n==null?null:Math.round(n*14.5038);};
 cards+=mcard("Tire pressure",
   row("Front L",tp('TpmsPressureFl'),"psi")+row("Front R",tp('TpmsPressureFr'),"psi")
   +row("Rear L",tp('TpmsPressureRl'),"psi")+row("Rear R",tp('TpmsPressureRr'),"psi"));

 // Vehicle
 cards+=card("Vehicle",
   row("Name",typeof raw('VehicleName')==="string"?raw('VehicleName'):null)
   +row("VIN",v.vin)
   +row("Software",typeof raw('Version')==="string"?raw('Version'):null)
   +row("Network",typeof raw('NetworkInterface')==="string"?raw('NetworkInterface'):null)
   +row("Status",v.online?"online":"offline")
   +row("Last record",ago(v.last_seen_epoch))
   +row("Client",v.client_version||null)
   +row("Signals",Object.keys(f).length));

 // Other — only genuinely ungrouped signals (future-proofing)
 const grouped=new Set(["Soc","BatteryLevel","RatedRange","IdealBatteryRange","EstBatteryRange","EnergyRemaining","ChargeLimitSoc","ChargeState","DetailedChargeState","ACChargingPower","DCChargingPower","ChargeRateMilePerHour","ChargeAmps","ChargerVoltage","ChargerPhases","ACChargingEnergyIn","DCChargingEnergyIn","TimeToFullCharge","ChargingCableType","FastChargerType","FastChargerPresent","ChargePortDoorOpen","ChargePortLatch","VehicleSpeed","Gear","GpsHeading","Odometer","InsideTemp","OutsideTemp","HvacACEnabled","HvacPower","ClimateKeeperMode","CabinOverheatProtectionMode","DoorState","Locked","SentryMode","FdWindow","FpWindow","RdWindow","RpWindow","TpmsPressureFl","TpmsPressureFr","TpmsPressureRl","TpmsPressureRr","VehicleName","Version","NetworkInterface","Location","LocatedAtHome","LocatedAtWork","LocatedAtFavorite","SettingTemperatureUnit","SettingDistanceUnit","ConnectionID","Status"]);
 const extra=Object.keys(f).filter(k=>!grouped.has(k));
 if(extra.length){cards+=card("Other signals",extra.map(k=>row(k,typeof raw(k)==="object"?JSON.stringify(raw(k)):(pretty(k,raw(k))!=null?pretty(k,raw(k)):raw(k)))).join(""));}

 return cards;
}

async function tick(){
 let st;try{st=await (await fetch(new URL('api/state',location.href),{cache:'no-store'})).json();}
 catch(e){$("#statusTxt").textContent="dashboard offline";return;}
 const fresh=st.last_record_epoch&&(st.now-st.last_record_epoch<600);
 $("#statusDot").style.background=fresh?"var(--good)":"var(--bad)";
 $("#statusTxt").textContent=fresh?"streaming":"no recent data";
 $("#ratePill").textContent=fmt(st.records_per_min,1)+" rec/min";
 $("#totalPill").textContent=fmt(st.total_records)+" records";
 $("#updatedPill").textContent="last record "+ago(st.last_record_epoch);
 $("#verPill").textContent="v"+(st.version||"—");
 const c=st.cert||{};
 $("#foot").innerHTML=`add-on v${esc(st.version||"—")} · uptime ${dur(st.uptime_seconds)} · namespace <b>${esc(st.namespace)}</b>`
   +(c.days_left!=null?` · TLS cert ${c.days_left>0?"valid "+fmt(c.days_left)+"d":"EXPIRED"} (${esc(c.not_after)})`:"");
 const vehiclesEl=$("#vehicles");
 $("#empty").style.display=st.vehicles.length?"none":"";
 const seen=new Set();
 for(const v of st.vehicles){
   const id="veh-"+v.vin;seen.add(id);
   let el=document.getElementById(id);
   if(!el){
     // Create the per-vehicle shell ONCE. The grid is re-rendered cheaply each tick
     // (text/SVG, no flash); the map iframe lives in its own card so it is never
     // recreated — we only change its src when the vehicle moves.
     el=document.createElement("div");el.id=id;
     el.innerHTML=`<h2 style="font-size:15px;margin:18px 0 10px">🚗 ${esc(v.vin)}</h2>`
       +`<div class="grid gridslot"></div>`
       +`<div class="card mapcard" style="display:none;margin-top:14px"><h3>Location</h3>`
       +`<div class="sub maploc"></div>`
       +`<iframe class="mapframe" loading="lazy"></iframe></div>`;
     vehiclesEl.appendChild(el);
   }
   el.querySelector(".gridslot").innerHTML=buildCards(v);
   const mapcard=el.querySelector(".mapcard"),frame=el.querySelector(".mapframe");
   if(v.location&&v.location.lat!=null){
     const la=v.location.lat,lo=v.location.lon,key=la.toFixed(5)+","+lo.toFixed(5);
     const ff=v.fields||{},gg=k=>ff[k]?ff[k].value:null;
     const geo=gg('LocatedAtHome')?"🏠 Home":gg('LocatedAtWork')?"🏢 Work":gg('LocatedAtFavorite')?"⭐ Favorite":"";
     const net=typeof gg('NetworkInterface')==="string"?gg('NetworkInterface'):"";
     el.querySelector(".maploc").innerHTML=`${la.toFixed(5)}, ${lo.toFixed(5)}`+(geo?` · ${esc(geo)}`:"")
       +(net?` · <span class="muted">${esc(net)}</span>`:"")
       +` · <a href="https://www.openstreetmap.org/?mlat=${la}&mlon=${lo}#map=15/${la}/${lo}" target="_blank">open map</a>`;
     if(mapKeys[v.vin]!==key){
       const d=0.01,bbox=[lo-d,la-d,lo+d,la+d].join("%2C");
       frame.src=`https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${la}%2C${lo}`;
       mapKeys[v.vin]=key;
     }
     mapcard.style.display="";
   }else{mapcard.style.display="none";}
 }
 // Drop any vehicle shells that are no longer present.
 Array.from(vehiclesEl.children).forEach(ch=>{if(ch.id&&!seen.has(ch.id))ch.remove();});
}
tick();setInterval(tick,5000);
</script>
</body></html>"""


def main():
    threading.Thread(target=_tail_records, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[fleet-telemetry-web] dashboard listening on :{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
