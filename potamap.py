#!/usr/bin/env python3
"""
POTA Hunter — Standalone Map Server
Reads map_state.json written by hamlog.pyw and serves the Leaflet map in the
browser.  Launched automatically when the user clicks "Open in Browser" in
the main app; can also be run independently.
"""

import http.server
import json
import os
import socketserver
import threading
import time
import webbrowser

LOGBOOK_DIR       = os.path.join(os.path.expanduser("~"), "HamLog")
MAP_STATE_FILE    = os.path.join(LOGBOOK_DIR, "map_state.json")
MAP_COMMANDS_FILE = os.path.join(LOGBOOK_DIR, "map_commands.json")
MAP_RESULTS_FILE  = os.path.join(LOGBOOK_DIR, "map_results.json")

_cmd_lock = threading.Lock()
_cmd_seq  = [0]

MAP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>POTA Hunter — Live Map</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{--red:#ff2020;--red-dim:#8b0000;--amber:#ff9900;--green:#00ff88;--cyan:#00e5ff;--bg:#030609;--panel:#070d12;--border:#1a3040;--text:#c8dde8;--dim:#3a5060;}
*{margin:0;padding:0;box-sizing:border-box;}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);font-family:'Share Tech Mono',monospace;}
body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:9000;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.07) 2px,rgba(0,0,0,.07) 4px);}
header{height:52px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;border-bottom:1px solid var(--red);background:linear-gradient(90deg,#0a0002,#0d0008,#0a0002);position:relative;z-index:1000;flex-shrink:0;}
header::after{content:'';position:absolute;inset:0;pointer-events:none;background:repeating-linear-gradient(90deg,transparent,transparent 60px,rgba(255,20,20,.025) 60px,rgba(255,20,20,.025) 61px);}
.logo{font-family:'Orbitron',sans-serif;font-weight:900;font-size:1.2rem;color:var(--red);letter-spacing:4px;text-shadow:0 0 20px rgba(255,32,32,.8),0 0 40px rgba(255,32,32,.3);}
.logo span{color:#fff;}
.hdr-mid{display:flex;gap:32px;align-items:center;font-size:.87rem;letter-spacing:2px;color:var(--dim);position:absolute;left:50%;transform:translateX(-50%);}
.status-dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);animation:sdpulse 1.5s ease-in-out infinite;display:inline-block;margin-right:5px;}
@keyframes sdpulse{0%,100%{opacity:1;box-shadow:0 0 8px var(--green)}50%{opacity:.4;box-shadow:0 0 2px var(--green)}}
#clock{font-family:'Orbitron',sans-serif;font-size:.96rem;color:var(--amber);letter-spacing:2px;}
.app-body{display:flex;height:calc(100vh - 52px);}
.panel{width:240px;flex-shrink:0;background:var(--panel);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;}
.panel.right{width:260px;border-right:none;border-left:1px solid var(--border);}
.panel-inner{flex:1;overflow-y:auto;padding:13px;display:flex;flex-direction:column;gap:9px;}
.panel-title{font-family:'Orbitron',sans-serif;font-size:.57rem;letter-spacing:3px;color:var(--red);text-transform:uppercase;padding-bottom:6px;border-bottom:1px solid var(--red-dim);flex-shrink:0;}
.card{background:rgba(255,255,255,.02);border:1px solid var(--border);padding:8px 11px;position:relative;flex-shrink:0;}
.card::before{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:var(--red);}
.card-label{font-size:.57rem;letter-spacing:2px;color:var(--dim);text-transform:uppercase;margin-bottom:3px;}
.card-value{font-family:'Orbitron',sans-serif;font-size:1rem;color:var(--amber);text-shadow:0 0 10px rgba(255,153,0,.5);}
.card-sub{font-size:.6rem;color:var(--dim);margin-top:2px;}
.chips{display:flex;flex-wrap:wrap;gap:3px;}
.chip{border:1px solid;padding:2px 7px;font-size:.56rem;letter-spacing:1px;}
.chip strong{color:var(--amber);}
.filter-chip{border:1px solid var(--red);padding:2px 7px;font-size:.56rem;letter-spacing:1px;cursor:pointer;transition:all .15s;color:var(--red);user-select:none;}
.filter-chip:hover{opacity:.8;}
.filter-chip.active{color:var(--green);border-color:var(--green);text-shadow:0 0 6px rgba(0,255,136,.4);}
#hide-qrt-btn{width:100%;padding:4px 0;font-family:'Orbitron',sans-serif;font-size:.52rem;letter-spacing:2px;background:transparent;border:1px solid var(--red);color:var(--red);cursor:pointer;transition:all .2s;margin-bottom:4px;}
#hide-qrt-btn:hover{background:rgba(255,32,32,.07);}
#hide-qrt-btn.active{border-color:var(--green);color:var(--green);text-shadow:0 0 6px rgba(0,255,136,.4);}
#respot-btn{width:100%;padding:6px 0;font-family:'Orbitron',sans-serif;font-size:.52rem;letter-spacing:2px;background:transparent;border:1px solid var(--amber);color:var(--amber);cursor:pointer;transition:all .2s;margin-top:4px;}
#respot-btn:hover{background:rgba(255,153,0,.08);box-shadow:0 0 10px rgba(255,153,0,.2);}
#respot-btn.active{border-color:var(--green);color:var(--green);text-shadow:0 0 6px rgba(0,255,136,.4);}
#respot-status{font-size:.52rem;color:var(--green);display:block;text-align:center;margin-top:2px;min-height:.8rem;}
.map-area{flex:1;position:relative;overflow:hidden;}
#map{width:100%;height:100%;background:#020810;}
.spot-item{background:rgba(255,255,255,.015);border:1px solid var(--border);padding:7px 10px;cursor:pointer;transition:all .15s;flex-shrink:0;border-left:3px solid var(--dim);}
.spot-item:hover{background:rgba(0,119,255,.07);box-shadow:0 0 10px rgba(0,119,255,.1);}
.spot-item.tuned{border-left-color:var(--cyan);background:rgba(0,229,255,.05);}
.spot-item.tuned:hover{background:rgba(0,229,255,.09);}
.spot-item.worked{border-left-color:#00bb44;}
.spot-item.worked:hover{background:rgba(0,187,68,.07);}
.spot-call{font-family:'Orbitron',sans-serif;font-size:.72rem;color:var(--cyan);text-shadow:0 0 6px rgba(0,229,255,.4);display:flex;align-items:center;justify-content:space-between;}
.spot-badge{font-size:.52rem;letter-spacing:1px;padding:1px 5px;border:1px solid currentColor;}
.spot-badge.tuned{color:var(--cyan);}
.spot-badge.worked{color:#00bb44;}
.spot-meta{font-size:.58rem;color:var(--dim);margin-top:3px;display:flex;gap:7px;flex-wrap:wrap;}
.spot-park{color:var(--amber);}
.no-spots{text-align:center;padding:28px 10px;color:var(--dim);font-size:.6rem;letter-spacing:2px;line-height:2;}
.beam-anim{animation:beam-flow 0.9s linear infinite;}
@keyframes beam-flow{to{stroke-dashoffset:-20;}}
@keyframes spot-flash{0%,100%{opacity:1}50%{opacity:0.1}}
.spot-flash{animation:spot-flash 1.5s ease-in-out infinite;}
::-webkit-scrollbar{width:3px;}
::-webkit-scrollbar-thumb{background:var(--red-dim);}
#scan-btn{cursor:pointer;font-family:'Orbitron',sans-serif;font-size:.6rem;letter-spacing:2px;padding:4px 12px;border:1px solid currentColor;transition:all .2s;user-select:none;pointer-events:all;}
#scan-btn.active{color:var(--green);border-color:var(--green);text-shadow:0 0 8px var(--green);}
#scan-btn.paused{color:var(--red);border-color:var(--red-dim);}
.map-overlays{position:absolute;top:10px;right:14px;z-index:500;display:flex;gap:8px;pointer-events:none;}
#snipe-btn{width:100%;padding:18px 0;font-family:'Orbitron',sans-serif;font-size:.58rem;letter-spacing:2px;background:transparent;border:1px solid var(--green);color:var(--green);cursor:pointer;text-shadow:0 0 6px rgba(0,255,136,.4);transition:all .2s;}
#snipe-btn:hover{background:rgba(0,255,136,.08);box-shadow:0 0 10px rgba(0,255,136,.2);}
.scan-rate-btn{flex:1;padding:4px 0;font-family:'Orbitron',sans-serif;font-size:.5rem;letter-spacing:2px;background:transparent;border:1px solid var(--dim);color:var(--dim);cursor:pointer;transition:all .15s;}
.scan-rate-btn.active{border-color:var(--amber);color:var(--amber);text-shadow:0 0 6px rgba(255,153,0,.3);}
.scan-rate-btn:hover{opacity:.8;}
#last-logged{display:none;border:1px solid #00e5ff;color:#00e5ff;padding:8px 6px;font-family:'Orbitron',sans-serif;font-size:.56rem;letter-spacing:1px;text-align:center;margin-top:4px;}
#last-logged.flashing{animation:last-log-flash 1.5s ease-in-out 3 forwards;}
@keyframes last-log-flash{0%,100%{opacity:0;box-shadow:none;}50%{opacity:1;box-shadow:0 0 12px rgba(0,229,255,.4);}}
#snipe-popup{position:absolute;top:14px;left:50%;transform:translateX(-50%);z-index:500;background:rgba(7,13,18,.97);border:1px solid var(--green);padding:16px 21px;min-width:373px;box-shadow:0 0 20px rgba(0,255,136,.15);}
.snipe-header{display:flex;justify-content:space-between;align-items:center;font-family:'Orbitron',sans-serif;font-size:.87rem;letter-spacing:2px;color:var(--green);margin-bottom:13px;border-bottom:1px solid var(--border);padding-bottom:8px;}
.snipe-row{display:flex;align-items:center;gap:11px;margin-bottom:7px;}
.snipe-row label{font-size:.73rem;letter-spacing:1px;color:var(--dim);width:90px;flex-shrink:0;}
.snipe-row input{flex:1;background:rgba(255,255,255,.04);border:1px solid var(--border);color:var(--text);font-family:'Share Tech Mono',monospace;font-size:.93rem;padding:4px 8px;}
#snipe-submit{font-family:'Orbitron',sans-serif;font-size:.77rem;letter-spacing:2px;background:transparent;border:1px solid var(--cyan);color:var(--cyan);padding:7px 21px;cursor:pointer;}
#snipe-submit:hover{background:rgba(0,229,255,.1);}
</style>
</head>
<body>
<header>
  <div class="logo">// <span>POTA Hunter</span></div>
  <div class="hdr-mid">
    <span><span class="status-dot"></span>POTA HUNTER</span>
    <span id="clock">--:--:-- ZULU</span>
    <span id="mycall" style="color:var(--cyan);font-family:'Orbitron',sans-serif;font-size:.96rem;letter-spacing:3px;"></span>
  </div>
</header>
<div class="app-body">
  <div class="panel">
    <div class="panel-inner">
      <div class="panel-title">◈ POTA STATUS</div>
      <div class="card">
        <div class="card-label">Active Spots</div>
        <div class="card-value" id="stat-spots">—</div>
        <div class="card-sub">Live POTA activations</div>
      </div>
      <div class="card">
        <div class="card-label">QSOs Logged</div>
        <div class="card-value" id="stat-qsos">—</div>
        <div class="card-sub">This session</div>
      </div>
      <div class="panel-title" style="margin-top:4px">◈ BANDS</div>
      <div class="chips" id="stat-bands">
        <div style="color:var(--dim);font-size:.6rem;letter-spacing:2px">NO SPOTS</div>
      </div>
      <div class="panel-title" style="margin-top:6px">◈ MODE</div>
      <div class="chips" id="stat-modes">
        <div style="color:var(--dim);font-size:.6rem;letter-spacing:2px">NO SPOTS</div>
      </div>
      <div class="panel-title" style="margin-top:6px">◈ ITU REGIONS</div>
      <div class="chips" id="stat-itu">
        <div class="filter-chip" data-itu="1">R1</div>
        <div class="filter-chip" data-itu="2">R2</div>
        <div class="filter-chip" data-itu="3">R3</div>
      </div>
      <button id="hide-qrt-btn">⊘ HIDE QRT</button>
      <div id="scan-rate-btns" style="display:flex;gap:6px;margin-top:2px;">
        <button class="scan-rate-btn active" data-rate="15">⏱ 15S</button>
        <button class="scan-rate-btn" data-rate="30">⏱ 30S</button>
      </div>
      <div class="panel-title" style="margin-top:8px">◈ LOG</div>
      <button id="respot-btn">⟳ AUTO REPORT</button>
      <span id="respot-status"></span>
      <button id="snipe-btn">⊕ SNIPE QSO</button>
      <div id="last-logged"></div>
    </div>
  </div>
  <div class="map-area">
    <div class="map-overlays">
      <div id="scan-btn" class="paused">⏸ SCAN PAUSED</div>
    </div>
    <div id="snipe-popup" style="display:none">
      <div class="snipe-header">
        <span>⊕ SNIPE QSO</span>
        <span id="snipe-close" style="cursor:pointer;color:var(--red)">✕</span>
      </div>
      <div class="snipe-row"><label>CALLSIGN</label><input id="sq-call" maxlength="11"></div>
      <div class="snipe-row"><label>RST SENT</label><input id="sq-rst-s" maxlength="5" value="59"></div>
      <div class="snipe-row"><label>RST RCVD</label><input id="sq-rst-r" maxlength="5" value="59"></div>
      <div class="snipe-row"><label>PARK #</label><input id="sq-park" maxlength="11"></div>
      <div class="snipe-row"><label>GRID</label><input id="sq-grid" maxlength="7"></div>
      <div class="snipe-row"><label>COMMENTS</label><input id="sq-comment" maxlength="40"></div>
      <div style="text-align:center;margin-top:8px">
        <button id="snipe-submit">LOG QSO</button>
        <span id="snipe-status" style="font-size:.55rem;color:var(--green);margin-left:8px"></span>
      </div>
    </div>
    <div id="map"></div>
  </div>
  <div class="panel right">
    <div class="panel-inner">
      <div class="panel-title">◈ ACTIVE SPOTS</div>
      <div id="spots-list">
        <div class="no-spots">AWAITING SPOTS...<br><br>Enable POTA scan to<br>populate this panel</div>
      </div>
    </div>
  </div>
</div>
<script>
var map=L.map('map',{center:[20,0],zoom:2});
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
  attribution:'&copy; OpenStreetMap contributors &copy; CARTO',
  subdomains:'abcd',maxZoom:19}).addTo(map);
var markers=[],beamLine=null;
var BAND_COLORS={'160m':'#ff4444','80m':'#ff8800','60m':'#ffcc00','40m':'#aaff00',
  '30m':'#00ffaa','20m':'#00e5ff','17m':'#0088ff','15m':'#8844ff',
  '12m':'#ff44cc','10m':'#ff2288','6m':'#ff0055','2m':'#ff6688','other':'#aaaaaa'};
function freqToBand(k){
  if(!k)return 'other';
  if(k<2000)return '160m';if(k<4000)return '80m';if(k<5500)return '60m';
  if(k<8000)return '40m';if(k<11000)return '30m';if(k<15500)return '20m';
  if(k<18500)return '17m';if(k<22000)return '15m';if(k<25000)return '12m';
  if(k<30000)return '10m';if(k<54000)return '6m';if(k<148000)return '2m';
  return 'other';}
function clearMarkers(){
  markers.forEach(function(m){map.removeLayer(m);});markers=[];
  if(beamLine){map.removeLayer(beamLine);beamLine=null;}}
var lastSpotData=null;
var filterBands=new Set();
var filterModes=new Set();
var filterItu=new Set();
var hideQrt=false;
var autoReport=false;
function syncFiltersToServer(){
  fetch('/set_map_filters',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({bands:Array.from(filterBands),modes:Array.from(filterModes),itu:Array.from(filterItu)})});
}

function latLonToItuRegion(lat,lon){
  return lon<=-30?2:lon<=60?1:3;}

function applyFilters(spots){
  return (spots||[]).filter(function(s){
    if(hideQrt&&s.comment&&s.comment.indexOf('qrt')!==-1)return false;
    if(filterBands.size>0&&!filterBands.has(freqToBand(s.freq_khz)))return false;
    if(filterModes.size>0&&!filterModes.has((s.mode||'').toUpperCase()))return false;
    if(filterItu.size>0&&!filterItu.has(latLonToItuRegion(s.lat,s.lon)))return false;
    return true;});}

function buildFilterChips(containerId,items,activeSet,colorFn,labelFn){
  var el=document.getElementById(containerId);
  if(!items.length){el.innerHTML='<div style="color:var(--dim);font-size:.6rem;letter-spacing:2px">NO SPOTS</div>';return;}
  el.innerHTML=items.map(function(v){
    var col=colorFn?colorFn(v):'var(--red)';
    var lbl=labelFn?labelFn(v):v;
    var act=activeSet.has(v)?'active':'';
    var sty=act?'':' style="border-color:'+col+';color:'+col+'"';
    return '<div class="filter-chip '+act+'"'+sty+' data-val="'+v+'">'+lbl+'</div>';
  }).join('');
  el.querySelectorAll('.filter-chip').forEach(function(chip){
    chip.addEventListener('click',function(){
      var v=chip.dataset.val;
      if(typeof v==='string'&&!isNaN(Number(v)))v=Number(v);
      if(activeSet.has(v))activeSet.delete(v);else activeSet.add(v);
      if(lastSpotData)renderSpotsAndMarkers(lastSpotData);
      syncFiltersToServer();});}); }

function updateFilterPanels(d){
  var spots=d.spots||[];
  var allBands=[],seenB={};
  spots.forEach(function(s){var b=freqToBand(s.freq_khz);if(!seenB[b]){seenB[b]=1;allBands.push(b);}});
  allBands.sort();
  buildFilterChips('stat-bands',allBands,filterBands,null,null);

  var allModes=[],seenM={};
  spots.forEach(function(s){var m=(s.mode||'').toUpperCase();if(m&&!seenM[m]){seenM[m]=1;allModes.push(m);}});
  allModes.sort();
  buildFilterChips('stat-modes',allModes,filterModes,function(){return 'var(--red)';},null);

  var ituEl=document.getElementById('stat-itu');
  ituEl.querySelectorAll('.filter-chip').forEach(function(chip){
    var v=Number(chip.dataset.itu);
    chip.className='filter-chip'+(filterItu.has(v)?' active':'');
  });}

function renderSpotsAndMarkers(d){
  var visible=applyFilters(d.spots);
  clearMarkers();
  (d.qsos||[]).forEach(function(q){
    var m=L.circleMarker([q.lat,q.lon],{radius:6,color:'#cc44ff',fillColor:'#cc44ff',fillOpacity:0.7,weight:1});
    var pop='<b>'+q.call+'</b>';
    if(q.park)pop+=' ['+q.park+']';
    if(q.band||q.mode)pop+='<br>'+[q.band,q.mode].filter(Boolean).join(' ');
    if(q.date)pop+='<br>'+q.date+' '+q.time_on+'z';
    m.bindPopup(pop);m.addTo(map);markers.push(m);});
  visible.forEach(function(s){
    var color=s.tuned?'#00e5ff':s.worked?'#00bb44':'#ffff00';
    var r=s.tuned?9:7;
    var cls=(!s.tuned&&!s.worked)?'spot-flash':'';
    var m=L.circleMarker([s.lat,s.lon],{radius:r,color:color,fillColor:color,fillOpacity:0.85,weight:s.tuned?2:1,className:cls});
    var pop=s.activator+' ['+s.park+']<br>'+s.freq_khz+' kHz '+s.mode;
    if(s.tuned)pop+='<br><b>&#x25CF; TUNED</b>';
    if(s.worked)pop+='<br><b>Worked</b>';
    m.bindPopup(pop);
    m.on('click',function(e){
      L.DomEvent.stopPropagation(e);
      fetch('/tune',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({activator:s.activator,park:s.park,freq_khz:s.freq_khz,mode:s.mode,tuned:s.tuned})});});
    m.addTo(map);markers.push(m);});
  if(d.my_grid){
    var mg=d.my_grid;
    var star=L.marker([mg.lat,mg.lon],{icon:L.divIcon({
      html:'<span style="color:#ff2222;font-size:18px;">&#9733;</span>',
      className:'',iconAnchor:[9,9]})});
    star.bindPopup('My grid: '+mg.gs);star.addTo(map);markers.push(star);}
  if(d.my_grid&&d.tuned_spot){
    var gcp=gcPoints(d.my_grid.lat,d.my_grid.lon,d.tuned_spot.lat,d.tuned_spot.lon,60);
    beamLine=L.polyline(gcp,{color:'#00e5ff',weight:2.5,dashArray:'12 8',opacity:0.85,className:'beam-anim'});
    beamLine.addTo(map);}
  var total=(d.spots||[]).length,shown=visible.length;
  document.getElementById('stat-spots').textContent=shown===total?total:(shown+'/'+total)||'—';
  document.getElementById('stat-qsos').textContent=(d.qsos||[]).length||'—';
  updateSpotsPanel(visible,d.spots);}

function updateSpotsPanel(visible,allSpots){
  var spots=visible.slice();
  spots.sort(function(a,b){return(a.spot_time||'').localeCompare(b.spot_time||'');});
  var el=document.getElementById('spots-list');
  if(!spots.length){el.innerHTML='<div class="no-spots">'+(allSpots&&allSpots.length?'NO SPOTS MATCH FILTERS':'NO ACTIVE SPOTS<br><br>Enable POTA scan to<br>populate this panel')+'</div>';return;}
  el.innerHTML=spots.map(function(s,i){
    var cls=s.tuned?'tuned':s.worked?'worked':'';
    var badge=s.tuned?'<span class="spot-badge tuned">&#9679; TUNED</span>'
      :s.worked?'<span class="spot-badge worked">&#10003; WORKED</span>':'';
    var mhz=s.freq_khz?(s.freq_khz/1000).toFixed(3)+' MHz':'?';
    var band=freqToBand(s.freq_khz),bc=BAND_COLORS[band]||'#aaa';
    return '<div class="spot-item '+cls+'" data-i="'+i+'">'
      +'<div class="spot-call"><span>'+s.activator+'</span>'+badge+'</div>'
      +'<div class="spot-meta"><span class="spot-park">'+(s.park||'?')+'</span>'
      +'<span style="color:'+bc+'">'+band+'</span>'
      +'<span>'+mhz+'</span><span>'+(s.mode||'')+'</span></div></div>';
  }).join('');
  el.querySelectorAll('.spot-item').forEach(function(item){
    var i=parseInt(item.dataset.i);
    item.addEventListener('click',function(){
      var s=spots[i];
      fetch('/tune',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({activator:s.activator,park:s.park,freq_khz:s.freq_khz,mode:s.mode,tuned:s.tuned})});
    });});}

function refreshData(){
  fetch('/data').then(function(r){return r.json();}).then(function(d){
    lastSpotData=d;
    updateFilterPanels(d);
    renderSpotsAndMarkers(d);
    var sb=document.getElementById('scan-btn');
    if(d.scanning){sb.className='active';sb.textContent='▶ SCANNING';}
    else{sb.className='paused';sb.textContent='⏸ SCAN PAUSED';}
    if(d.callsign){document.getElementById('mycall').textContent=d.callsign;}
    if(typeof d.hide_qrt==='boolean'&&d.hide_qrt!==hideQrt){
      hideQrt=d.hide_qrt;
      document.getElementById('hide-qrt-btn').className=hideQrt?'active':'';}
    if(typeof d.auto_respot==='boolean'){
      autoReport=d.auto_respot;
      document.getElementById('respot-btn').className=autoReport?'active':'';}
    if(d.scan_interval){
      document.querySelectorAll('.scan-rate-btn').forEach(function(b){
        b.className='scan-rate-btn'+(Number(b.dataset.rate)===d.scan_interval?' active':'');});}
  }).catch(function(e){console.error('Fetch error:',e);});}
function gcPoints(la1,lo1,la2,lo2,n){
  var R=Math.PI/180;
  var f1=la1*R,l1=lo1*R,f2=la2*R,l2=lo2*R;
  var d=2*Math.asin(Math.sqrt(Math.pow(Math.sin((f2-f1)/2),2)+Math.cos(f1)*Math.cos(f2)*Math.pow(Math.sin((l2-l1)/2),2)));
  if(d<1e-6)return[[la1,lo1],[la2,lo2]];
  var pts=[];
  for(var i=0;i<=n;i++){
    var f=i/n,A=Math.sin((1-f)*d)/Math.sin(d),B=Math.sin(f*d)/Math.sin(d);
    var x=A*Math.cos(f1)*Math.cos(l1)+B*Math.cos(f2)*Math.cos(l2);
    var y=A*Math.cos(f1)*Math.sin(l1)+B*Math.cos(f2)*Math.sin(l2);
    var z=A*Math.sin(f1)+B*Math.sin(f2);
    pts.push([Math.atan2(z,Math.sqrt(x*x+y*y))/R,Math.atan2(y,x)/R]);}
  return pts;}
function updateClock(){
  var n=new Date();
  document.getElementById('clock').textContent=
    ('0'+n.getUTCHours()).slice(-2)+':'+('0'+n.getUTCMinutes()).slice(-2)+':'+('0'+n.getUTCSeconds()).slice(-2)+' ZULU';}
setInterval(updateClock,1000);updateClock();
refreshData();
setInterval(refreshData,2000);
map.on('click',function(){fetch('/scan',{method:'POST'});});
document.getElementById('scan-btn').addEventListener('click',function(e){e.stopPropagation();fetch('/scan',{method:'POST'});});
document.getElementById('stat-itu').addEventListener('click',function(e){
  var chip=e.target.closest('.filter-chip[data-itu]');
  if(!chip)return;
  var v=Number(chip.dataset.itu);
  if(filterItu.has(v))filterItu.delete(v);else filterItu.add(v);
  if(lastSpotData)renderSpotsAndMarkers(lastSpotData);
  chip.className='filter-chip'+(filterItu.has(v)?' active':'');
  syncFiltersToServer();});
document.getElementById('hide-qrt-btn').addEventListener('click',function(){
  hideQrt=!hideQrt;
  this.className=hideQrt?'active':'';
  if(lastSpotData)renderSpotsAndMarkers(lastSpotData);
  fetch('/set_hide_qrt',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({enabled:hideQrt})});});
document.getElementById('respot-btn').addEventListener('click',function(){
  autoReport=!autoReport;
  this.className=autoReport?'active':'';
  fetch('/set_autorespot',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({enabled:autoReport})});});
document.querySelectorAll('.scan-rate-btn').forEach(function(btn){
  btn.addEventListener('click',function(){
    var rate=Number(btn.dataset.rate);
    document.querySelectorAll('.scan-rate-btn').forEach(function(b){b.className='scan-rate-btn';});
    btn.className='scan-rate-btn active';
    fetch('/set_scan_interval',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({interval:rate})});});});
document.getElementById('snipe-btn').addEventListener('click',function(){
  var popup=document.getElementById('snipe-popup');
  if(popup.style.display!=='none'){popup.style.display='none';return;}
  var tuned=lastSpotData&&lastSpotData.spots?lastSpotData.spots.find(function(s){return s.tuned;}):null;
  document.getElementById('sq-call').value=tuned?tuned.activator:'';
  document.getElementById('sq-park').value=tuned?tuned.park:'';
  document.getElementById('sq-grid').value=tuned?(tuned.gs||''):'';
  document.getElementById('sq-rst-s').value='59';
  document.getElementById('sq-rst-r').value='59';
  document.getElementById('sq-comment').value='';
  document.getElementById('snipe-status').textContent='';
  popup.style.display='block';
});
document.getElementById('snipe-close').addEventListener('click',function(e){
  e.stopPropagation();
  document.getElementById('snipe-popup').style.display='none';
});
document.getElementById('snipe-submit').addEventListener('click',function(e){
  e.stopPropagation();
  var call=document.getElementById('sq-call').value.trim().toUpperCase();
  if(!call){document.getElementById('snipe-status').textContent='CALLSIGN REQUIRED';return;}
  var payload={
    call:call,
    rst_sent:document.getElementById('sq-rst-s').value.trim()||'59',
    rst_rcvd:document.getElementById('sq-rst-r').value.trim()||'59',
    park_nr:document.getElementById('sq-park').value.trim(),
    gridsquare:document.getElementById('sq-grid').value.trim().toUpperCase(),
    comment:document.getElementById('sq-comment').value.trim()
  };
  fetch('/log',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(function(r){return r.json();})
    .then(function(r){
      if(r.ok){
        document.getElementById('snipe-status').textContent='LOGGED ✔';
        var tuned=lastSpotData&&lastSpotData.spots?lastSpotData.spots.find(function(s){return s.tuned;}):null;
        var ll=document.getElementById('last-logged');
        ll.textContent=call+(payload.park_nr?' ['+payload.park_nr+']':'')+(tuned?' '+tuned.freq_khz+'kHz '+tuned.mode:'');
        ll.style.display='block';
        void ll.offsetWidth;
        ll.className='flashing';
        ll.addEventListener('animationend',function(){ll.style.display='none';ll.className='';},{once:true});
        setTimeout(function(){document.getElementById('snipe-popup').style.display='none';},1200);
      }else{document.getElementById('snipe-status').textContent=r.error||'ERROR';}
    })
    .catch(function(){document.getElementById('snipe-status').textContent='ERROR';});
});
</script>
</body>
</html>"""


def _write_command(cmd_type, data):
    """Append a command to map_commands.json. Returns the command id."""
    with _cmd_lock:
        _cmd_seq[0] += 1
        cmd_id = f"cmd-{time.time():.3f}-{_cmd_seq[0]:04d}"
    cmd = {"id": cmd_id, "type": cmd_type, "data": data,
           "written_at_float": time.time()}
    for _ in range(3):
        try:
            existing = []
            if os.path.exists(MAP_COMMANDS_FILE):
                try:
                    with open(MAP_COMMANDS_FILE, "r", encoding="utf-8") as f:
                        existing = json.load(f).get("commands", [])
                except Exception:
                    existing = []
            now = time.time()
            existing = [c for c in existing if now - c.get("written_at_float", 0) < 10]
            existing.append(cmd)
            tmp = MAP_COMMANDS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"written_at": now, "commands": existing}, f)
            os.replace(tmp, MAP_COMMANDS_FILE)
            return cmd_id
        except Exception:
            time.sleep(0.05)
    return cmd_id


def _read_result(cmd_id, timeout=5.0):
    """Poll map_results.json for a result matching cmd_id."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if os.path.exists(MAP_RESULTS_FILE):
                with open(MAP_RESULTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for r in data.get("results", []):
                    if r.get("id") == cmd_id:
                        return r
        except Exception:
            pass
        time.sleep(0.15)
    return {"id": cmd_id, "ok": False, "error": "timeout"}


class _Handler(http.server.BaseHTTPRequestHandler):

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/data":
            self._handle_data()
        else:
            body = MAP_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _handle_data(self):
        try:
            if not os.path.exists(MAP_STATE_FILE):
                self._send_json({"spots": [], "qsos": [], "scanning": False,
                                 "callsign": "", "hide_qrt": False,
                                 "auto_respot": False, "scan_interval": 15})
                return
            try:
                with open(MAP_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                self._send_json({"error": "bad state", "spots": [], "qsos": []})
                return
            if time.time() - state.get("written_at", 0) > 60:
                state["stale"] = True
            self._send_json(state)
        except Exception as exc:
            self._send_json({"error": str(exc), "spots": [], "qsos": []})

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length))
        except Exception:
            return None

    def do_POST(self):
        if self.path == "/tune":
            data = self._read_body()
            if data is None:
                self._send_json({"error": "bad json"}, status=400)
                return
            _write_command("tune", data)
            self._send_json({"ok": True})

        elif self.path == "/scan":
            _write_command("scan", {})
            self._send_json({"ok": True})

        elif self.path == "/log":
            data = self._read_body()
            if data is None:
                self._send_json({"error": "bad json"}, status=400)
                return
            cmd_id = _write_command("log", data)
            result = _read_result(cmd_id, timeout=5.0)
            if result.get("ok"):
                self._send_json({"ok": True})
            else:
                self._send_json({"error": result.get("error", "timeout")})

        elif self.path == "/respot":
            data = self._read_body()
            if data is None:
                self._send_json({"error": "bad json"}, status=400)
                return
            _write_command("respot", data)
            self._send_json({"ok": True})

        elif self.path == "/set_autorespot":
            data = self._read_body()
            if data is None:
                self._send_json({"error": "bad json"}, status=400)
                return
            _write_command("set_autorespot", data)
            self._send_json({"ok": True})

        elif self.path == "/set_scan_interval":
            data = self._read_body()
            if data is None:
                self._send_json({"error": "bad json"}, status=400)
                return
            _write_command("set_scan_interval", data)
            self._send_json({"ok": True})

        elif self.path == "/set_hide_qrt":
            data = self._read_body()
            if data is None:
                self._send_json({"error": "bad json"}, status=400)
                return
            _write_command("set_hide_qrt", data)
            self._send_json({"ok": True})

        elif self.path == "/set_map_filters":
            data = self._read_body()
            if data is None:
                self._send_json({"error": "bad json"}, status=400)
                return
            _write_command("set_filters", data)
            self._send_json({"ok": True})

        else:
            self._send_json({"error": "not found"}, status=404)

    def log_message(self, fmt, *args):
        pass  # suppress access log noise


class _ReuseServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


def main():
    os.makedirs(LOGBOOK_DIR, exist_ok=True)
    server = None
    bound_port = None
    for port in (8765, 8766, 8767):
        try:
            server = _ReuseServer(("localhost", port), _Handler)
            bound_port = port
            break
        except OSError:
            continue
    if server is None:
        print("Could not bind to ports 8765-8767. Is potamap.py already running?")
        return
    print(f"POTA Map server running at http://localhost:{bound_port}")
    webbrowser.open(f"http://localhost:{bound_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
