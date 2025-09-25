# webapp.py
import os
from decimal import Decimal
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, conlist
from typing import Optional, Dict, Any, Literal, List, Tuple

from core.params import (
    load_overrides, upsert_params, get_paused, set_paused, ensure_schema,
    list_pairs, upsert_pairs
)
from core.reporting import (
    get_settings as get_report_settings,
    set_settings as set_report_settings,
    send_report as send_report_now
)
from core.telemetry import send_event
from config import ADMIN_TOKEN as CONF_ADMIN_TOKEN

app = FastAPI(title="CEX Trading Bot API", version="2.4.1")

# ========== Admin token handling ==========
ADMIN_TOKEN = (CONF_ADMIN_TOKEN or os.getenv("ADMIN_TOKEN", "")).strip()

def require_admin(request: Request):
    if not ADMIN_TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1].strip()
    if not token:
        token = request.query_params.get("token") or request.cookies.get("admintoken")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

# ========== Pydantic models ==========
GapMode = Literal["off", "down_only", "symmetric"]

class ParamsUpdate(BaseModel):
    PAIR: Optional[str] = Field(None, pattern=r"^[A-Z0-9]+_[A-Z0-9]+$")
    DEVIATION_PCT: Optional[float] = Field(None, ge=0, le=100)
    QUOTE: Optional[float] = Field(None, ge=0)
    LOT_SIZE_BASE: Optional[float] = Field(None, ge=0)
    GAP_MODE: Optional[GapMode] = None
    GAP_SWITCH_PCT: Optional[float] = Field(None, ge=0, le=100)

class PairItem(BaseModel):
    pair: str = Field(..., pattern=r"^[A-Z0-9]+_[A-Z0-9]+$")
    deviation_pct: float = Field(..., ge=0, le=100)
    quote: float = Field(..., ge=0)
    lot_size_base: float = Field(..., ge=0)
    gap_mode: GapMode = Field(default="down_only")
    gap_switch_pct: float = Field(..., ge=0, le=100)
    enabled: bool = True

class PairsBody(BaseModel):
    pairs: conlist(PairItem, min_length=0, max_length=5)

class PauseReq(BaseModel):
    paused: bool

class ReportingBody(BaseModel):
    enabled: bool
    period_min: int = Field(..., description="Один из {1,5,10,15,30,60}")

# ========== Startup ==========
@app.on_event("startup")
def _startup():
    ensure_schema()

# ========== Helpers for diffs ==========
def _norm_dec(v: Any) -> str:
    try:
        return str(Decimal(str(v)))
    except Exception:
        return str(v)

def _pair_to_view(p) -> Dict[str, str]:
    return {
        "idx": str(p.get("idx", "")),
        # v0.7.2: показываем биржу (дефолт 'gate', чтобы не ломать старые записи)
        "exchange": str(p.get("exchange", "gate")),
        "pair": str(p.get("pair", "")),
        "deviation_pct": _norm_dec(p.get("deviation_pct", "")),
        "quote": _norm_dec(p.get("quote", "")),
        "lot_size_base": _norm_dec(p.get("lot_size_base", "")),
        "gap_mode": str(p.get("gap_mode", "")),
        "gap_switch_pct": _norm_dec(p.get("gap_switch_pct", "")),
        "enabled": "true" if p.get("enabled", True) else "false",
    }

def _pairs_map(arr: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    m: Dict[str, Dict[str, str]] = {}
    for x in arr:
        v = _pair_to_view(x)
        if v["pair"]:
            m[v["pair"]] = v
    return m

def _diff_pairs(old: Dict[str, Dict[str, str]], new: Dict[str, Dict[str, str]]) -> Tuple[List[str], List[str], List[str]]:
    added, removed, changed = [], [], []
    keys = set(old.keys()) | set(new.keys())
    fields = ["deviation_pct", "quote", "lot_size_base", "gap_mode", "gap_switch_pct", "enabled"]
    for k in sorted(keys):
        if k not in old:
            v = new.get(k, {})
            added.append(f"+ <code>{k}</code> DEV={v.get('deviation_pct','')} QUOTE={v.get('quote','')} LOT={v.get('lot_size_base','')} {v.get('gap_mode','')}/{v.get('gap_switch_pct','')} EN={v.get('enabled','')}")
        elif k not in new:
            v = old.get(k, {})
            removed.append(f"− <code>{k}</code> (была: DEV={v.get('deviation_pct','')} QUOTE={v.get('quote','')} LOT={v.get('lot_size_base','')} {v.get('gap_mode','')}/{v.get('gap_switch_pct','')} EN={v.get('enabled','')})")
        else:
            o, n = old[k], new[k]
            diffs = []
            for f in fields:
                if o.get(f) != n.get(f):
                    diffs.append(f"{f.upper()} {o.get(f)}→{n.get(f)}")
            if diffs:
                changed.append(f"• <code>{k}</code>: " + "; ".join(diffs))
    return added, removed, changed

def _diff_params(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    out = []
    keys = set(old.keys()) | set(new.keys())
    for k in sorted(keys):
        ov = None if k not in old else str(old[k])
        nv = None if k not in new else str(new[k])
        if ov != nv:
            if ov is None:
                out.append(f"+ <code>{k}</code>={nv}")
            elif nv is None:
                out.append(f"− <code>{k}</code> (было {ov})")
            else:
                out.append(f"• <code>{k}</code>: {ov}→{nv}")
    return out

# ========== Basic endpoints ==========
@app.get("/", response_class=JSONResponse)
def root():
    return {"status": "ok", "service": "cex-trading-bot", "role": "web", "paused": get_paused()}

@app.get("/status", dependencies=[Depends(require_admin)])
def status():
    p = load_overrides()
    rep_enabled, rep_period = get_report_settings()
    # v0.7.2: добавим состояния по парам (с биржей), не ломая старое поле params
    pairs_view = [_pair_to_view(x) for x in list_pairs(include_disabled=True)]
    return {
        "status": "ok",
        "paused": get_paused(),
        "params": {k: str(v) for k, v in p.items()},
        "reporting": {"enabled": rep_enabled, "period_min": rep_period},
        "pairs": pairs_view,  # <-- добавлено
    }

@app.get("/params", dependencies=[Depends(require_admin)])
def get_params():
    return {k: str(v) for k, v in load_overrides().items()}

@app.put("/params", dependencies=[Depends(require_admin)])
def put_params(body: ParamsUpdate):
    before = load_overrides()
    upd: Dict[str, Any] = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    after = upsert_params(upd)

    # Телеграм-уведомление, только если есть реальные изменения
    diffs = _diff_params(before, after)
    if diffs:
        send_event("params_update", "<b>Изменены глобальные параметры</b>\n" + "\n".join(diffs))

    return {"ok": True, "params": {k: str(v) for k, v in after.items()}}

# ========== Multi-pair endpoints ==========
@app.get("/pairs", dependencies=[Depends(require_admin)])
def get_pairs(include_disabled: bool = True):
    arr = list_pairs(include_disabled=include_disabled)
    return {"ok": True, "pairs": [_pair_to_view(x) for x in arr]}

@app.put("/pairs", dependencies=[Depends(require_admin)])
def put_pairs(body: PairsBody):
    before_arr = list_pairs(include_disabled=True)
    before_map = _pairs_map(before_arr)

    arr = []
    for p in body.pairs:
        arr.append({
            "pair": p.pair.upper(),
            "deviation_pct": Decimal(str(p.deviation_pct)),
            "quote": Decimal(str(p.quote)),
            "lot_size_base": Decimal(str(p.lot_size_base)),
            "gap_mode": p.gap_mode,
            "gap_switch_pct": Decimal(str(p.gap_switch_pct)),
            "enabled": bool(p.enabled),
            # exchange намеренно не пишем/не редактируем в v0.7.2 (только отображение)
        })
    after_arr = upsert_pairs(arr)
    after_map = _pairs_map(after_arr)

    # Телеграм-уведомление об изменениях по парам
    added, removed, changed = _diff_pairs(before_map, after_map)
    if added or removed or changed:
        lines = ["<b>Обновлены торговые пары</b>"]
        if added:
            lines.append("<u>Добавлены</u>:\n" + "\n".join(added))
        if removed:
            lines.append("<u>Удалены</u>:\n" + "\n".join(removed))
        if changed:
            lines.append("<u>Изменены</u>:\n" + "\n".join(changed))
        send_event("pairs_update", "\n\n".join(lines))

    return {"ok": True, "pairs": [_pair_to_view(x) for x in after_arr]}

# ========== Pause control ==========
@app.post("/control/pause", dependencies=[Depends(require_admin)])
def pause(body: PauseReq):
    set_paused(body.paused)
    try:
        if body.paused:
            send_event("paused_on", "Торговый цикл поставлен на паузу админом")
        else:
            send_event("paused_off", "Пауза снята, цикл продолжится со следующей минуты")
    except Exception:
        pass
    return {"ok": True, "paused": get_paused()}

# ========== Reporting control ==========
@app.get("/reporting", dependencies=[Depends(require_admin)])
def get_reporting():
    enabled, period_min = get_report_settings()
    return {"ok": True, "enabled": enabled, "period_min": period_min}

@app.put("/reporting", dependencies=[Depends(require_admin)])
def put_reporting(body: ReportingBody):
    old_enabled, old_period = get_report_settings()
    enabled, period_min = set_report_settings(body.enabled, body.period_min)

    # Телеграм-уведомление при изменении настроек отчётов
    diffs = []
    if enabled != old_enabled:
        diffs.append(f"ENABLED {str(old_enabled).lower()}→{str(enabled).lower()}")
    if period_min != old_period:
        diffs.append(f"PERIOD {old_period}m→{period_min}m")
    if diffs:
        send_event("reporting_update", "<b>Изменены настройки отчётов</b>\n• " + "; ".join(diffs))

    return {"ok": True, "enabled": enabled, "period_min": period_min}

@app.post("/reporting/send", dependencies=[Depends(require_admin)])
def send_reporting_now():
    ok = send_report_now(force=True)
    # уведомим, что вручную отправили отчёт
    send_event("manual_report", "Отчёт отправлен вручную из админки")
    return {"ok": True, "sent": bool(ok)}

# ========== Admin UI ==========
@app.get("/admin", response_class=HTMLResponse)
def admin_ui():
    return HTML_PAGE

# --- HTML (минимальные правки: колонка Exchange; показ row.exchange) ---
HTML_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CEX Trading Bot — Admin</title>
<style>
:root { --bg:#0f172a; --panel:#111827; --muted:#94a3b8; --text:#e5e7eb; --ok:#16a34a; --warn:#d97706; --err:#ef4444; --brand:#22d3ee; }
*{box-sizing:border-box;font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial}
body{margin:0;background:linear-gradient(180deg,#0b1220, #0f172a);color:var(--text)}
.wrap{max-width:980px;margin:40px auto;padding:0 16px}
.card{background:rgba(17,24,39,.8);border:1px solid #1f2937;border-radius:16px;padding:20px;box-shadow:0 10px 30px rgba(0,0,0,.25)}
h1{font-size:22px;margin:0 0 6px 0}
.sub{color:var(--muted);font-size:13px;margin-bottom:16px}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
.sep{flex:1}
.badge{display:inline-flex;align-items:center;gap:8px;font-size:12px;padding:6px 10px;border-radius:999px;border:1px solid #334155;background:#0b1220}
.status-dot{width:8px;height:8px;border-radius:50%}
.dot-ok{background:#22c55e}.dot-paused{background:#f59e0b}.dot-off{background:#6b7280}
button{border:0;border-radius:10px;padding:10px 14px;font-weight:600;cursor:pointer}
.primary{background:linear-gradient(135deg,#06b6d4,#22d3ee);color:#001018}
.ghost{background:#0b1220;color:var(--text);border:1px solid #334155}
.warn{background:#1f2937;color:#fde68a;border:1px solid #6b7280}
.ok{background:#052e1a;color:#bbf7d0;border:1px solid #14532d}
table{width:100%;border-collapse:separate;border-spacing:0 10px}
th,td{padding:8px 10px}
th{color:#9ca3af;font-weight:600;text-align:left}
tr{background:#0b1220;border:1px solid #334155}
tr td{border-top:1px solid #334155;border-bottom:1px solid #334155}
tr td:first-child{border-left:1px solid #334155;border-radius:10px 0 0 10px}
tr td:last-child{border-right:1px solid #334155;border-radius:0 10px 10px 0}
input,select{width:100%;padding:8px 10px;border:1px solid #334155;background:#0b1220;color:#e5e7eb;border-radius:10px;font-size:14px}
.small{font-size:12px;color:#9ca3af}
.toast{position:fixed;right:16px;bottom:16px;background:#0b1220;border:1px solid #334155;color:#e5e7eb;padding:12px 16px;border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.25);display:none}
.section{margin-top:22px;padding-top:12px;border-top:1px dashed #334155}
h2{font-size:16px;margin:0 0 8px 0;color:#e2e8f0}
label{font-size:13px;color:#cbd5e1;margin-right:8px}
.switch{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border:1px solid #334155;border-radius:12px;background:#0b1220}
</style></head><body>
<div class="wrap"><div class="card">
<h1>⚙️ CEX Trading Bot — Admin (мультипары)</h1>
<div class="sub">До 5 пар. Для каждой пары — свои параметры.</div>

<div class="row">
  <span class="badge"><span id="status-dot" class="status-dot dot-off"></span><span id="paused-label">Статус: неизвестно</span></span>
  <span class="sep"></span>
  <button class="ghost" onclick="changeToken()">🔐 Ввести/сменить пароль</button>
</div>

<div style="height:12px"></div>

<table id="pairs"><thead><tr>
  <th>#</th><th>Exchange</th><th>PAIR</th><th>DEV %</th><th>QUOTE</th><th>LOT_BASE</th><th>GAP_MODE</th><th>GAP %</th><th>ENABLED</th>
</tr></thead><tbody></tbody></table>

<div class="small">Подсказка: QUOTE игнорируется, если LOT_BASE &gt; 0</div>

<div class="row" style="margin-top:16px">
  <button class="primary" onclick="save()">💾 Сохранить пары</button>
  <button class="ghost" onclick="reload()">🔄 Обновить</button>
  <button class="ok" onclick="setPause(false)">▶️ Снять паузу</button>
  <button class="warn" onclick="setPause(true)">⏸ Пауза</button>
</div>

<div class="section">
  <h2>📊 Периодические отчёты</h2>
  <div class="row">
    <div class="switch">
      <label for="rep_enabled">Включено</label>
      <select id="rep_enabled">
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    </div>
    <div class="switch">
      <label for="rep_period">Период</label>
      <select id="rep_period">
        <option value="1">1 мин</option>
        <option value="5">5 мин</option>
        <option value="10">10 мин</option>
        <option value="15">15 мин</option>
        <option value="30">30 мин</option>
        <option value="60">60 мин</option>
      </select>
    </div>
    <button class="primary" onclick="saveReporting()">💾 Сохранить</button>
    <button class="ghost" onclick="sendReportNow()">📮 Отправить сейчас</button>
  </div>
  <div class="small">Отчёт уходит в первую минуту после завершения периода (UTC-границы). CSV содержит BUY/SELL окна по вашим правилам.</div>
</div>

</div></div>
<div id="toast" class="toast"></div>
<script>
const TOKEN_KEY='admintoken';
function getToken(){ return localStorage.getItem(TOKEN_KEY) || ''; }
function setToken(t){ localStorage.setItem(TOKEN_KEY, t || ''); }
function changeToken(){ const t = prompt('Введите пароль администратора'); if (t === null) return; setToken(t.trim()); toast('Пароль сохранён локально'); }
function authHeaders(){ const t = getToken(); return t ? {'Authorization':'Bearer '+t} : {}; }
function toast(msg, ok=true){ const t = document.getElementById('toast'); t.textContent = msg; t.style.borderColor = ok ? '#14532d' : '#7f1d1d'; t.style.color = ok ? '#e5e7eb' : '#fecaca'; t.style.display='block'; setTimeout(()=> t.style.display='none', 2200); }
function setBadge(paused){ const dot=document.getElementById('status-dot'); const label=document.getElementById('paused-label');
  if (paused===true){ dot.className='status-dot dot-paused'; label.textContent='Статус: на паузе'; }
  else if (paused===false){ dot.className='status-dot dot-ok'; label.textContent='Статус: работает'; }
  else { dot.className='status-dot dot-off'; label.textContent='Статус: неизвестно'; }
}

function emptyRow(i){ return { idx:i, exchange:'gate', pair:'', deviation_pct:'', quote:'', lot_size_base:'', gap_mode:'down_only', gap_switch_pct:'', enabled:'true' }; }

async function reload(){
  try{
    const s = await fetch('/pairs', { headers: authHeaders() });
    if (s.status===401){ toast('Неверный пароль (401). Нажмите «Ввести/сменить пароль».', false); return; }
    const data = await s.json();
    const tbody = document.querySelector('#pairs tbody'); tbody.innerHTML='';
    let arr = (data.pairs||[]);
    for(let i=1;i<=5;i++){ let row = arr[i-1] || emptyRow(i); addRow(tbody, i, row); }

    const stat = await fetch('/status', { headers: authHeaders() });
    if (stat.ok){
      const js = await stat.json();
      setBadge(js.paused);
      if (js.reporting){
        document.getElementById('rep_enabled').value = js.reporting.enabled ? 'true' : 'false';
        document.getElementById('rep_period').value = String(js.reporting.period_min || 60);
      }
    }
  }catch(e){ toast('Ошибка загрузки', false); }
}

function addRow(tbody, i, row){
  const tr=document.createElement('tr');
  const ex = row.exchange || 'gate';
  tr.innerHTML = `
    <td>${i}</td>
    <td><div class="small">${ex}</div></td>
    <td><input id="PAIR_${i}" placeholder="BTC_USDT" value="${row.pair||''}"/></td>
    <td><input id="DEV_${i}" type="number" step="0.1" min="0" max="100" value="${row.deviation_pct||''}"/></td>
    <td><input id="QUOTE_${i}" type="number" step="0.01" min="0" value="${row.quote||''}"/></td>
    <td><input id="LOT_${i}" type="number" step="0.00000001" min="0" value="${row.lot_size_base||''}"/></td>
    <td>
      <select id="GAPM_${i}">
        <option value="off" ${row.gap_mode==='off'?'selected':''}>off</option>
        <option value="down_only" ${row.gap_mode==='down_only'?'selected':''}>down_only</option>
        <option value="symmetric" ${row.gap_mode==='symmetric'?'selected':''}>symmetric</option>
      </select>
    </td>
    <td><input id="GAPP_${i}" type="number" step="0.1" min="0" max="100" value="${row.gap_switch_pct||''}"/></td>
    <td>
      <select id="EN_${i}">
        <option value="true" ${row.enabled!=='false'?'selected':''}>true</option>
        <option value="false" ${row.enabled==='false'?'selected':''}>false</option>
      </select>
    </td>`;
  tbody.appendChild(tr);
}

async function save(){
  const body={pairs:[]};
  for(let i=1;i<=5;i++){
    const p=document.getElementById('PAIR_'+i).value.trim().toUpperCase();
    const dev=document.getElementById('DEV_'+i).value.trim();
    const q=document.getElementById('QUOTE_'+i).value.trim();
    const lot=document.getElementById('LOT_'+i).value.trim();
    const gm=document.getElementById('GAPM_'+i).value.trim();
    const gp=document.getElementById('GAPP_'+i).value.trim();
    const en=document.getElementById('EN_'+i).value.trim();
    if (!p) continue;
    body.pairs.push({ pair:p, deviation_pct:parseFloat(dev||'0'), quote:parseFloat(q||'0'), lot_size_base:parseFloat(lot||'0'), gap_mode:gm, gap_switch_pct:parseFloat(gp||'0'), enabled:(en==='true') });
  }
  try{
    const res = await fetch('/pairs', { method:'PUT', headers:Object.assign({"Content-Type":"application/json"}, authHeaders()), body:JSON.stringify(body) });
    const data = await res.json();
    if (res.status===401){ toast('Неверный пароль (401).', false); return; }
    if (!res.ok || data.ok===false){ throw new Error(data.detail||JSON.stringify(data)); }
    toast('Пары сохранены'); reload();
  }catch(e){ toast('Ошибка сохранения: '+e.message, false); }
}

async function setPause(flag){
  try{
    const r = await fetch('/control/pause', { method:'POST', headers:Object.assign({"Content-Type":"application/json"}, authHeaders()), body:JSON.stringify({paused:!!flag}) });
    const data = await r.json();
    if (r.status===401){ toast('Неверный пароль (401).', false); return; }
    setBadge(data.paused); toast(flag ? 'Пауза включена' : 'Пауза снята');
  }catch(e){ toast('Ошибка', false); }
}

async function saveReporting(){
  const en = document.getElementById('rep_enabled').value === 'true';
  const pm = parseInt(document.getElementById('rep_period').value || '60', 10);
  try{
    const r = await fetch('/reporting', { method:'PUT', headers:Object.assign({"Content-Type":"application/json"}, authHeaders()), body: JSON.stringify({enabled: en, period_min: pm}) });
    const js = await r.json();
    if (!r.ok || js.ok===false){ throw new Error(js.detail||JSON.stringify(js)); }
    toast('Настройки отчётов сохранены');
  }catch(e){ toast('Не удалось сохранить: '+e.message, false); }
}

async function sendReportNow(){
  try{
    const r = await fetch('/reporting/send', { method:'POST', headers: authHeaders() });
    const js = await r.json();
    if (!r.ok || js.ok===false){ throw new Error(js.detail||JSON.stringify(js)); }
    toast('Отчёт отправлен');
  }catch(e){ toast('Не удалось отправить отчёт: '+e.message, false); }
}

reload();
</script></body></html>
"""
