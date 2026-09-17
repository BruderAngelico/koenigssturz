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
#valist .head,#valist .rowl{grid-template-columns:88px 1.6fr 72px 44px 52px;}
#valist .rowl span:nth-child(2){overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
#vabody h2{margin:0 0 4px;font-size:16px;}
#vabody h3{margin:14px 0 6px;font-size:13px;}
.vaplayer{flex:0 0 auto;background:var(--bg);border-top:1px solid #c4bfb4;padding:8px 0 0;}
.vaplayer audio{width:100%;}
#vatx .tx{display:grid;grid-template-columns:64px 130px 1fr;gap:6px 10px;font-size:12px;padding:4px 0;border-bottom:1px solid #eee;cursor:pointer;}
#vatx .tx:hover{background:var(--hover);}
.bottom{flex:1;display:flex;flex-direction:column;min-height:180px;}
.split{display:flex;flex:1;min-height:0;gap:0;}
.tlist{flex:0 0 280px;width:280px;min-width:180px;max-width:70%;display:flex;flex-direction:column;}
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
      <button class="act" type="button" onclick="importZip()">Einspielen</button>
      <span class="status" id="impstatus">Bereit</span>
    </div>
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
      <span class="status" id="vahint">Noch keine Stammtische lokal</span>
    </div>
    <div class="split">
      <div class="tlist" id="valistbox">
        <div class="list" id="valist">
          <div class="head"><span>Datum</span><span>Titel</span><span>Quelle</span><span>Audio</span><span>Dauer</span></div>
          <div id="varows"></div>
        </div>
      </div>
      <div class="splitbar" id="vasplitbar"></div>
      <div class="agrid">
        <div class="list" id="vabody"></div>
        <div class="vaplayer" id="vaplayer" style="display:none">
          <audio id="vaaudio" controls></audio>
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
async function loadVaLocal(){
  const r=await fetch('/api/va/local');
  const d=await r.json().catch(function(){return [];});
  vaItems=Array.isArray(d)?d:[];
  renderVaList();
}
function renderVaList(){
  const filter=document.getElementById('vafilter').value;
  const q=(document.getElementById('vasearch').value||'').toLowerCase().trim();
  const rows=vaItems.filter(function(it){
    if(filter!=='all' && it.kind!==filter) return false;
    if(!q) return true;
    const hay=((it.titel||'')+' '+(it.datum||'')+' '+(it.ort||'')+' '+((it.themen||[]).join(' '))+' '+(it.hinweis||'')).toLowerCase();
    return hay.indexOf(q)>=0;
  });
  document.getElementById('vahint').textContent=rows.length?(rows.length+' Stammtische'):'Noch keine Stammtische lokal';
  document.getElementById('varows').innerHTML=rows.map(function(it){
    const on=vaOpen&&vaOpen.kind===it.kind&&vaOpen.id===it.id?' on':'';
    const mark=it.hinweis?' · Kopie':'';
    return '<button type="button" class="rowl'+on+'" data-kind="'+escAttr(it.kind)+'" data-id="'+escAttr(it.id)+'"><span>'+esc(it.datum||'')+'</span><span>'+esc((it.titel||it.id||'')+mark)+'</span><span>'+esc(vaKindLabel(it.kind))+'</span><span>'+(it.has_audio?'ja':'–')+'</span><span>'+vaDur(it.dauer_sek)+'</span></button>';
  }).join('');
}
async function openVa(kind, id){
  if(!kind||!id) return;
  vaOpen={kind:kind,id:id};
  renderVaList();
  const r=await fetch('/api/va/item?kind='+encodeURIComponent(kind)+'&id='+encodeURIComponent(id));
  const d=await r.json().catch(function(){return {};});
  if(!r.ok){ alert(d.detail||'Nicht gefunden'); return; }
  renderVaDetail(d);
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
    html+=vaBlock('Folien', item.folien_dateien.map(function(f){
      const href='/api/va/file/'+encodeURIComponent(item.kind)+'/'+encodeURIComponent(item.id)+'/'+encodeURIComponent(f);
      return '<p><a href="'+href+'" target="_blank" rel="noopener">'+esc(f)+'</a></p>';
    }).join(''));
  }
  const nv=item.nachverfolgung;
  if(nv&&typeof nv==='string'&&nv.trim()) html+=vaBlock('Nachverfolgung','<p>'+esc(nv)+'</p>');
  if(item.transkript&&item.transkript.length){
    html+=vaBlock('Transkript', vaTxLines(item.transkript, sprecher));
  }
  document.getElementById('vabody').innerHTML=html;
  const player=document.getElementById('vaaudio');
  const wrap=document.getElementById('vaplayer');
  if(item.has_audio){
    wrap.style.display='block';
    player.src='/api/va/audio/'+encodeURIComponent(item.kind)+'/'+encodeURIComponent(item.id);
  }else{
    wrap.style.display='none';
    player.removeAttribute('src');
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
  document.getElementById('impstatus').textContent='Prüfe Paket …';
  const r=await fetch('/api/va/import',{method:'POST',body:fd});
  const d=await r.json().catch(function(){return {};});
  if(!r.ok){
    err.textContent=d.detail||'Import fehlgeschlagen';
    document.getElementById('impstatus').textContent='Fehler';
    return;
  }
  if((d.counts&&d.counts.conflict)>0){
    showConflict(d.conflict||[]);
    document.getElementById('impstatus').textContent='Unterschiede – bitte wählen';
    return;
  }
  await resolveConflict('abort');
}
async function resolveConflict(mode){
  const err=document.getElementById('imperr');
  const r=await fetch('/api/va/import/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on_conflict:mode})});
  const d=await r.json().catch(function(){return {};});
  hideConflict();
  if(!r.ok){
    err.textContent=d.detail||'Nicht übernommen';
    document.getElementById('impstatus').textContent='Fehler';
    return;
  }
  if(d.aborted){
    document.getElementById('impstatus').textContent=d.message||'Abgebrochen';
    return;
  }
  document.getElementById('impstatus').textContent=summarize(d);
  loadVaLocal();
}
document.getElementById('varows').addEventListener('click', function(ev){
  const btn=ev.target.closest('button.rowl');
  if(!btn) return;
  openVa(btn.getAttribute('data-kind'), btn.getAttribute('data-id'));
});
document.getElementById('vabody').addEventListener('click', function(ev){
  const row=ev.target.closest('.tx');
  if(!row) return;
  const t=row.getAttribute('data-t');
  if(t==null||t==='') return;
  seekVa(t);
});
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
</script>
</body></html>
"""


app = FastAPI(title="VA Reader")


def _root():
    os.makedirs(DATA_ROOT, mode=0o700, exist_ok=True)
    return DATA_ROOT


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


@app.get("/api/va/local")
def api_local():
    return va.list_local(_root())


@app.get("/api/va/item")
def api_item(kind: str, item_id: str = Query(..., alias="id")):
    if kind not in va.KINDS:
        raise HTTPException(status_code=400, detail="Unbekannte Quelle.")
    try:
        return va.item_for_ui(kind, item_id, _root())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


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
    ext = os.path.splitext(path)[1].lower()
    media = VA_AUDIO_TYPES.get(ext, "application/octet-stream")
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
    return FileResponse(path, filename=os.path.basename(path))


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
        preview = vapack.preview_import(dest, _root())
    except Exception as exc:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=str(exc))
    with STATE.lock:
        if STATE.pending_zip and STATE.pending_zip != dest:
            try:
                os.remove(STATE.pending_zip)
            except OSError:
                pass
        STATE.pending_zip = dest
    return preview


@app.post("/api/va/import/apply")
def api_apply(body: Dict[str, Any]):
    mode = str((body or {}).get("on_conflict") or "abort")
    with STATE.lock:
        path = STATE.pending_zip
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=400, detail="Kein Paket bereit. Zuerst eine Zip einspielen.")
    try:
        result = vapack.apply_import(path, dest_root=_root(), on_conflict=mode)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result.get("ok") or result.get("aborted"):
        with STATE.lock:
            STATE.pending_zip = None
        try:
            os.remove(path)
        except OSError:
            pass
    return result


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
    print("Daten: %s" % _root())
    print("Nur für diesen Benutzer, nur localhost.")

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
