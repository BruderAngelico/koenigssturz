# -*- coding: utf-8 -*-
"""Eigenständige Vereinsarchiv-Leseapp: Pakete einspielen, lokal browsen, kein cURL."""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from typing import Any, Dict

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

import kingfall_va_pack as vapack
import kingfall_va_store as va

HOST = "127.0.0.1"
PORT_CANDIDATES = (18766, 18765, 18080, 19000, 8088, 8000, 8765)
VA_AUDIO_TYPES = {
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}


def _port_from_args():
    args = sys.argv
    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 < len(args):
            return int(args[idx + 1])
    env = os.environ.get("KINGFALL_PORT")
    if env:
        return int(env)
    return None


def _can_bind(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _os_free_port(host):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def pick_port(host=HOST):
    wanted = _port_from_args()
    tried = []
    if wanted:
        tried.append(wanted)
    for port in PORT_CANDIDATES:
        if port not in tried:
            tried.append(port)
    for port in tried:
        if _can_bind(host, port):
            return port
    return _os_free_port(host)

DATA_ROOT = vapack.default_data_dir()
os.makedirs(DATA_ROOT, mode=0o700, exist_ok=True)


class ReaderState:
    def __init__(self):
        self.lock = threading.Lock()
        self.pending_zip = None
        self.last_message = ""
        self.imp_running = False
        self.imp_stop = False
        self.imp_progress = {}
        self.imp_error = ""
        self.imp_preview = None
        self.imp_result = None
        self.imp_phase = "idle"
        self.preview_ready = False
        self.apply_ready = False

    def snapshot(self):
        with self.lock:
            return {
                "running": self.imp_running,
                "phase": self.imp_phase,
                "progress": dict(self.imp_progress),
                "error": self.imp_error,
                "preview": self.imp_preview,
                "result": self.imp_result,
                "preview_ready": self.preview_ready,
                "apply_ready": self.apply_ready,
                "audio_prep": va.AUDIO_PREP.snapshot(),
            }

    def request_stop(self):
        with self.lock:
            self.imp_stop = True
            self.imp_progress = dict(self.imp_progress)
            self.imp_progress["status"] = "Stoppe …"
        va.AUDIO_PREP.request_stop()

    def _progress(self, info):
        with self.lock:
            self.imp_progress = dict(info)

    def _stop(self):
        with self.lock:
            return self.imp_stop

    def start_preview(self, path):
        with self.lock:
            if self.imp_running:
                raise RuntimeError("Import läuft bereits.")
            self.imp_running = True
            self.imp_stop = False
            self.imp_error = ""
            self.imp_preview = None
            self.imp_result = None
            self.preview_ready = False
            self.apply_ready = False
            self.imp_phase = "preview"
            self.imp_progress = {"status": "Starte …", "pct": 0}
            self.pending_zip = path
        threading.Thread(target=self._preview_worker, args=(path,), daemon=True).start()

    def _preview_worker(self, path):
        try:
            preview = vapack.preview_import(path, _root(), progress=self._progress, stop=self._stop)
            with self.lock:
                self.imp_preview = preview
                self.preview_ready = True
                self.imp_progress = {"status": "Geprüft", "pct": 100, "eta_sec": 0}
        except va.JobCancelled:
            with self.lock:
                self.imp_error = ""
                self.imp_progress = {"status": "Abgebrochen", "pct": 0}
                self.imp_preview = None
        except Exception as exc:
            with self.lock:
                self.imp_error = str(exc)
                self.imp_progress = {"status": "Fehler", "pct": 0}
        finally:
            with self.lock:
                self.imp_running = False
                self.imp_phase = "idle"

    def start_apply(self, mode):
        with self.lock:
            path = self.pending_zip
            if self.imp_running:
                raise RuntimeError("Import läuft bereits.")
            if not path:
                raise RuntimeError("Kein Paket bereit.")
            self.imp_running = True
            self.imp_stop = False
            self.imp_error = ""
            self.imp_result = None
            self.apply_ready = False
            self.imp_phase = "apply"
            self.imp_progress = {"status": "Übernehme …", "pct": 0}
        threading.Thread(target=self._apply_worker, args=(path, mode), daemon=True).start()

    def _apply_worker(self, path, mode):
        try:
            result = vapack.apply_import(
                path, dest_root=_root(), on_conflict=mode, progress=self._progress, stop=self._stop
            )
            with self.lock:
                self.imp_result = result
                self.apply_ready = True
                if result.get("ok") or result.get("aborted"):
                    self.pending_zip = None
            if result.get("ok") or result.get("aborted"):
                try:
                    os.remove(path)
                except OSError:
                    pass
            if result.get("ok"):
                with self.lock:
                    self.imp_phase = "convert"
                    self.imp_progress = {"status": "Prüfe Audio …", "pct": 95}
                conv = va.convert_pending(
                    root=_root(), progress_cb=self._progress, stop_fn=self._stop
                )
                with self.lock:
                    st = self.imp_progress.get("status") or conv.get("status") or "Fertig"
                    if conv.get("total", 0) == 0:
                        st = "Fertig – Audio aus Paket"
                    self.imp_progress = {
                        "status": st,
                        "pct": 100,
                        "eta_sec": 0,
                    }
        except va.JobCancelled:
            with self.lock:
                self.imp_error = ""
                self.imp_progress = {"status": "Abgebrochen", "pct": 0}
        except Exception as exc:
            with self.lock:
                self.imp_error = str(exc)
                self.imp_progress = {"status": "Fehler", "pct": 0}
        finally:
            with self.lock:
                self.imp_running = False
                self.imp_phase = "idle"


STATE = ReaderState()

PAGE = r"""<!DOCTYPE html>
<html lang="de"><head>
<meta charset="utf-8"/>
<title>VA Reader</title>
<style>
:root{--bg:#e8e4dc;--btn:#d4cfc4;--btn2:#c4bfb4;--field:#fff;--fg:#111;--bar:#3d6b99;--hover:#c5def5;}
*{box-sizing:border-box;}
html,body{height:100%;}
body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.35 system-ui,Helvetica,Arial,sans-serif;display:flex;flex-direction:column;}
.top{display:flex;align-items:center;justify-content:space-between;padding:10px 12px 0;}
h1{margin:0;font-size:18px;}
.pane{display:flex;flex-direction:column;flex:1;overflow:auto;padding:10px;min-height:0;}
fieldset{border:1px solid #c4bfb4;margin:0 0 8px;padding:10px;background:var(--bg);}
legend{font-weight:700;padding:0 6px;}
.hint{margin:0 0 8px;color:#333;}
.inline{display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px;margin:6px 0;}
.inline label{font-weight:600;}
input[type=text]{font:inherit;border:1px solid #c4bfb4;background:var(--field);color:var(--fg);padding:4px 6px;}
button.act{font:inherit;font-weight:700;background:var(--btn);border:1px solid #b8b3a8;padding:6px 12px;cursor:pointer;}
button.act:disabled{opacity:.55;cursor:not-allowed;}
.status{font-style:italic;margin-left:auto;}
.err{color:#8a1c1c;font-weight:700;min-height:1.2em;}
.warn{color:#6b4a00;font-weight:700;min-height:1.2em;}
.list{flex:1;overflow:auto;border:1px solid #c4bfb4;background:#f7f4ee;min-height:120px;}
.head,.rowl{display:grid;gap:8px;align-items:center;padding:6px 8px;font-size:12px;}
.head{font-weight:700;border-bottom:1px solid #c4bfb4;background:#efeae2;position:sticky;top:0;}
.rowl{border-bottom:1px solid #ddd;}
.rowl:hover{background:var(--hover);}
#valist{--va-cols:28px 80px 180px 64px 44px 44px 48px;flex:1;min-height:0;overflow:auto;}
#valist .head,#valist .rowl,#valist div.rowl{grid-template-columns:var(--va-cols)!important;gap:6px;width:max-content;min-width:100%;box-sizing:border-box;}
#valist .head{display:grid;position:sticky;top:0;z-index:3;}
#valist .rowl span.vadatum,#valist .rowl span.vaquelle,#valist .rowl span.vaaudio,#valist .rowl span.vadocs,#valist .rowl span.vadauer,#valist .vatitle .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;}
#valist .vasel{overflow:visible;display:flex;align-items:center;justify-content:center;}
#valist .vatitle{display:flex;align-items:center;gap:6px;min-width:0;overflow:hidden;}
#valist .vatitle .t{flex:1;min-width:0;}
.vaconv{flex:0 0 auto;font-weight:700;color:#3d6b99;white-space:nowrap;font-size:11px;}
#valist .rowl.converting{background:#d5e6f6;}
#valist .head .vacol{position:relative;padding-right:8px;overflow:visible;min-width:0;display:flex;align-items:center;}
#valist .head .vacol .vacol-lab{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;flex:1;}
#valist .head .colres{position:absolute;right:-4px;top:0;bottom:0;width:9px;cursor:col-resize;z-index:5;background:transparent;}
#valist .head .colres::before{content:"";position:absolute;left:50%;top:3px;bottom:3px;width:1px;background:#8a847a;transform:translateX(-50%);}
#valist .head .colres:hover::before,#valist .head .colres.drag::before{width:2px;background:#3d6b99;}
#valist .head .colres:hover,#valist .head .colres.drag{background:rgba(61,107,153,.14);}
#valist .rowl > span:not(:last-child),#valist .head .vacol:not(:last-child){box-shadow:inset -1px 0 0 #d9d3c8;}
#valist div.rowl{cursor:pointer;border:none;background:transparent;text-align:left;font:inherit;color:inherit;display:grid;align-items:center;padding:6px 8px;font-size:12px;border-bottom:1px solid #ddd;}
#valist div.rowl:hover{background:var(--hover);}
#valist div.rowl.on{background:var(--hover);font-weight:700;}
#vabody h2{margin:0 0 4px;font-size:16px;}
#vabody h3{margin:14px 0 6px;font-size:13px;}
.vaplayer{flex:0 0 auto;background:var(--bg);border-top:1px solid #c4bfb4;padding:2px 0 0;}
.vaplayer audio{width:100%;height:32px;display:block;}
.vaplayer .err{min-height:0;margin:2px 0 0;}
.vaplayer .err:empty{display:none;}
.job{display:none;margin:8px 0;max-width:520px;}
.job.on{display:block;}
.job .bar{height:12px;width:100%;background:var(--btn);border:1px solid #c4bfb4;}
.job .bar i{display:block;height:100%;background:var(--bar);width:0;}
.job .meta{margin-top:4px;font-style:italic;}
#vatx .tx{display:grid;grid-template-columns:64px 130px 1fr;gap:6px 10px;font-size:12px;padding:4px 0;border-bottom:1px solid #eee;cursor:pointer;}
#vatx .tx:hover{background:var(--hover);}
.bottom{flex:1;display:flex;flex-direction:column;min-height:180px;}
.split{display:flex;flex:1;min-height:0;gap:0;}
.tlist{flex:0 0 720px;width:720px;min-width:280px;max-width:75%;display:flex;flex-direction:column;}
.tlist .list{flex:1;min-height:0;}
.tlist button.rowl{cursor:pointer;border:none;background:transparent;width:100%;text-align:left;font:inherit;color:inherit;}
.tlist .rowl.on{background:var(--hover);font-weight:700;}
.splitbar{flex:0 0 6px;width:6px;cursor:col-resize;background:#c4bfb4;align-self:stretch;margin:0 4px;}
.splitbar:hover,.splitbar.drag{background:var(--bar);}
.agrid{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;}
.agrid .list{flex:1;min-height:0;}
#conflictbox{display:none;}
#conflictbox.on{display:block;}
</style>
</head>
<body>
<div class="top">
  <h1>VA Reader – Vereinsarchiv</h1>
</div>
<div class="pane">
  <fieldset>
    <legend> 1. Paket einspielen </legend>
    <p class="hint">Zip aus Königssturz. Kein cURL, kein Netz. Daten liegen nur im Konto dieses Rechner-Nutzers. Gleiche Einträge werden übersprungen; bei abweichendem Inhalt: abbrechen oder Kopie anlegen.</p>
    <div class="inline">
      <input id="vafile" type="file" accept=".zip,application/zip"/>
      <button class="act" type="button" id="btnImp" onclick="importZip()">Einspielen</button>
      <button class="act" type="button" id="btnImpStop" onclick="stopImport()" disabled>Abbrechen</button>
      <span class="status" id="impstatus">Bereit</span>
    </div>
    <div class="job" id="impjob"><div class="bar"><i id="impbar"></i></div><div class="meta" id="impmeta"></div></div>
    <div class="err" id="imperr"></div>
    <div id="conflictbox">
      <p class="warn" id="conflicttext"></p>
      <div class="inline">
        <button class="act" type="button" onclick="resolveConflict('abort')">Abbrechen</button>
        <button class="act" type="button" onclick="resolveConflict('copy')">Kopie speichern</button>
      </div>
    </div>
  </fieldset>
  <fieldset class="bottom">
    <legend> 2. Lokal browsen </legend>
    <div class="inline">
      <label for="vafilter">Quelle</label>
      <select id="vafilter" onchange="renderVaList()">
        <option value="all">Alle</option>
        <option value="public">Öffentlich</option>
        <option value="intern">Intern</option>
      </select>
      <label for="vasearch">Suche</label>
      <input id="vasearch" type="text" oninput="renderVaList()" placeholder="Titel, Thema, Datum"/>
      <button class="act" type="button" onclick="vaSelAllVisible(true)">Alle</button>
      <button class="act" type="button" onclick="vaSelAllVisible(false)">Keine</button>
      <button class="act" type="button" id="btnVaDel" onclick="deleteVaSelected()">Auswahl löschen</button>
      <span class="status" id="vahint">Noch keine Stammtische lokal</span>
    </div>
    <div class="split">
      <div class="tlist" id="valistbox">
        <div class="list" id="valist">
          <div class="head" id="vahead"></div>
          <div id="varows"></div>
        </div>
      </div>
      <div class="splitbar" id="vasplitbar"></div>
      <div class="agrid">
        <div class="list" id="vabody"></div>
        <div class="vaplayer" id="vaplayer" style="display:none">
          <audio id="vaaudio" controls></audio>
          <div class="job" id="vaaudiojob"><div class="bar"><i id="vaaudiobar"></i></div><div class="meta" id="vaaudiometa"></div></div>
          <p class="err" id="vaaudioerr"></p>
        </div>
      </div>
    </div>
  </fieldset>
</div>
<script>
function esc(s){return (s||'').toString().replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function escAttr(s){return esc(s).replace(/"/g,'&quot;');}
function vaDur(n){
  n=parseInt(n,10);
  if(isNaN(n)||n<0) return '–';
  const h=Math.floor(n/3600);
  const m=Math.floor((n%3600)/60);
  const s=n%60;
  const ss=String(s).padStart(2,'0');
  if(h) return h+':'+String(m).padStart(2,'0')+':'+ss;
  return m+':'+ss;
}
function vaKindLabel(kind){ return kind==='intern'?'Intern':'Öffentlich'; }
let vaItems=[];
let vaOpen=null;
let vaAbort=null;
let vaSelected={};
let vaColsFitted=false;
let vaColWidths=null;
const VA_COL_LABELS=['','Datum','Titel','Quelle','Audio','Docs','Dauer'];
const VA_COL_MIN=[28,72,140,58,44,44,48];
const VA_COL_MAX=[40,110,260,90,56,56,64];
let vaConv={running:false,kind:'',id:'',pct:0,file_pct:0,title:''};
function vaKey(it){ return (it.kind||'')+'/'+(it.id||''); }
function vaYes(v){ return v?'ja':'–'; }
function vaConvTitle(st){
  const raw=(st&&st.status)||'';
  const m=raw.match(/:\s*(.+)$/);
  return m?m[1].trim():'';
}
function vaConvMatch(it){
  if(!vaConv.running) return false;
  if(vaConv.id&&it.id===vaConv.id) return !vaConv.kind||vaConv.kind===it.kind;
  if(vaConv.title){
    const t=(it.titel||it.id||'').toLowerCase();
    if(t&&vaConv.title.toLowerCase().indexOf(t)>=0) return true;
    if(t&&t.indexOf(vaConv.title.toLowerCase())>=0) return true;
  }
  return false;
}
function vaConvPct(){
  return Math.max(0, Math.min(99, vaConv.file_pct||vaConv.pct||0));
}
function vaRing(it){
  if(!vaConvMatch(it)) return '';
  return '<span class="vaconv">umwandeln '+vaConvPct()+'%</span>';
}
function vaApplyCols(){
  const list=document.getElementById('valist');
  if(!list) return;
  if(!vaColWidths||vaColWidths.length!==VA_COL_MIN.length) vaColWidths=VA_COL_MIN.slice();
  const parts=vaColWidths.map(function(w,i){
    const lo=VA_COL_MIN[i];
    const hi=VA_COL_MAX[i]||Math.max(lo*4, 320);
    const n=Math.max(lo, Math.min(hi, w||lo));
    vaColWidths[i]=n;
    return n+'px';
  });
  list.style.setProperty('--va-cols', parts.join(' '));
}
function vaRenderHead(){
  const head=document.getElementById('vahead');
  if(!head) return;
  head.innerHTML=VA_COL_LABELS.map(function(label,i){
    const grip=i<VA_COL_LABELS.length-1?'<i class="colres" data-col="'+i+'" title="Breite ziehen"></i>':'';
    if(i===0) return '<span class="vacol vasel"><input type="checkbox" id="vaSelAll" title="Sichtbare auswählen">'+grip+'</span>';
    return '<span class="vacol"><span class="vacol-lab">'+esc(label)+'</span>'+grip+'</span>';
  }).join('');
  const all=document.getElementById('vaSelAll');
  if(all){
    all.onchange=function(){ vaSelAllVisible(!!all.checked); };
  }
  head.querySelectorAll('.colres').forEach(function(grip){
    grip.onmousedown=function(e){
      e.preventDefault();
      e.stopPropagation();
      const idx=parseInt(grip.getAttribute('data-col'),10);
      if(isNaN(idx)) return;
      if(!vaColWidths||vaColWidths.length!==VA_COL_MIN.length) vaFitCols(true);
      const startX=e.clientX;
      const startW=vaColWidths[idx];
      grip.classList.add('drag');
      function move(ev){
        const hi=VA_COL_MAX[idx]||Math.max(VA_COL_MIN[idx]*4, 480);
        vaColWidths[idx]=Math.max(VA_COL_MIN[idx], Math.min(hi, startW+(ev.clientX-startX)));
        vaApplyCols();
      }
      function up(){
        grip.classList.remove('drag');
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
      }
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    };
  });
}
function vaFitCols(force){
  if(vaColsFitted && !force) return;
  const list=document.getElementById('valist');
  if(!list) return;
  const widths=VA_COL_MIN.slice();
  const canvas=document.createElement('canvas');
  const ctx=canvas.getContext('2d');
  function grow(i, text, bold){
    const hi=VA_COL_MAX[i]||320;
    if(!ctx){ widths[i]=Math.max(widths[i], Math.min(hi, 12*((text||'').length)+18)); return; }
    ctx.font=(bold?'700 ':'')+'12px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
    widths[i]=Math.max(widths[i], Math.min(hi, Math.ceil(ctx.measureText(text||'').width)+18));
  }
  VA_COL_LABELS.forEach(function(label,i){ if(i) grow(i, label, true); });
  const filter=document.getElementById('vafilter').value;
  const q=(document.getElementById('vasearch').value||'').toLowerCase().trim();
  vaItems.filter(function(it){
    if(filter!=='all' && it.kind!==filter) return false;
    if(!q) return true;
    const hay=((it.titel||'')+' '+(it.datum||'')+' '+(it.ort||'')+' '+((it.themen||[]).join(' '))+' '+(it.hinweis||'')).toLowerCase();
    return hay.indexOf(q)>=0;
  }).slice(0,200).forEach(function(it){
    const mark=it.hinweis?' · Kopie':'';
    grow(1, it.datum||'');
    grow(2, (it.titel||it.id||'')+mark+(vaConvMatch(it)?' umwandeln 99%':''));
    grow(3, vaKindLabel(it.kind));
    grow(4, vaYes(it.has_audio));
    grow(5, vaYes(it.has_folien));
    grow(6, vaDur(it.dauer_sek));
  });
  vaColWidths=widths;
  vaColsFitted=true;
  vaApplyCols();
}
function applyVaConv(st){
  st=st||{};
  const next={
    running:!!st.running,
    kind:st.kind||'',
    id:st.id||st.item_id||'',
    pct:st.pct||0,
    file_pct:st.file_pct||0,
    title:vaConvTitle(st)
  };
  const moved=vaConv.running!==next.running||vaConv.kind!==next.kind||vaConv.id!==next.id||vaConv.title!==next.title;
  vaConv=next;
  if(moved){
    renderVaList();
    const row=document.querySelector('#varows .rowl.converting');
    if(row&&row.scrollIntoView) row.scrollIntoView({block:'nearest'});
    return;
  }
  const lab=document.querySelector('#varows .rowl.converting .vaconv');
  if(lab) lab.textContent='umwandeln '+vaConvPct()+'%';
}
async function loadVaLocal(){
  const r=await fetch('/api/va/local');
  const d=await r.json().catch(function(){return [];});
  vaItems=Array.isArray(d)?d:[];
  vaColsFitted=false;
  renderVaList();
}
function renderVaList(){
  if(!document.getElementById('vahead')||!document.getElementById('vahead').children.length) vaRenderHead();
  const filter=document.getElementById('vafilter').value;
  const q=(document.getElementById('vasearch').value||'').toLowerCase().trim();
  const rows=vaItems.filter(function(it){
    if(filter!=='all' && it.kind!==filter) return false;
    if(!q) return true;
    const hay=((it.titel||'')+' '+(it.datum||'')+' '+(it.ort||'')+' '+((it.themen||[]).join(' '))+' '+(it.hinweis||'')).toLowerCase();
    return hay.indexOf(q)>=0;
  });
  const nSel=rows.filter(function(it){return !!vaSelected[vaKey(it)];}).length;
  document.getElementById('vahint').textContent=rows.length?(rows.length+' Stammtische'+(nSel?' · '+nSel+' gewählt':'')):'Noch keine Stammtische lokal';
  document.getElementById('varows').innerHTML=rows.map(function(it){
    const key=vaKey(it);
    const on=vaOpen&&vaOpen.kind===it.kind&&vaOpen.id===it.id?' on':'';
    const conv=vaConvMatch(it)?' converting':'';
    const mark=it.hinweis?' · Kopie':'';
    const checked=vaSelected[key]?' checked':'';
    return '<div class="rowl'+on+conv+'" data-kind="'+escAttr(it.kind)+'" data-id="'+escAttr(it.id)+'"><span class="vasel"><input type="checkbox" class="vaselbox"'+checked+'></span><span class="vadatum">'+esc(it.datum||'')+'</span><span class="vatitle"><span class="t">'+esc((it.titel||it.id||'')+mark)+'</span>'+vaRing(it)+'</span><span class="vaquelle">'+esc(vaKindLabel(it.kind))+'</span><span class="vaaudio">'+vaYes(it.has_audio)+'</span><span class="vadocs">'+vaYes(it.has_folien)+'</span><span class="vadauer">'+vaDur(it.dauer_sek)+'</span></div>';
  }).join('');
  const all=document.getElementById('vaSelAll');
  if(all) all.checked=rows.length>0 && nSel===rows.length;
  if(!vaColsFitted) vaFitCols(false);
}
function vaSelAllVisible(on){
  const filter=document.getElementById('vafilter').value;
  const q=(document.getElementById('vasearch').value||'').toLowerCase().trim();
  vaItems.forEach(function(it){
    if(filter!=='all' && it.kind!==filter) return;
    if(q){
      const hay=((it.titel||'')+' '+(it.datum||'')+' '+(it.ort||'')+' '+((it.themen||[]).join(' '))+' '+(it.hinweis||'')).toLowerCase();
      if(hay.indexOf(q)<0) return;
    }
    if(on) vaSelected[vaKey(it)]=true;
    else delete vaSelected[vaKey(it)];
  });
  renderVaList();
}
function vaSelectedItems(){
  return vaItems.filter(function(it){ return !!vaSelected[vaKey(it)]; });
}
async function openVa(kind, id){
  if(!kind||!id) return;
  if(vaAbort) vaAbort.abort();
  vaAbort=new AbortController();
  const token=vaAbort;
  vaOpen={kind:kind,id:id};
  renderVaList();
  try{
    const r=await fetch('/api/va/item?kind='+encodeURIComponent(kind)+'&id='+encodeURIComponent(id),{signal:token.signal});
    const d=await r.json().catch(function(){return {};});
    if(token!==vaAbort) return;
    if(!r.ok){ alert(d.detail||'Nicht gefunden'); return; }
    renderVaDetail(d);
  }catch(e){
    if(e&&e.name==='AbortError') return;
  }
}
async function showVaTranscript(){
  if(!vaOpen) return;
  const box=document.getElementById('vatxmount');
  const btn=document.getElementById('showtx');
  if(!box) return;
  if(btn) btn.disabled=true;
  try{
    const r=await fetch('/api/va/item?kind='+encodeURIComponent(vaOpen.kind)+'&id='+encodeURIComponent(vaOpen.id)+'&full=1');
    const d=await r.json().catch(function(){return {};});
    if(!r.ok){ alert(d.detail||'Transkript nicht lesbar'); return; }
    box.innerHTML=vaTxLines(d.transkript||[], d.sprecher||[]);
    if(btn) btn.style.display='none';
  }catch(e){
    if(btn) btn.disabled=false;
  }
}
function vaBlock(title, html){
  if(!html) return '';
  return '<h3>'+esc(title)+'</h3>'+html;
}
function vaStamp(v){
  if(v==null||v==='') return null;
  if(typeof v==='number') return v>100000?v/1000:v;
  const t=parseFloat(v);
  return isNaN(t)?null:t;
}
function vaTxLines(list, sprecher){
  if(!list||!list.length||!list.map) return '';
  return '<div id="vatx">'+list.map(function(t){
    const n=vaStamp(t.t!=null?t.t:(t.start!=null?t.start:t.zeit));
    const name=sprecher[t.sp]!=null?sprecher[t.sp]:(t.sprecher||t.speaker||('Sprecher '+(t.sp==null?'?':t.sp)));
    return '<div class="tx" data-t="'+(n||0)+'"><span>'+vaDur(n)+'</span><span>'+esc(name)+'</span><span>'+esc(t.text||t.titel||t.title||'')+'</span></div>';
  }).join('')+'</div>';
}
function renderVaDetail(item){
  const z=item.zusammenfassung&&typeof item.zusammenfassung==='object'?item.zusammenfassung:{};
  const sprecher=item.sprecher||[];
  const themen=(item.themen||[]).join? (item.themen||[]).join(', ') : '';
  let html='<h2>'+esc(item.titel||item.id||'')+'</h2>';
  html+='<p class="hint">'+esc(vaKindLabel(item.kind))+' · '+esc(item.datum||'')+(item.ort?' · '+esc(item.ort):'')+' · '+vaDur(item.dauer_sek)+(themen?' · '+esc(themen):'')+'</p>';
  if(item.hinweis) html+='<p class="warn">'+esc(item.hinweis)+'</p>';
  if(z.lead) html+=vaBlock('Kurzfassung','<p>'+esc(z.lead)+'</p>');
  if(z.fragen&&z.fragen.length){
    html+=vaBlock('Fragen', z.fragen.map(function(q){
      return '<p><strong>'+esc(q.frage||'')+'</strong><br>'+esc(q.antwort||'')+'</p>';
    }).join(''));
  }
  if(z.aufgaben&&z.aufgaben.length){
    html+=vaBlock('Aufgaben', z.aufgaben.map(function(a){
      return '<p><strong>'+esc(a.wer||'')+':</strong> '+esc(a.text||'')+'</p>';
    }).join(''));
  }
  if(z.sachstand&&z.sachstand.length){
    html+=vaBlock('Sachstand','<ul>'+z.sachstand.map(function(s){return '<li>'+esc(s)+'</li>';}).join('')+'</ul>');
  }
  if(z.offene_fragen&&z.offene_fragen.length){
    html+=vaBlock('Offene Fragen','<ul>'+z.offene_fragen.map(function(s){return '<li>'+esc(s)+'</li>';}).join('')+'</ul>');
  }
  if(item.kapitel&&item.kapitel.length&&item.kapitel.map){
    html+=vaBlock('Kapitel', vaTxLines(item.kapitel.map(function(c){
      if(typeof c==='string') return {t:0,text:c};
      return {t:c.t!=null?c.t:c.start, sp:c.sp, text:c.titel||c.title||c.name||c.text||''};
    }), sprecher));
  }
  if(item.folien_dateien&&item.folien_dateien.length){
    html+=vaBlock('Folien / Präsentation', item.folien_dateien.map(function(f){
      const low=(f||'').toLowerCase();
      const kind=low.indexOf('.pdf')>=0?'PDF':(low.indexOf('.ppt')>=0||low.indexOf('.key')>=0||low.indexOf('.odp')>=0?'Präsentation':'Datei');
      return '<p><button class="act" type="button" data-kind="'+escAttr(item.kind)+'" data-id="'+escAttr(item.id)+'" data-folie="'+escAttr(f)+'">'+esc(kind)+' öffnen: '+esc(f)+'</button></p>';
    }).join(''));
  }
  const nv=item.nachverfolgung;
  if(nv&&typeof nv==='string'&&nv.trim()) html+=vaBlock('Nachverfolgung','<p>'+esc(nv)+'</p>');
  if((item.transkript&&item.transkript.length)||item.transkript_n){
    const n=(item.transkript&&item.transkript.length)?item.transkript.length:(item.transkript_n||0);
    html+=vaBlock('Transkript', '<p><button class="act" type="button" id="showtx">Transkript zeigen ('+n+' Absätze)</button></p><div id="vatxmount"></div>');
  }
  document.getElementById('vabody').innerHTML=html;
  const txBtn=document.getElementById('showtx');
  if(txBtn) txBtn.onclick=showVaTranscript;
  armVaAudio(item);
}
async function deleteVaSelected(){
  const picked=vaSelectedItems();
  const hint=document.getElementById('vahint');
  if(!picked.length){
    if(hint) hint.textContent='Keine Stammtische markiert.';
    alert('Keine Stammtische markiert.');
    return;
  }
  if(!confirm(picked.length+' Stammtisch'+(picked.length===1?'':'e')+' lokal löschen?\n\nAudio, Folien und Text werden entfernt. Beim nächsten Import können sie neu geladen werden.')) return;
  const payload={items:picked.map(function(it){return {kind:it.kind,id:it.id};})};
  const r=await fetch('/api/va/item/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const d=await r.json().catch(function(){return {};});
  if(!r.ok){ alert(d.detail||'Löschen fehlgeschlagen'); return; }
  const gone={};
  (d.deleted||payload.items).forEach(function(it){ gone[vaKey(it)]=true; });
  if(vaOpen && gone[vaKey(vaOpen)]){
    vaOpen=null;
    document.getElementById('vabody').innerHTML='';
    armVaAudio({});
  }
  Object.keys(gone).forEach(function(k){ delete vaSelected[k]; });
  vaItems=vaItems.filter(function(it){ return !gone[vaKey(it)]; });
  vaColsFitted=false;
  renderVaList();
  if(hint) hint.textContent=(d.deleted||[]).length+' gelöscht';
}
function fmtEta(sec){
  if(sec==null||sec==='') return '';
  sec=Math.max(0,parseInt(sec,10)||0);
  if(sec<60) return 'noch ca. '+sec+' s';
  return 'noch ca. '+Math.floor(sec/60)+' min '+ (sec%60)+' s';
}
function setJob(id, on, pct, text){
  const box=document.getElementById(id);
  if(!box) return;
  box.className=on?'job on':'job';
  const bar=box.querySelector('i');
  if(bar) bar.style.width=(pct||0)+'%';
  const meta=box.querySelector('.meta');
  if(meta) meta.textContent=text||'';
}
let vaAudioPoll=null;
function armVaAudio(item){
  const player=document.getElementById('vaaudio');
  const wrap=document.getElementById('vaplayer');
  const err=document.getElementById('vaaudioerr');
  if(vaAudioPoll){ clearInterval(vaAudioPoll); vaAudioPoll=null; }
  if(player){ player.onerror=null; player.oncanplay=null; }
  if(!item.has_audio){
    wrap.style.display='none';
    if(err) err.textContent='';
    setJob('vaaudiojob', false, 0, '');
    if(player){ player.removeAttribute('src'); player.load(); }
    return;
  }
  wrap.style.display='block';
  if(err) err.textContent='';
  player.oncanplay=function(){ if(err) err.textContent=''; setJob('vaaudiojob', false, 0, ''); };
  player.onerror=function(){
    const code=player.error&&player.error.code;
    if(!code || code===1) return;
    if(err) err.textContent='Diese Datei kann der Player nicht lesen.';
  };
  const src='/api/va/audio/'+encodeURIComponent(item.kind)+'/'+encodeURIComponent(item.id);
  const opened=item.kind+'/'+item.id;
  if(item.audio_ready){
    player.src=src;
    player.setAttribute('data-id', opened);
  }else{
    player.removeAttribute('src');
    player.removeAttribute('data-id');
    player.load();
    setJob('vaaudiojob', true, 0, 'Audio wird nach Import/Download für den Player vorbereitet …');
  }
  async function tickAudio(){
    if(!vaOpen||(vaOpen.kind+'/'+vaOpen.id)!==opened) return;
    const meta=await (await fetch('/api/va/audio/meta?kind='+encodeURIComponent(item.kind)+'&id='+encodeURIComponent(item.id))).json().catch(function(){return {};});
    const st=await (await fetch('/api/va/audio/prepare')).json().catch(function(){return {};});
    applyVaConv(st);
    if(!vaOpen||(vaOpen.kind+'/'+vaOpen.id)!==opened) return;
    if(meta.ready){
      if(player.getAttribute('data-id')!==opened){
        player.src=src;
        player.setAttribute('data-id', opened);
      }
    }
    if(st.running){
      setJob('vaaudiojob', true, st.pct||0, (st.status||'Wandle Audio …')+(st.eta_sec!=null?(' · '+fmtEta(st.eta_sec)):''));
    }else if(!meta.ready){
      setJob('vaaudiojob', true, 0, 'Audio wird nach Import/Download für den Player vorbereitet …');
    }else{
      setJob('vaaudiojob', false, 0, '');
    }
    if(meta.ready && !st.running && vaAudioPoll){
      clearInterval(vaAudioPoll);
      vaAudioPoll=null;
    }
  }
  tickAudio();
  if(!item.audio_ready){
    vaAudioPoll=setInterval(tickAudio, 1200);
  }
}
function seekVa(t){
  const player=document.getElementById('vaaudio');
  const n=parseFloat(t);
  if(!player||isNaN(n)) return;
  try{ player.currentTime=n; }catch(e){}
  if(player.paused) player.play().catch(function(){});
}
function hideConflict(){ document.getElementById('conflictbox').className=''; }
function showConflict(rows){
  const n=rows.length;
  document.getElementById('conflicttext').textContent=n+' Eintrag/Einträge sind schon da, der Inhalt unterscheidet sich. Abbrechen (nichts ändern) oder Kopie speichern (beides behalten, Kopie mit Hinweis).';
  document.getElementById('conflictbox').className='on';
}
function summarize(d){
  return 'Neu '+ (d.added||0) +', schon da '+(d.skipped||0)+', Audio ergänzt '+(d.updated_audio||0)+', Kopien '+(d.copies||0);
}
async function importZip(){
  const err=document.getElementById('imperr');
  err.textContent='';
  hideConflict();
  const f=document.getElementById('vafile').files[0];
  if(!f){ err.textContent='Bitte eine Zip wählen.'; return; }
  const fd=new FormData();
  fd.append('file', f);
  document.getElementById('impstatus').textContent='Lade Zip …';
  window._impPrev=false; window._impApply=false;
  const r=await fetch('/api/va/import',{method:'POST',body:fd});
  const d=await r.json().catch(function(){return {};});
  if(!r.ok){
    err.textContent=d.detail||'Import fehlgeschlagen';
    document.getElementById('impstatus').textContent='Fehler';
    return;
  }
}
function stopImport(){ fetch('/api/va/import/stop',{method:'POST'}); }
async function tickImport(){
  const s=await (await fetch('/api/va/import/status')).json().catch(function(){return {};});
  const running=!!s.running;
  const btn=document.getElementById('btnImp');
  const stop=document.getElementById('btnImpStop');
  if(btn) btn.disabled=running;
  if(stop) stop.disabled=!running;
  let p=s.progress||{};
  const ap=s.audio_prep||{};
  if(running && (s.phase==='convert'||s.phase==='apply') && ap.running){
    const fpAp=parseInt(ap.file_pct,10)||0;
    const pctAp=parseInt(ap.pct,10)||0;
    const pctImp=parseInt(p.pct,10)||0;
    p={
      status: ap.status||p.status||'Wandle Audio …',
      pct: Math.max(pctImp, pctAp, fpAp ? Math.min(99, 90 + Math.floor(fpAp / 10)) : 0),
      file_pct: fpAp || p.file_pct || 0,
      eta_sec: p.eta_sec!=null ? p.eta_sec : ap.eta_sec
    };
  }
  const fp=(p.file_pct!=null&&p.file_pct!=='')?(' · Datei '+p.file_pct+'%'):'';
  setJob('impjob', running || ((p.pct||0)>0 && (p.pct||0)<100), p.pct||0, (p.status||'')+fp+(p.eta_sec!=null&&running?(' · '+fmtEta(p.eta_sec)):''));
  if(p.status||running) document.getElementById('impstatus').textContent=(p.status||'')+fp;
  if(s.error) document.getElementById('imperr').textContent=s.error;
  if(!running && s.preview_ready && s.preview && !window._impPrev){
    window._impPrev=true;
    const d=s.preview;
    if((d.counts&&d.counts.conflict)>0){
      showConflict(d.conflict||[]);
      document.getElementById('impstatus').textContent='Unterschiede – bitte wählen';
    }else{
      resolveConflict('abort');
    }
  }
  if(!running && s.apply_ready && s.result && !window._impApply){
    window._impApply=true;
    hideConflict();
    const d=s.result;
    if(d.aborted) document.getElementById('impstatus').textContent=d.message||'Abgebrochen';
    else { document.getElementById('impstatus').textContent=summarize(d); loadVaLocal(); }
  }
  applyVaConv(s.audio_prep);
}
async function resolveConflict(mode){
  const err=document.getElementById('imperr');
  window._impApply=false;
  const r=await fetch('/api/va/import/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on_conflict:mode})});
  const d=await r.json().catch(function(){return {};});
  hideConflict();
  if(!r.ok){
    err.textContent=d.detail||'Nicht übernommen';
    document.getElementById('impstatus').textContent='Fehler';
  }
}
document.getElementById('varows').addEventListener('click', function(ev){
  if(ev.target.closest('.vaselbox')||ev.target.closest('.vasel')){
    const box=ev.target.closest('.rowl');
    if(!box) return;
    const cb=box.querySelector('.vaselbox');
    if(!cb) return;
    if(ev.target!==cb) cb.checked=!cb.checked;
    const key=box.getAttribute('data-kind')+'/'+box.getAttribute('data-id');
    if(cb.checked) vaSelected[key]=true;
    else delete vaSelected[key];
    renderVaList();
    return;
  }
  const row=ev.target.closest('div.rowl');
  if(!row) return;
  openVa(row.getAttribute('data-kind'), row.getAttribute('data-id'));
});
document.getElementById('vabody').addEventListener('click', function(ev){
  const btn=ev.target.closest('button[data-folie]');
  if(btn){
    ev.preventDefault();
    fetch('/api/va/file/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:btn.getAttribute('data-kind'),id:btn.getAttribute('data-id'),name:btn.getAttribute('data-folie')})})
      .then(function(r){return r.json().catch(function(){return {};}).then(function(d){if(!r.ok) alert(d.detail||'Datei nicht gefunden');});});
    return;
  }
  const row=ev.target.closest('.tx');
  if(!row) return;
  const t=row.getAttribute('data-t');
  if(t==null||t==='') return;
  seekVa(t);
});
vaRenderHead();
(function(){
  const bar=document.getElementById('vasplitbar');
  const box=document.getElementById('valistbox');
  bar.addEventListener('mousedown', function(e){
    const startX=e.clientX;
    const startW=box.getBoundingClientRect().width;
    function move(ev){
      const w=Math.max(180, Math.min(window.innerWidth*0.7, startW+(ev.clientX-startX)));
      box.style.flexBasis=w+'px';
      box.style.width=w+'px';
    }
    function up(){
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      bar.classList.remove('drag');
    }
    bar.classList.add('drag');
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
    e.preventDefault();
  });
})();
loadVaLocal();
setInterval(tickImport, 800);
setInterval(async function(){
  const st=await (await fetch('/api/va/audio/prepare')).json().catch(function(){return {};});
  applyVaConv(st);
}, 500);
</script>
</body></html>
"""


app = FastAPI(title="VA Reader")


def _root():
    os.makedirs(DATA_ROOT, mode=0o700, exist_ok=True)
    return DATA_ROOT


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@app.get("/api/va/local")
def api_local():
    return va.list_local(_root())


@app.get("/api/va/item")
def api_item(kind: str, item_id: str = Query(..., alias="id"), full: bool = Query(False)):
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    try:
        return va.item_for_ui(kind, item_id, _root(), include_transcript=full)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/va/item/delete")
def api_item_delete(body: Dict[str, Any]):
    raw_items = body.get("items")
    if isinstance(raw_items, list) and raw_items:
        items = [{"kind": str(it.get("kind") or ""), "id": str(it.get("id") or "")} for it in raw_items if isinstance(it, dict)]
    else:
        items = [{"kind": str(body.get("kind") or ""), "id": str(body.get("id") or "")}]
    if not items:
        raise HTTPException(status_code=400, detail="Keine Einträge.")
    out = va.delete_items(items, _root())
    if not out.get("deleted") and out.get("errors"):
        raise HTTPException(status_code=400, detail=(out["errors"][0] or {}).get("error") or "Löschen fehlgeschlagen")
    return out


@app.get("/api/va/audio/{kind}/{item_id}")
def api_audio(kind: str, item_id: str):
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    try:
        safe = va.sanitize_id(item_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültige ID.")
    path = va.find_audio(kind, safe, _root())
    if not path:
        raise HTTPException(status_code=404, detail="Keine Audiodatei.")
    path, media = va.playback_audio(path)
    return FileResponse(
        path,
        media_type=media,
        filename=os.path.basename(path),
        content_disposition_type="inline",
    )


@app.get("/api/va/file/{kind}/{item_id}/{name}")
def api_file(kind: str, item_id: str, name: str):
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    try:
        path = va.folie_path(kind, item_id, name, _root())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    media = va.folie_media(path)
    return FileResponse(
        path,
        media_type=media,
        filename=os.path.basename(path),
        content_disposition_type="inline" if media == "application/pdf" else "attachment",
    )


@app.post("/api/va/file/open")
def api_file_open(body: Dict[str, Any]):
    kind = str(body.get("kind") or "")
    item_id = str(body.get("id") or "")
    name = str(body.get("name") or "")
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    try:
        path = va.folie_path(kind, item_id, name, _root())
        va.open_local_file(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.post("/api/va/import")
async def api_import(file: UploadFile = File(...)):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Leere Datei.")
    dest = os.path.join(os.path.dirname(_root()) or ".", "pending-import.zip")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as handle:
        handle.write(raw)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    try:
        STATE.start_preview(dest)
    except Exception as exc:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.get("/api/va/import/status")
def api_import_status():
    return STATE.snapshot()


@app.post("/api/va/import/stop")
def api_import_stop():
    STATE.request_stop()
    return {"ok": True}


@app.post("/api/va/import/apply")
def api_apply(body: Dict[str, Any]):
    mode = str((body or {}).get("on_conflict") or "abort")
    try:
        STATE.start_apply(mode)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.get("/api/va/audio/meta")
def api_va_audio_meta(kind: str, item_id: str = Query(..., alias="id")):
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    path = va.find_audio(kind, va.sanitize_id(item_id), _root())
    if not path:
        raise HTTPException(status_code=404, detail="Keine Audiodatei.")
    return {"ready": not va.needs_audio_convert(path)}


@app.get("/api/va/audio/prepare")
def api_va_audio_prepare_status():
    return va.AUDIO_PREP.snapshot()


@app.post("/api/va/audio/prepare")
def api_va_audio_prepare(body: Dict[str, Any]):
    kind = str((body or {}).get("kind") or "")
    item_id = str((body or {}).get("id") or "")
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    path = va.find_audio(kind, va.sanitize_id(item_id), _root())
    if not path:
        raise HTTPException(status_code=404, detail="Keine Audiodatei.")
    with va.AUDIO_PREP.lock:
        va.AUDIO_PREP.kind = kind
        va.AUDIO_PREP.item_id = item_id
    threading.Thread(target=va.prepare_playback, args=(path,), daemon=True).start()
    return {"ok": True}


@app.post("/api/va/audio/prepare/stop")
def api_va_audio_prepare_stop():
    va.AUDIO_PREP.request_stop()
    return {"ok": True}


def _no_browser():
    if "--no-browser" in sys.argv:
        return True
    return os.environ.get("KINGFALL_NO_BROWSER", "").strip().lower() in ("1", "true", "yes")


def serve():
    try:
        import uvicorn
    except ImportError:
        print("FastAPI/Uvicorn fehlen. Einmalig:")
        print("  python3 -m pip install -r requirements.txt")
        sys.exit(1)
    try:
        port = pick_port(HOST)
    except OSError as exc:
        print("Kein lokaler Port frei (%s)." % exc)
        sys.exit(1)
    url = "http://%s:%s/" % (HOST, port)
    print("VA Reader: %s" % url)
    print("KINGFALL_READY url=%s" % url)
    print("Daten: %s" % _root())
    print("Nur für diesen Benutzer, nur localhost.")
    sys.stdout.flush()
    va.start_convert_pending(root=_root())

    if not _no_browser():
        def _open():
            time.sleep(0.8)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    # Reader bevorzugt 18766, fällt auf die üblichen Ports zurück.
    if "--port" not in sys.argv:
        sys.argv.extend(["--port", os.environ.get("KINGFALL_PORT") or "18766"])
    serve()
