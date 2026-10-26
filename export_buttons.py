"""'Copy image' / 'Download PNG' buttons for the table or chart rendered just above them.

A tiny same-origin component finds the element directly above itself on the page and renders it in the browser:
 * kind='plot'  -> the chart above, through the page's own Plotly (window.parent.Plotly.toImage, 2x resolution)
 * kind='table' -> the HTML table above, through html2canvas (a heading line is added to the picture so the saved
                   image says which country / commodity / units it shows)
Copy puts the PNG on the clipboard (browsers allow it on a click on https); if a browser blocks that, the button
says so and Download PNG still works.
"""
from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components

_TEMPLATE = """
<style>
  html,body{margin:0;background:transparent;font-family:'Source Sans 3','Source Sans Pro',Segoe UI,Arial,sans-serif}
  .row{display:flex;gap:8px;align-items:center;padding:2px 0}
  button{border:1px solid #cfd8d1;background:#fff;color:#32373c;border-radius:8px;padding:5px 12px;
         font:600 13px 'Source Sans 3','Source Sans Pro',Segoe UI,Arial,sans-serif;cursor:pointer}
  button:hover{background:#eef3ef}
  button:disabled{opacity:.55;cursor:wait}
  #msg{font-size:12.5px;color:#6b7a70}
  #msg.err{color:#b3261e}
</style>
<div class="row">
  <button id="copy" title="Copy the picture to the clipboard">⧉ Copy image</button>
  <button id="dl" title="Save the picture as a PNG file">⬇ Download PNG</button>
  <span id="msg"></span>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<script>
const CFG = __CFG__;
const P = window.parent;
const msg = document.getElementById('msg');
const say = (t, err) => { msg.textContent = t; msg.className = err ? 'err' : ''; if (!err) setTimeout(() => { msg.textContent = ''; }, 2500); };

function target() {
  const fe = window.frameElement;
  let node = fe.closest('[data-testid="stElementContainer"]') || fe.closest('.element-container') || fe.parentElement;
  const sel = CFG.kind === 'plot' ? '.js-plotly-plot' : '.bs-wrap';
  for (let prev = node.previousElementSibling; prev; prev = prev.previousElementSibling) {
    const hit = prev.matches(sel) ? prev : prev.querySelector(sel);
    if (hit) return hit;
  }
  return null;
}

async function makeBlob() {
  const t = target();
  if (!t) throw new Error('Nothing to capture above this button.');
  if (CFG.kind === 'plot') {
    if (!P.Plotly) throw new Error('Plotly is not available on this page.');
    const url = await P.Plotly.toImage(t, {format: 'png', width: t.offsetWidth, height: t.offsetHeight, scale: 2});
    return await (await fetch(url)).blob();
  }
  if (!window.html2canvas) throw new Error('The image library could not load (offline or blocked).');
  const tbl = t.querySelector('table');
  const canvas = await html2canvas(t, {
    scale: 2, backgroundColor: '#ffffff', useCORS: true, logging: false,
    windowWidth: Math.max(P.innerWidth, (tbl ? tbl.scrollWidth : 0) + 80),
    onclone: (doc, el) => {
      const st = doc.createElement('style');          // sticky header / first column mis-place in a screenshot
      st.textContent = 'table.bs thead th, table.bs th.lab{position:static !important;left:auto !important;top:auto !important}' +
        ' table.bs, table.bs *{font-family:Arial,Helvetica,sans-serif !important;letter-spacing:0 !important}' +
        ' .bs-title .t1{font-family:Georgia,serif !important}.bs-title .t2{font-family:Arial,Helvetica,sans-serif !important}' +
        ' .bs-title{position:static !important}';
      doc.head.appendChild(st);
      el.style.overflow = 'visible';
      if (tbl) el.style.width = tbl.scrollWidth + 'px';
      if (CFG.title) {
        const h = doc.createElement('div');
        h.textContent = CFG.title;
        h.style.cssText = 'font:700 22px Georgia,serif;padding:12px 14px 10px;color:#32373c;background:#fff';
        el.insertBefore(h, el.firstChild);
      }
    }
  });
  return await new Promise((res, rej) => canvas.toBlob(b => b ? res(b) : rej(new Error('Could not build the picture.')), 'image/png'));
}

function save(blob) {
  const a = P.document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = CFG.file;
  P.document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
}

document.getElementById('dl').onclick = async (ev) => {
  const b = ev.currentTarget; b.disabled = true; say('Preparing…');
  try { save(await makeBlob()); say('Saved ✓'); } catch (e) { say(String(e.message || e), true); }
  b.disabled = false;
};

async function legacyCopy(blob) {                       // older route: select a picture and run the copy command
  const url = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(blob); });
  const holder = document.createElement('div');
  holder.contentEditable = 'true';
  holder.style.cssText = 'position:fixed;left:-10000px;top:0;opacity:0';
  const img = document.createElement('img'); img.src = url; holder.appendChild(img); document.body.appendChild(holder);
  await new Promise(r => { if (img.complete) r(); else img.onload = r; });
  const range = document.createRange(); range.selectNodeContents(holder);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  holder.focus();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  sel.removeAllRanges(); holder.remove();
  return ok;
}

document.getElementById('copy').onclick = async (ev) => {
  const b = ev.currentTarget; b.disabled = true; say('Preparing…');
  const bp = makeBlob();                                      // one picture, started inside the click
  bp.catch(() => {});
  let done = false, capErr = null;
  try { await navigator.clipboard.write([new ClipboardItem({'image/png': bp})]); done = true; }
  catch (e1) {
    try { await bp; } catch (e) { capErr = e; }
    if (!capErr) {
      try { await P.navigator.clipboard.write([new ClipboardItem({'image/png': bp})]); done = true; } catch (e2) {}
    }
  }
  if (!done && !capErr) {
    try { done = await legacyCopy(await bp); } catch (e3) { done = false; }
  }
  if (done) say('Copied ✓');
  else if (capErr) say(String(capErr.message || capErr), true);
  else say('Copy was blocked by the browser: use Download PNG.', true);
  b.disabled = false;
};
</script>
"""


def image_buttons(kind: str, title: str, filename: str, height: int = 44) -> None:
    """kind: 'plot' (the Plotly chart above) or 'table' (the .bs-wrap table above)."""
    cfg = json.dumps({"kind": kind, "title": title, "file": filename})
    html = _TEMPLATE.replace("__CFG__", cfg)
    embed = getattr(components, "html", None)            # st.components.v1.html is being retired in favour of st.iframe
    if embed is not None:
        embed(html, height=height)
    else:
        st.iframe(html, height=height)
