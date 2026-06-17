"""產生自包含的 HTML 報告。

把掃描結果序列化成 JSON 內嵌於單一 HTML 檔，搭配內嵌 CSS/JS 渲染：
磁碟總覽、可折疊目錄樹、treemap、檔案類型分布、最大檔案、無法存取清單。
產出檔案離線即可開啟，不需任何第三方資源。
"""

from __future__ import annotations

import html
import json
from datetime import datetime

from . import cleanup as cleanup_mod
from .scanner import ScanResult, human_size

# 取頁面層級顯示的設定
_TREEMAP_TOP = 20      # treemap 顯示的頂層目錄數
_EXT_TOP = 25          # 類型分布顯示的副檔名數


def _result_to_payload(
    res: ScanResult,
    min_size: int,
    max_depth: int | None,
    top_n: int,
    flag_cleanup: bool,
) -> dict:
    tree = res.tree.to_dict(min_size=min_size, depth=0, max_depth=max_depth)
    if flag_cleanup:
        cleanup_mod.annotate(tree)

    # 副檔名分布（取最大 _EXT_TOP，其餘合併）
    ext_items = sorted(res.ext_sizes.items(), key=lambda kv: kv[1][0], reverse=True)
    exts = [{"ext": e, "size": s, "count": c} for e, (s, c) in ext_items[:_EXT_TOP]]
    if len(ext_items) > _EXT_TOP:
        rest = ext_items[_EXT_TOP:]
        exts.append({
            "ext": f"（其他 {len(rest)} 種）",
            "size": sum(v[0] for _, v in rest),
            "count": sum(v[1] for _, v in rest),
        })

    return {
        "root": res.root,
        "elapsed": round(res.elapsed, 2),
        "disk_total": res.disk_total,
        "disk_used": res.disk_used,
        "disk_free": res.disk_free,
        "scanned_size": res.tree.size,
        "file_count": res.tree.file_count,
        "tree": tree,
        "exts": exts,
        "largest": [{"size": s, "path": p} for s, p in res.largest_files[:top_n]],
        "inaccessible": res.inaccessible,
    }


def build_report(
    results: list[ScanResult],
    output_path: str,
    min_size: int = 0,
    max_depth: int | None = None,
    top_n: int = 50,
    flag_cleanup: bool = False,
) -> str:
    """產生 HTML 報告並寫入 output_path，回傳 output_path。"""
    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "flag_cleanup": flag_cleanup,
        "drives": [
            _result_to_payload(r, min_size, max_depth, top_n, flag_cleanup)
            for r in results
        ],
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    html_doc = _HTML_TEMPLATE.replace("/*__DATA__*/", data_json)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return output_path


# ---------------------------------------------------------------------------
# HTML 範本：資料以 JSON 注入到 /*__DATA__*/ 佔位處。JS 在 client 端渲染。
# ---------------------------------------------------------------------------
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>磁碟空間分析報告</title>
<style>
  :root { --bg:#0f1419; --panel:#1a2029; --panel2:#222a35; --text:#e6e6e6;
          --muted:#8b97a7; --accent:#4ea1ff; --bar:#2d6cdf; --warn:#e0a83b; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:"Segoe UI","Microsoft JhengHei",system-ui,sans-serif; font-size:14px; }
  header { padding:18px 24px; background:linear-gradient(90deg,#16202c,#0f1419);
           border-bottom:1px solid #2a3340; }
  header h1 { margin:0; font-size:20px; }
  header .meta { color:var(--muted); font-size:12px; margin-top:4px; }
  .tabs { display:flex; gap:6px; padding:10px 24px 0; flex-wrap:wrap; }
  .tab { padding:8px 16px; background:var(--panel); border:1px solid #2a3340;
         border-bottom:none; border-radius:8px 8px 0 0; cursor:pointer; color:var(--muted); }
  .tab.active { background:var(--panel2); color:var(--text); }
  main { padding:0 24px 40px; }
  .drive { display:none; }
  .drive.active { display:block; }
  .cards { display:flex; gap:14px; flex-wrap:wrap; margin:18px 0; }
  .card { background:var(--panel); border:1px solid #2a3340; border-radius:10px;
          padding:14px 18px; min-width:180px; }
  .card .label { color:var(--muted); font-size:12px; }
  .card .value { font-size:22px; font-weight:600; margin-top:4px; }
  .usagebar { height:14px; background:#2a3340; border-radius:7px; overflow:hidden; margin-top:10px; }
  .usagebar > span { display:block; height:100%; background:linear-gradient(90deg,#4ea1ff,#2d6cdf); }
  section { background:var(--panel); border:1px solid #2a3340; border-radius:10px;
            padding:16px 18px; margin:18px 0; }
  section h2 { margin:0 0 12px; font-size:16px; }
  .hint { color:var(--muted); font-size:12px; margin:-6px 0 12px; }
  /* 樹狀 */
  .tree { font-variant-numeric:tabular-nums; }
  .row { display:flex; align-items:center; gap:8px; padding:3px 0;
         border-bottom:1px solid #20262f; }
  .row:hover { background:#20262f; }
  .toggle { width:16px; text-align:center; cursor:pointer; color:var(--accent); user-select:none; }
  .toggle.leaf { color:transparent; cursor:default; }
  .name { flex:0 0 auto; max-width:46%; overflow:hidden; text-overflow:ellipsis;
          white-space:nowrap; }
  .name.dir { color:var(--accent); }
  .name.agg { color:var(--muted); font-style:italic; }
  .minibar { flex:1 1 auto; height:10px; background:#2a3340; border-radius:5px; overflow:hidden; }
  .minibar > span { display:block; height:100%; background:var(--bar); }
  .sz { flex:0 0 90px; text-align:right; color:#cdd6e0; }
  .pct { flex:0 0 52px; text-align:right; color:var(--muted); font-size:12px; }
  .cleanup { color:var(--warn); font-size:11px; border:1px solid var(--warn);
             border-radius:4px; padding:0 5px; white-space:nowrap; }
  .children { margin-left:18px; display:none; }
  .children.open { display:block; }
  /* treemap */
  .treemap { display:flex; flex-wrap:wrap; gap:3px; }
  .tile { background:var(--bar); border-radius:4px; padding:6px 8px; overflow:hidden;
          color:#fff; font-size:12px; min-height:42px; }
  .tile small { display:block; opacity:.8; }
  /* 表格 */
  table { width:100%; border-collapse:collapse; }
  th,td { text-align:left; padding:6px 10px; border-bottom:1px solid #20262f; }
  th { color:var(--muted); font-weight:500; }
  td.num,th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .path { color:#cdd6e0; word-break:break-all; }
  details summary { cursor:pointer; color:var(--muted); }
</style>
</head>
<body>
<header>
  <h1>磁碟空間分析報告</h1>
  <div class="meta" id="meta"></div>
</header>
<div class="tabs" id="tabs"></div>
<main id="main"></main>

<script id="data" type="application/json">/*__DATA__*/</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);

function human(n){
  let v = n, u = ['B','KB','MB','GB','TB','PB'];
  for(let i=0;i<u.length;i++){
    if(Math.abs(v) < 1024) return (u[i]==='B') ? v+' B' : v.toFixed(2)+' '+u[i];
    v/=1024;
  }
  return v.toFixed(2)+' EB';
}
function esc(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

document.getElementById('meta').textContent =
  '產生時間：' + DATA.generated + (DATA.flag_cleanup ? '　·　已啟用「常見可清理」標記' : '');

// 分頁
const tabs = document.getElementById('tabs');
const main = document.getElementById('main');
DATA.drives.forEach((d, i) => {
  const t = document.createElement('div');
  t.className = 'tab' + (i===0?' active':'');
  t.textContent = d.root;
  t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.drive').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('drive'+i).classList.add('active');
  };
  tabs.appendChild(t);
  main.appendChild(renderDrive(d, i));
});

function renderDrive(d, idx){
  const wrap = document.createElement('div');
  wrap.className = 'drive' + (idx===0?' active':'');
  wrap.id = 'drive'+idx;

  // 總覽卡片
  const usedPct = d.disk_total ? (d.disk_used/d.disk_total*100) : 0;
  const cards = document.createElement('div');
  cards.className = 'cards';
  cards.innerHTML = `
    <div class="card"><div class="label">磁碟總容量</div><div class="value">${human(d.disk_total)}</div></div>
    <div class="card"><div class="label">已使用</div><div class="value">${human(d.disk_used)}</div>
      <div class="usagebar"><span style="width:${usedPct}%"></span></div>
      <div class="label" style="margin-top:6px">使用率 ${usedPct.toFixed(1)}%</div></div>
    <div class="card"><div class="label">可用空間</div><div class="value">${human(d.disk_free)}</div></div>
    <div class="card"><div class="label">本次掃描到</div><div class="value">${human(d.scanned_size)}</div>
      <div class="label" style="margin-top:6px">${d.file_count.toLocaleString()} 個檔案 · ${d.elapsed}s</div></div>`;
  wrap.appendChild(cards);

  // treemap
  const tmSec = document.createElement('section');
  tmSec.innerHTML = '<h2>頂層目錄佔比（Treemap）</h2><div class="hint">面積對應大小，僅顯示最大的數個項目。</div>';
  const tm = document.createElement('div');
  tm.className = 'treemap';
  const kids = (d.tree.children||[]).filter(c=>c.size>0).slice(0, """ + str(_TREEMAP_TOP) + r""");
  const totalTm = kids.reduce((a,c)=>a+c.size,0) || 1;
  kids.forEach(c=>{
    const frac = c.size/totalTm;
    const tile = document.createElement('div');
    tile.className='tile';
    tile.style.flex = (Math.max(frac,0.02)*100) + ' 1 90px';
    tile.style.opacity = 0.5 + frac*0.5;
    tile.title = c.path + '  ' + human(c.size);
    tile.innerHTML = esc(c.name) + '<small>'+human(c.size)+'</small>';
    tm.appendChild(tile);
  });
  tmSec.appendChild(tm);
  wrap.appendChild(tmSec);

  // 目錄樹
  const treeSec = document.createElement('section');
  treeSec.innerHTML = '<h2>目錄樹（依大小排序）</h2><div class="hint">點擊 ▶ 展開子目錄。長條代表佔父層比例。</div>';
  const treeRoot = document.createElement('div');
  treeRoot.className = 'tree';
  treeRoot.appendChild(renderNode(d.tree, d.tree.size, 0));
  treeSec.appendChild(treeRoot);
  wrap.appendChild(treeSec);

  // 類型分布
  const extSec = document.createElement('section');
  extSec.innerHTML = '<h2>檔案類型分布</h2>';
  const maxExt = d.exts.length ? d.exts[0].size : 1;
  let et = '<table><thead><tr><th>副檔名</th><th class="num">大小</th><th class="num">數量</th><th>佔比</th></tr></thead><tbody>';
  d.exts.forEach(e=>{
    const w = (e.size/maxExt*100).toFixed(1);
    et += `<tr><td>${esc(e.ext)}</td><td class="num">${human(e.size)}</td><td class="num">${e.count.toLocaleString()}</td>
      <td><div class="minibar"><span style="width:${w}%"></span></div></td></tr>`;
  });
  et += '</tbody></table>';
  extSec.innerHTML += et;
  wrap.appendChild(extSec);

  // 最大檔案
  const lgSec = document.createElement('section');
  lgSec.innerHTML = '<h2>最大檔案 Top '+d.largest.length+'</h2>';
  let lt = '<table><thead><tr><th class="num">大小</th><th>路徑</th></tr></thead><tbody>';
  d.largest.forEach(f=>{
    lt += `<tr><td class="num">${human(f.size)}</td><td class="path">${esc(f.path)}</td></tr>`;
  });
  lt += '</tbody></table>';
  lgSec.innerHTML += lt;
  wrap.appendChild(lgSec);

  // 無法存取
  if(d.inaccessible.length){
    const inSec = document.createElement('section');
    inSec.innerHTML = '<h2>無法存取的項目（'+d.inaccessible.length+'）</h2>'+
      '<div class="hint">這些路徑因權限不足或被佔用而略過，實際大小可能略大於本報告。</div>';
    const det = document.createElement('details');
    det.innerHTML = '<summary>展開清單</summary><div class="path" style="margin-top:8px">'+
      d.inaccessible.slice(0,500).map(esc).join('<br>')+'</div>';
    inSec.appendChild(det);
    wrap.appendChild(inSec);
  }
  return wrap;
}

function renderNode(node, parentSize, depth){
  const row = document.createElement('div');
  const line = document.createElement('div');
  line.className = 'row';
  const pct = parentSize ? (node.size/parentSize*100) : 0;
  const hasKids = node.children && node.children.length;

  const toggle = document.createElement('span');
  toggle.className = 'toggle' + (hasKids ? '' : ' leaf');
  toggle.textContent = hasKids ? '▶' : '·';

  const name = document.createElement('span');
  name.className = 'name ' + (node.is_aggregate ? 'agg' : (node.is_dir ? 'dir' : ''));
  name.textContent = node.name;
  name.title = node.path || node.name;

  const bar = document.createElement('span');
  bar.className = 'minibar';
  bar.innerHTML = '<span style="width:'+Math.min(pct,100)+'%"></span>';

  const sz = document.createElement('span');
  sz.className = 'sz'; sz.textContent = human(node.size);
  const pc = document.createElement('span');
  pc.className = 'pct'; pc.textContent = pct.toFixed(1)+'%';

  line.appendChild(toggle); line.appendChild(name);
  if(node.cleanup){
    const cl = document.createElement('span');
    cl.className='cleanup'; cl.textContent='可清理'; cl.title=node.cleanup;
    line.appendChild(cl);
  }
  line.appendChild(bar); line.appendChild(sz); line.appendChild(pc);
  row.appendChild(line);

  if(hasKids){
    const box = document.createElement('div');
    box.className = 'children';
    let built = false;
    toggle.onclick = () => {
      if(!built){
        node.children.forEach(c => box.appendChild(renderNode(c, node.size, depth+1)));
        built = true;
      }
      const open = box.classList.toggle('open');
      toggle.textContent = open ? '▼' : '▶';
    };
    // 預設展開最頂層
    if(depth === 0){
      node.children.forEach(c => box.appendChild(renderNode(c, node.size, depth+1)));
      built = true; box.classList.add('open'); toggle.textContent='▼';
    }
    row.appendChild(box);
  }
  return row;
}
</script>
</body>
</html>
"""
