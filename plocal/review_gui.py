"""Cascade Reviewer control room (stdlib server + single-page UI, no extra dependencies besides PyMuPDF).

On top of the real PDF it draws, live:
  * RED word boxes sweeping over the text the model is reading (prefill) and the exact words it cites
  * BLUE/PURPLE arcs from the analysed passage to what it is compared with (other sections / experimental
    log) with a chip saying HOW (value / concept / evidence, "exceeds the evidence of", ...)
  * GREEN rewritten passages, AMBER suggestions you can apply with one click
  * minimap of paper attention, cascade timeline (fast vs deep model), diffs with justification
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None


class Viewer:
    def __init__(self, out):
        self.out = Path(out)
        self.docs, self.cache, self.lock = {}, {}, threading.Lock()

    def path(self, which):
        return self.out / "view" / f"{which}.pdf"

    def doc(self, which):
        with self.lock:
            p = self.path(which)
            if which not in self.docs and p.exists() and fitz:
                self.docs[which] = fitz.open(str(p))
            return self.docs.get(which)

    def png(self, which, n):
        key = (which, n)
        if key in self.cache:
            return self.cache[key]
        d = self.doc(which)
        if not d or n >= len(d):
            return None
        with self.lock:
            data = d[n].get_pixmap(matrix=fitz.Matrix(1.8, 1.8)).tobytes("png")
        self.cache[key] = data
        return data

    def layout(self):
        res = {"fitz": fitz is not None, "pages": {}}
        for w in ("before", "after", "redline"):
            d = self.doc(w)
            if d:
                res["pages"][w] = [[p.rect.width, p.rect.height] for p in d]
        return res


def serve(mon, out, port=8765):
    viewer = Viewer(out)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json", code=200):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            full = self.path
            p = full.split("?")[0]
            try:
                if p == "/":
                    self._send(HTML, "text/html; charset=utf-8")
                elif p == "/status.json":
                    self._send(json.dumps(mon.snapshot(), ensure_ascii=False, default=str))
                elif p == "/geo.json":
                    since = int(full.split("since=")[1]) if "since=" in full else 0
                    self._send(json.dumps(mon.geo_since(since)))
                elif p == "/layout.json":
                    self._send(json.dumps(viewer.layout()))
                elif p.startswith("/page/"):
                    _, _, which, n = p.split("/")
                    data = viewer.png(which, int(n.split(".")[0]))
                    self._send(data, "image/png") if data else self._send(b"", "text/plain", 404)
                elif p.startswith("/pdf/"):
                    f = viewer.path(p.split("/")[2])
                    self._send(f.read_bytes(), "application/pdf") if f.exists() else self._send(b"", "text/plain", 404)
                else:
                    self._send(b"", "text/plain", 404)
            except Exception as e:
                self._send(str(e), "text/plain", 500)

        def do_POST(self):
            p = self.path.split("?")[0]
            try:
                if p.startswith("/accept/"):
                    self._send(json.dumps(mon.accept(int(p.split("/")[2]))))
                else:
                    self._send(b"", "text/plain", 404)
            except Exception as e:
                self._send(json.dumps({"ok": False, "msg": str(e)}), "application/json", 500)

    srv = None
    for pt in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", pt), H)
            break
        except OSError:
            continue
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


HTML = r"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Cascade Reviewer · sala de control</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#06080f;--p:#0e1423;--p2:#151d33;--t:#e8eefc;--d:#8795b5;--red:#ff3b5c;--blue:#3aa0ff;--cy:#38d2f0;--pu:#b86bff;--am:#ffb020;--gr:#2ee59d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:13px/1.4 system-ui,Segoe UI,sans-serif;height:100vh;overflow:hidden}
#app{display:grid;grid-template-columns:1fr 460px;height:100vh}
#left{overflow:auto;background:radial-gradient(1200px 600px at 30% -10%,#12204a55,transparent),#05070c;position:relative}
#right{overflow:auto;background:linear-gradient(180deg,#0e1424,#0a0f1c);border-left:1px solid #1d2744;padding:14px}
.tb{position:sticky;top:0;z-index:30;background:#05070ceb;backdrop-filter:blur(8px);padding:8px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #151d33}
.tb b.logo{letter-spacing:.2em;background:linear-gradient(90deg,var(--cy),var(--pu),var(--red));-webkit-background-clip:text;color:transparent;font-size:15px}
.tb button{background:var(--p2);color:var(--t);border:1px solid #28345a;border-radius:7px;padding:4px 11px;cursor:pointer}.tb button.on{border-color:var(--cy);box-shadow:0 0 10px #38d2f055}
.tb button:disabled{opacity:.35;cursor:default}
.lg{display:flex;gap:10px;font-size:11px;color:var(--d);margin-left:auto}.lg i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:-1px}
#pages{position:relative;padding:14px 270px 40px 16px}#pl{position:relative}
.page{position:relative;margin:0 auto 18px;width:var(--pw,760px);box-shadow:0 6px 34px #000c,0 0 0 1px #ffffff12;background:#fff}
.page img{display:block;width:100%}.ov{position:absolute;inset:0;pointer-events:none;overflow:hidden}.ov>div{position:absolute;inset:0}
#fx{position:absolute;left:0;top:0;pointer-events:none;overflow:visible;z-index:12}
.w{position:absolute;border-radius:2px;opacity:0}
.w.read{opacity:1;background:#ff3b5c26;box-shadow:inset 0 -1.5px 0 #ff3b5c66}
.w.head{opacity:1;background:#ff3b5c88;box-shadow:0 0 9px 2px #ff3b5ccc;outline:1px solid #ff3b5c}
.w.flag{opacity:1;background:#ff3b5c55;outline:1.5px solid #ff3b5c;box-shadow:0 0 12px 3px #ff3b5caa;animation:pulse 1.1s ease-in-out infinite;mix-blend-mode:multiply}
@keyframes pulse{50%{box-shadow:0 0 20px 6px #ff3b5cff;background:#ff3b5c77}}
.sb{position:absolute;border-radius:3px;mix-blend-mode:multiply}
.sb.op{background:#ff3b5c33;outline:1px solid #ff3b5c99}.sb.ap{background:#2ee59d55;outline:1.5px solid #14b878}
.sb.sg{background:#ffb02055;outline:1.5px dashed #e69a00}.sb.nt{background:#3aa0ff22;outline:1px dotted #3aa0ff99}.sb.ev{background:#b86bff33;outline:1px dashed #b86bff}
.sb.flash{animation:flash 1.4s ease-out 2}@keyframes flash{0%{box-shadow:0 0 0 0 #2ee59dff}100%{box-shadow:0 0 0 16px #2ee59d00}}
.band{position:absolute;left:0;right:0;background:linear-gradient(90deg,#38d2f010,#38d2f01f);border-top:1px solid #38d2f055;border-bottom:1px solid #38d2f055}
.band.deep{background:linear-gradient(90deg,#b86bff10,#b86bff22);border-color:#b86bff66}
.arc{fill:none;stroke-width:2;stroke-dasharray:7 7;animation:dash .9s linear infinite;filter:url(#glow)}@keyframes dash{to{stroke-dashoffset:-14}}
.arc.draw{stroke-dasharray:var(--len);stroke-dashoffset:var(--len);animation:draw .9s ease-out forwards,dashrun .9s linear .9s infinite}
@keyframes draw{to{stroke-dashoffset:0}}@keyframes dashrun{to{stroke-dasharray:7 7}}
.chip text{font:600 10.5px system-ui;fill:#fff}.chip rect{rx:8}
.card text{font:11px system-ui;fill:#e8eefc}
h1{font-size:14px;margin:0 0 2px;letter-spacing:.14em}h2{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--d);margin:16px 0 6px;display:flex;align-items:center;gap:8px}
h2:after{content:"";flex:1;height:1px;background:linear-gradient(90deg,#28345a,transparent)}
.hero{display:flex;gap:14px;align-items:center}.ring{--p:0;width:92px;height:92px;border-radius:50%;background:conic-gradient(var(--cy) calc(var(--p)*1%),#18213b 0);display:grid;place-items:center;position:relative;flex:none}
.ring:before{content:"";position:absolute;inset:8px;border-radius:50%;background:#0b1120}.ring span{position:relative;font-weight:700;font-size:15px;text-align:center;line-height:1.1}.ring small{display:block;font-size:9px;color:var(--d);font-weight:400}
.kp{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:6px}.k{background:var(--p2);border-radius:9px;padding:6px 9px}.k b{display:block;font-size:17px}.k span{font-size:10px;color:var(--d)}
.st{display:grid;grid-template-columns:92px 1fr 46px;gap:8px;align-items:center;margin:3px 0;font-size:11.5px}.st .b{height:7px;background:#0a1020;border-radius:6px;overflow:hidden}.st .b i{display:block;height:100%;background:var(--gr);transition:width .5s}
.st.running{color:var(--am)}.st.running .b i{background:linear-gradient(90deg,var(--am),var(--red));animation:glowb 1.2s infinite alternate}.st.waiting{color:var(--d)}@keyframes glowb{to{filter:brightness(1.6)}}
.now{background:var(--p2);border-radius:12px;padding:10px;border:1px solid #28345a;position:relative;overflow:hidden}.now.fast{border-left:4px solid var(--cy)}.now.deep{border-left:4px solid var(--pu)}
.now:after{content:"";position:absolute;left:0;right:0;top:0;height:2px;background:linear-gradient(90deg,transparent,var(--cy),transparent);animation:sweep 2s linear infinite}
@keyframes sweep{from{transform:translateX(-100%)}to{transform:translateX(100%)}}
.ph{display:inline-block;padding:1px 8px;border-radius:9px;font-size:10px;font-weight:700;letter-spacing:.06em;margin-left:6px}.ph.prefill{background:#ff3b5c33;color:#ff8aa0}.ph.generate{background:#2ee59d33;color:#7dffc8}.ph.think{background:#ffb02033;color:#ffd27a}
.mini{height:5px;background:#0a1020;border-radius:4px;margin:7px 0;overflow:hidden}.mini i{display:block;height:100%;background:linear-gradient(90deg,var(--red),var(--am));transition:width .2s linear}.mini i.ind{width:35%!important;animation:ind 1.1s ease-in-out infinite alternate}@keyframes ind{from{margin-left:0}to{margin-left:65%}}.wait{color:var(--am);font-size:11px;margin:2px 0}
.live{font:11px/1.35 ui-monospace,Consolas,monospace;color:#b9c6e4;white-space:pre-wrap;max-height:96px;overflow:hidden;opacity:.92}
.rel{display:flex;gap:6px;align-items:flex-start;background:#0b1224;border-radius:8px;padding:5px 7px;margin:4px 0;font-size:11px;border-left:3px solid var(--blue)}.rel.evidence{border-color:var(--pu)}.rel.hot{background:#1a2342;box-shadow:0 0 0 1px var(--red)}
.rel b{font-size:10px;color:var(--d);text-transform:uppercase;min-width:54px}.rel q{color:var(--d)}
.ch{background:var(--p2);border-radius:10px;padding:8px 10px;margin:6px 0;border-left:4px solid var(--gr);font-size:12px}.ch.rev{border-color:var(--am)}.ch.sug{border-color:var(--am);border-left-style:dashed}
.ch .df{margin:4px 0;line-height:1.45}del{background:#ff3b5c25;color:#ff9db0;text-decoration:line-through}ins{background:#2ee59d22;color:#8bffd0;text-decoration:none}
.tg{display:inline-block;font-size:9.5px;padding:0 6px;border-radius:7px;background:#232f52;color:#aebde0;margin:1px 3px 1px 0}.tg.ok{background:#12432f;color:#7dffc8}.tg.warn{background:#4a3410;color:#ffd27a}
.why{color:var(--d);font-size:11px}.btn{background:linear-gradient(90deg,#ffb020,#ff7a1a);color:#1a1000;border:0;border-radius:7px;padding:3px 10px;font-weight:700;cursor:pointer;margin-top:4px}
.bars{display:grid;grid-template-columns:90px 1fr 38px;gap:6px;font-size:11px;align-items:center;margin:2px 0}.bars .b{height:7px;background:#0a1020;border-radius:6px;overflow:hidden}.bars .b i{display:block;height:100%;background:linear-gradient(90deg,var(--cy),var(--pu))}
.row{display:flex;justify-content:space-between;color:var(--d);font-size:11.5px}.log{font:11px ui-monospace,Consolas,monospace;color:var(--d)}.log b{color:var(--t)}
svg text{font-family:system-ui}
</style></head><body><div id="app">
<div id="left"><div class="tb"><b class="logo">CASCADE</b>
 <button id="v_before" class="on" onclick="setView('before')">Original</button><button id="v_after" onclick="setView('after')" disabled>Reescrito</button><button id="v_redline" onclick="setView('redline')" disabled>Cambios (rojo/azul)</button>
 <label style="color:var(--d)">zoom <input type="range" id="zoom" min="480" max="1100" value="760"></label>
 <label style="color:var(--d)"><input type="checkbox" id="follow" checked> seguir al agente</label>
 <span id="nopdf" style="color:var(--am)"></span>
 <div class="lg"><span><i style="background:var(--red)"></i>leyendo / analizando</span><span><i style="background:var(--blue)"></i>comparado con</span><span><i style="background:var(--pu)"></i>evidencia (log)</span><span><i style="background:var(--gr)"></i>reescrito</span><span><i style="background:var(--am)"></i>sugerencia</span></div></div>
 <div id="pages"><div id="pl"></div><svg id="fx"><defs><filter id="glow"><feGaussianBlur stdDeviation="2.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
 <marker id="ar" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#fff"/></marker></defs><g id="fxg"></g></svg></div></div>
<div id="right">
 <div class="hero"><div class="ring" id="ring"><span id="rt">--<small>ETA</small></span></div>
  <div class="kp"><div class="k"><b id="el">--</b><span>transcurrido</span></div><div class="k"><b id="etw">--</b><span>peor caso</span></div><div class="k"><b id="tps">--</b><span>tok/s</span></div><div class="k"><b id="ram">--</b><span>RAM GB</span></div></div></div>
 <div style="margin-top:8px"><h1 id="title">Cascade</h1></div>
 <h2>Pipeline</h2><div id="stages"></div>
 <h2>Ahora mismo</h2><div id="now" class="now">en espera</div>
 <h2>Mapa de atención del paper</h2><svg id="mm" width="432" height="92" style="background:#0b1120;border-radius:10px"></svg>
 <h2>Cascada de modelos (tiempo)</h2><svg id="gt" width="432" height="70" style="background:#0b1120;border-radius:10px"></svg>
 <h2>Cambios hechos al paper <span id="nch" class="tg ok"></span></h2><div id="changes"></div>
 <h2>Sugerencias para ti <span id="nsg" class="tg warn"></span></h2><div id="sugs"></div>
 <h2>Evaluación heurística</h2><div id="panel" style="color:var(--d)">pendiente</div>
 <h2>Cascada · estadísticas</h2><div id="casc"></div>
 <h2>Torneo de parches</h2><div id="tour" style="color:var(--d)">-</div>
 <h2>Eventos</h2><div id="log" class="log"></div></div></div>
<script>
const $=id=>document.getElementById(id);
let S=null,LAY=null,GEO={},geoV=0,view='before',built='',off=0,curKey='',wordEls=[],headI=0,stKey='',flKey='',arcKey='',selChange=null;
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=s=>{s=Math.max(0,Math.round(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return (h?h+':'+String(m).padStart(2,'0'):m)+':'+String(x).padStart(2,'0')};
const KC={value:'#3aa0ff',concept:'#5ee0ff',evidence:'#b86bff'};
$('zoom').oninput=e=>{document.documentElement.style.setProperty('--pw',e.target.value+'px');arcKey='';setTimeout(sizeFx,50)};
function setView(v){view=v;built='';for(const k of['before','after','redline'])$('v_'+k).classList.toggle('on',k==v);buildPages();stKey='';flKey='';arcKey='';curKey=''}
async function getLayout(){try{LAY=await (await fetch('/layout.json')).json()}catch(e){}}
function D(){return LAY&&LAY.pages[view]}
function buildPages(){
 const ps=D(),box=$('pl');
 if(!ps){box.innerHTML='';$('nopdf').textContent=LAY&&LAY.fitz?'sin PDF aún (usa --compile o --pdf)':'instala PyMuPDF: pip install pymupdf';return}
 $('nopdf').textContent='';if(built===view+ps.length)return;built=view+ps.length;box.innerHTML='';wordEls=[];
 ps.forEach((d,i)=>{const p=document.createElement('div');p.className='page';p.id='pg'+i;p.style.aspectRatio=d[0]+'/'+d[1];
  p.innerHTML='<img src="/page/'+view+'/'+i+'.png"><div class="ov"><div class="L-st"></div><div class="L-wd"></div><div class="L-fl"></div><div class="L-bd"></div></div>';box.appendChild(p)});
 setTimeout(sizeFx,200)}
function sizeFx(){const f=$('fx'),p=$('pages');f.setAttribute('width',p.scrollWidth);f.setAttribute('height',p.scrollHeight);f.style.width=p.scrollWidth+'px';f.style.height=p.scrollHeight+'px'}
function layer(i,n){const el=document.querySelector('#pg'+i+' .L-'+n);return el}
function pct(r,d){return `left:${r[1]/d[0]*100}%;top:${r[2]/d[1]*100}%;width:${(r[3]-r[1])/d[0]*100}%;height:${(r[4]-r[2])/d[1]*100}%`}
function absPos(r){const ps=D();if(!ps)return null;const el=document.getElementById('pg'+r[0]);if(!el)return null;const d=ps[r[0]];
 const W=el.offsetWidth,H=el.offsetHeight;return{x:el.offsetLeft+r[1]/d[0]*W,y:el.offsetTop+r[2]/d[1]*H,w:(r[3]-r[1])/d[0]*W,h:(r[4]-r[2])/d[1]*H,right:el.offsetLeft+W}}
function ctr(rects){if(!rects||!rects.length)return null;const a=absPos(rects[0]);if(!a)return null;const b=absPos(rects[rects.length-1])||a;return{x:a.x,y:a.y+a.h/2,w:a.w,right:a.right,y2:b.y+b.h/2}}
function clearLayer(n){document.querySelectorAll('.L-'+n).forEach(e=>e.innerHTML='')}
// ---------- static overlays: issues / changes ----------
function statics(s){
 const key=view+geoV+JSON.stringify(s.issues.map(i=>[i.id,i.state]))+s.changes.length+selChange;if(key===stKey||!D())return;stKey=key;clearLayer('st');const ps=D();
 const add=(r,cls,title)=>{const l=layer(r[0],'st');if(!l)return;const e=document.createElement('div');e.className='sb '+cls;e.style.cssText=pct(r,ps[r[0]]);e.title=title||'';l.appendChild(e)};
 if(view==='before')s.issues.forEach(i=>{const g=GEO['i'+i.id];if(!g||i.state==='refuted')return;
  const cls=(i.state==='applied'||i.state==='accepted-by-user')?'ap':i.state==='suggestion'?'sg':i.type==='evidence'?'ev':(i.state==='note'||i.state==='addressed')?'nt':'op';
  g.forEach(r=>add(r,cls+(selChange===i.id?' flash':''),i.text))});
 if(view==='after')s.changes.forEach(c=>{const g=GEO['pa'+c.id];if(g)g.forEach(r=>add(r,'ap'+(selChange===c.issue?' flash':''),c.why))})}
// ---------- word-level reading sweep (60 fps) ----------
function buildWords(W){const ps=D();wordEls=[];W.forEach(r=>{const l=layer(r[0],'wd');if(!l){wordEls.push(null);return}const e=document.createElement('div');e.className='w';e.style.cssText=pct(r,ps[r[0]]);l.appendChild(e);wordEls.push(e)})}
function frame(){
 const n=S&&S.now;
 if(n&&view==='before'&&D()&&n.chunk){const W=GEO['W:'+n.chunk];const key=n.tid+'|'+n.chunk+'|'+n.since;
  if(key!==curKey){clearLayer('wd');curKey=key;wordEls=[];headI=0;if(W)buildWords(W)}
  if(wordEls.length){const t=Date.now()/1000-off-n.since;const pre=n.phase==='prefill';const p=pre?Math.min(.985,t/Math.max(.8,n.pre_eta||2)):1;const h=Math.floor(p*wordEls.length);
   for(let i=headI;i<h;i++){const e=wordEls[i];if(e)e.className='w read'}
   for(let i=Math.max(0,headI-10);i<headI;i++){const e=wordEls[i];if(e)e.className='w read'}
   if(pre)for(let i=Math.max(0,h-9);i<h;i++){const e=wordEls[i];if(e)e.className='w head'}
   headI=Math.max(headI,h)}}
 else if(curKey&&!n){/* keep last trail briefly */ if(!frame.t)frame.t=Date.now();if(Date.now()-frame.t>2500){clearLayer('wd');curKey='';wordEls=[];frame.t=0}}
 requestAnimationFrame(frame)}
// ---------- flags (words the model cites) + arcs (what it is compared with) ----------
function dynamic(s){
 const n=s.now,ps=D();if(!ps)return;
 const cites=(n&&n.cites)||[];
 const fk=view+geoV+JSON.stringify(cites)+(n?n.tid:'');
 if(fk!==flKey){flKey=fk;clearLayer('fl');
  if(view==='before')cites.forEach(c=>{const g=GEO[c.k];if(!g)return;g.forEach(r=>{const l=layer(r[0],'fl');if(!l)return;const e=document.createElement('div');e.className='w flag';e.style.cssText=pct(r,ps[r[0]]);l.appendChild(e)})});
  clearLayer('bd');if(view==='before'&&n&&n.start){const a=n.start,b=n.end||n.start;for(let p=a.page;p<=b.page;p++){const d=ps[p],l=layer(p,'bd');if(!l)continue;
   const y0=p==a.page?a.y0:0,y1=p==b.page?b.y1:d[1];const e=document.createElement('div');e.className='band'+(n.model==s.deep&&s.fast!=s.deep?' deep':'');e.style.cssText=`top:${y0/d[1]*100}%;height:${Math.max(1,(y1-y0)/d[1]*100)}%`;l.appendChild(e)}
   if($('follow').checked&&curKey!==frame.f){frame.f=curKey;const el=$('pg'+a.page);if(el)$('left').scrollTo({top:el.offsetTop+(a.y0/ps[a.page][1])*el.offsetHeight-140,behavior:'smooth'})}}}
 const rels=n&&view==='before'?(n.rels||[]):[];
 const ak=view+geoV+JSON.stringify([rels,cites.map(c=>[c.k,c.c])])+selChange;if(ak===arcKey)return;arcKey=ak;
 let g='';let cardY=-999;
 const arc=(A,B,color,label,kind,card)=>{
  if(!A)return;const bulge=A.right+34+Math.min(120,Math.abs((B?B.y:A.y)-A.y)*.12);
  const ax=A.x+A.w,ay=A.y;
  if(card){const cx=A.right+22,cy=Math.max(ay-14,cardY+40);cardY=cy;const t=(card.slice(0,46)),t2=card.slice(46,92);
   g+=`<path class="arc" d="M${ax},${ay} C${A.right+10},${ay} ${cx-18},${cy+16} ${cx},${cy+16}" stroke="${color}" marker-end="url(#ar)"/>`+
   `<g class="card"><rect x="${cx}" y="${cy}" width="236" height="${t2?44:30}" rx="9" fill="#1a1030" stroke="${color}" stroke-width="1.5"/><text x="${cx+8}" y="${cy+13}" font-weight="700" fill="${color}" style="fill:${color}">LOG · ${esc(label)}</text><text x="${cx+8}" y="${cy+26}">${esc(t)}</text>${t2?`<text x="${cx+8}" y="${cy+38}">${esc(t2)}</text>`:''}</g>`;return}
  if(!B)return;const bx=B.x+B.w,by=B.y;const d=`M${ax},${ay} C${bulge},${ay} ${bulge},${by} ${bx},${by}`;
  g+=`<path class="arc" d="${d}" stroke="${color}" marker-end="url(#ar)"/>`;
  const mx=bulge-6,my=(ay+by)/2,w=label.length*5.9+16;
  g+=`<g class="chip"><rect x="${mx-w/2}" y="${my-9}" width="${w}" height="18" fill="${color}" opacity=".95"/><text x="${mx-w/2+8}" y="${my+4}">${esc(label)}</text></g>`};
 rels.forEach(id=>{const r=s.relations[id];if(!r)return;const A=ctr(GEO['ra'+id]);const col=KC[r.kind]||'#3aa0ff';const lab=r.kind+': '+r.label;
  if(r.log)arc(A,null,col,r.label,r.kind,r.b_text);else arc(A,ctr(GEO['rb'+id]),col,lab,r.kind)});
 cites.forEach(c=>{if(c.c==null)return;const r=s.relations[c.c];if(!r)return;const A=ctr(GEO[c.k]);
  if(r.log)arc(A,null,'#ff3b5c',r.label,'evidence','⟷ '+r.b_text);else arc(A,ctr(GEO['rb'+r.id]),'#ff3b5c','⚑ cita ⟷ '+r.kind,r.kind)});
 $('fxg').innerHTML=g;sizeFx()}
// ---------- panels ----------
function minimap(s){
 const ids=Object.keys(s.chunks);const n=ids.length||1;const W=432,cw=(W-16)/n;const col={pending:'#26325a',mapped:'#1f6f8f',scanned:'#38d2f0',attacked:'#b86bff',rewritten:'#2ee59d'};
 let g='';const x=i=>8+i*cw+cw/2;const idx={};ids.forEach((id,i)=>idx[id]=i);const act=s.now&&s.now.chunk;
 s.relations.forEach(r=>{if(idx[r.chunk]==null)return;const hot=act===r.chunk;if(r.log){g+=`<circle cx="${x(idx[r.chunk])}" cy="12" r="2.6" fill="#b86bff" opacity="${hot?1:.5}"/>`;return}
  if(idx[r.b_chunk]==null)return;const a=x(idx[r.chunk]),b=x(idx[r.b_chunk]),h=10+Math.abs(a-b)*.22;g+=`<path d="M${a},50 Q${(a+b)/2},${50-h*1.6} ${b},50" fill="none" stroke="${hot?'#5ee0ff':'#3aa0ff'}" stroke-width="${hot?1.8:.8}" opacity="${hot?.95:.28}"/>`});
 ids.forEach((id,i)=>{const c=s.chunks[id];const h=8+c.risk*30;const y=84-h;const hot=id===act;
  g+=`<rect x="${8+i*cw+1}" y="${y}" width="${Math.max(3,cw-3)}" height="${h}" rx="3" fill="${col[c.state]||col.pending}" ${hot?'stroke="#fff" stroke-width="1.6"':''}><title>${esc(id)} ${esc(c.title)} · riesgo ${c.risk} · ${c.state}${c.changes?' · '+c.changes+' cambios':''}</title></rect>`;
  if(c.changes)g+=`<text x="${x(i)}" y="${y-3}" font-size="9" fill="#2ee59d" text-anchor="middle">✎${c.changes}</text>`});
 $('mm').innerHTML=g}
function gantt(s){const W=432,tot=Math.max(30,s.elapsed);let g='';const lane=m=>m===s.fast?0:(m===s.deep?1:2);const colr=['#38d2f0','#b86bff','#2ee59d'];
 ['FAST','DEEP','DET'].forEach((l,i)=>g+=`<text x="4" y="${18+i*20}" font-size="8" fill="#6d7ba0">${l}</text>`);
 s.timeline.forEach(t=>{const e=t.e==null?s.elapsed:t.e;const x=26+t.s/tot*(W-32),w=Math.max(2,(e-t.s)/tot*(W-32));const l=lane(t.model);
  g+=`<rect x="${x}" y="${8+l*20}" width="${w}" height="12" rx="3" fill="${colr[l]}" opacity="${t.e==null?1:.75}">${t.e==null?'<animate attributeName="opacity" values="1;.4;1" dur="1s" repeatCount="indefinite"/>':''}<title>${esc(t.stage)} · ${esc(t.label)}</title></rect>`});
 $('gt').innerHTML=g}
function tags(sg,tr){let h='';for(const[k,v]of Object.entries(sg||{}))if(v)h+=`<span class="tg ok">${esc(k)}</span>`;if(tr&&tr.length)h+=`<span class="tg ok">torneo A/B:${esc(tr[0])} B/A:${esc(tr[1])}</span>`;return h}
function render(s){
 $('title').textContent=s.title;$('el').textContent=fmt(s.elapsed);$('etw').textContent=s.finished?'-':fmt(s.eta_worst);$('tps').textContent=(s.tps||0).toFixed(1);$('ram').textContent=s.ram?s.ram[0].toFixed(1)+'/'+s.ram[1].toFixed(0):'n/a';
 $('ring').style.setProperty('--p',(s.finished?100:s.progress*100).toFixed(1));$('rt').innerHTML=(s.finished?'listo':fmt(s.eta_likely))+'<small>'+(s.finished?'':'ETA ')+Math.round(s.progress*100)+'%</small>';
 $('stages').innerHTML=s.stages.map(x=>{const f=x.total?x.done/x.total:(x.state=='done'?1:0);return `<div class="st ${x.state}"><span>${x.name}</span><div class="b"><i style="width:${f*100}%"></i></div><span>${x.total?x.done+'/'+x.total:(x.state=='done'?'✓':'')}</span></div>`}).join('');
 const n=s.now;
 if(n){const deep=n.model==s.deep&&s.fast!=s.deep;const t=Date.now()/1000-off-n.since;const pre=n.phase==='prefill';const p=pre?Math.min(98,t/Math.max(.8,n.pre_eta||2)*100):100;const tp=Date.now()/1000-off-(n.t_phase||n.since);const waiting=(n.phase==='prefill'&&t>(n.pre_eta||2)*1.05)||n.phase==='think';
  const cites=n.cites||[];const hot=new Set(cites.map(c=>c.c));
  const rl=(n.rels||[]).map(id=>s.relations[id]).filter(Boolean).map(r=>`<div class="rel ${r.kind} ${hot.has(r.id)?'hot':''}" style="border-color:${KC[r.kind]}"><b>${r.kind}</b><div><q>“${esc(r.a_text.slice(0,70))}”</q><br>⟷ <span style="color:${KC[r.kind]}">${esc(r.b_title)}</span> “${esc(r.b_text.slice(0,90))}” <span class="tg">${esc(r.label)}</span></div></div>`).join('');
  $('now').className='now '+(deep?'deep':'fast');
  $('now').innerHTML=`<span class="tg" style="background:${deep?'#4a2a66':'#0e4a57'}">${esc(n.model)}</span><b>${esc(n.agent)}</b><span class="ph ${n.phase}">${n.phase==='prefill'?'LEYENDO':n.phase==='think'?'PENSANDO':'GENERANDO'}</span><div style="margin-top:4px">${esc(n.label)}</div>
   <div class="mini"><i class="${waiting?'ind':''}" style="width:${p}%"></i></div>${waiting?`<div class="wait">⏳ esperando el primer token del modelo… ${Math.round(tp)} s (${n.phase==='think'?'está pensando':'procesando el prompt'}; normal en CPU)</div>`:''}<div class="live">${esc(s.live)}</div>${rl?'<div style="margin-top:6px;color:var(--d);font-size:10px;letter-spacing:.1em">COMPARANDO CON</div>'+rl:''}`}
 else{$('now').className='now';$('now').innerHTML=s.finished?'finalizado ✓':'paso determinista / en espera'}
 minimap(s);gantt(s);
 const ch=s.changes.filter(c=>c.status==='applied'||c.status==='accepted-by-user');$('nch').textContent=ch.length;
 $('changes').innerHTML=ch.slice().reverse().slice(0,12).map(c=>`<div class="ch ${c.review?'rev':''}" onclick="selChange=${c.issue};stKey='';focusIssue(${c.issue})" style="cursor:pointer"><b>#${c.id}</b> · ${esc(c.chunk)} · ${esc(c.type)} · ${esc(c.severity)} ${c.review?'<span class="tg warn">⚠ revisar</span>':''}
  <div class="df"><del>${esc(c.old.slice(0,200))}</del><br><ins>${esc(c.new.slice(0,200))}</ins></div><div class="why">${esc(c.why)}</div>
  ${c.compared?`<div class="why">⟷ comparado con <b>${esc(c.compared.where)}</b> (${esc(c.compared.relation||c.compared.kind)}): “${esc(c.compared.text.slice(0,110))}”</div>`:''}<div>${tags(c.signals,c.tournament)}</div></div>`).join('')||'<div class="why">aún ninguno</div>';
 const sg=s.suggestions.filter(x=>x.state==='open');$('nsg').textContent=sg.length;
 $('sugs').innerHTML=sg.slice(0,10).map(x=>`<div class="ch sug"><b>#${x.id}</b> · ${esc(x.chunk)} · ${esc(x.type)} · ${esc(x.severity)}<div class="df"><del>${esc(x.old.slice(0,160))}</del><br><ins>${esc(x.new.slice(0,160))}</ins></div><div class="why">${esc(x.why)} — <i>no aplicado: ${esc(x.reason)}</i></div>${s.mode==='pdf'?'<div class="why">modo PDF: edita tu original a mano</div>':'<button class="btn" onclick="acceptSug('+x.id+',this)">Aplicar al paper</button>'}</div>`).join('')||'<div class="why">ninguna</div>';
 const P=s.panel,dims=['soundness','contribution','clarity','evidence_support','overclaim_risk'];
 if(P.before||P.after)$('panel').innerHTML=dims.map(d=>`<div class="bars"><span>${d.replace('_',' ')}</span><div class="b"><i style="width:${((P.after||P.before)[d]||0)*10}%"></i></div><span>${(P.before||{})[d]??'-'}→${(P.after||{})[d]??'-'}</span></div>`).join('')+'<div class="why">autoevaluación del modelo: sirve para priorizar, no predice aceptación</div>';
 const c=s.cnt;$('casc').innerHTML=`<div class="row"><span>rápido (screener)</span><span>${esc(s.fast)}</span></div><div class="row"><span>profundo (adjudicador)</span><span>${esc(s.deep)}</span></div><div class="row"><span>capítulos escalados</span><span>${c.escalated}</span></div><div class="row"><span>críticas inventadas eliminadas</span><span>${c.filtered}</span></div><div class="row"><span>números sin respaldo en el log</span><span>${c.unbound}</span></div><div class="row"><span>reescrituras · swaps · cache</span><span>${c.rewritten} · ${c.swaps} · ${c.cache}</span></div>`;
 const T=s.tourn;$('tour').innerHTML=T.length?`${T.filter(t=>t.won).length}/${T.length} parches ganaron en ambos órdenes`:'-';
 $('log').innerHTML=s.log.slice(-9).map(l=>`<div>${l.t} <b>${esc(l.kind)}</b> ${esc(l.msg)}</div>`).join('');
 $('v_after').disabled=!s.pdf.after;$('v_redline').disabled=!s.pdf.redline}
function focusIssue(id){const g=GEO['i'+id];if(!g||!g.length||!D())return;setView('before');setTimeout(()=>{const r=g[0],el=$('pg'+r[0]);if(el)$('left').scrollTo({top:el.offsetTop+(r[2]/D()[r[0]][1])*el.offsetHeight-160,behavior:'smooth'})},120)}
async function acceptSug(id,btn){btn.disabled=true;btn.textContent='aplicando…';try{const r=await (await fetch('/accept/'+id,{method:'POST'})).json();btn.textContent=r.ok?'✓ aplicado':'✗ '+r.msg}catch(e){btn.textContent='error'}}
async function poll(){try{const s=await (await fetch('/status.json')).json();S=s;off=Date.now()/1000-s.server_time;
 if(s.geo_ver!==geoV){const g=await (await fetch('/geo.json?since='+geoV)).json();Object.assign(GEO,g.items);geoV=g.ver}
 if(!LAY||!LAY.pages[view]||(s.pdf.after&&!LAY.pages.after)||(s.pdf.redline&&!LAY.pages.redline)){await getLayout();buildPages()}
 render(s);statics(s);dynamic(s)}catch(e){console.error(e)}setTimeout(poll,550)}
getLayout().then(()=>{buildPages();poll();requestAnimationFrame(frame)});
window.addEventListener('resize',()=>{arcKey='';setTimeout(sizeFx,80)});
</script></body></html>"""
