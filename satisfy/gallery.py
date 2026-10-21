"""Batch gallery: a self-contained HTML page for picking keepers.

Open ``gallery.html`` directly (marks are kept in the browser and the page
shows a ready-to-copy ``final`` command), or run ``python -m satisfy serve
<batch_dir>`` so marks are also written to ``keepers.json`` in the batch folder,
where ``final`` picks them up automatically.
"""

from __future__ import annotations

import html
import json
import socketserver
import webbrowser
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

KEEPERS_FILE = "keepers.json"
MANIFEST_FILE = "manifest.json"


def read_keepers(batch_dir: Path) -> list[int]:
    path = batch_dir / KEEPERS_FILE
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    seeds = data.get("seeds", []) if isinstance(data, dict) else data
    return sorted({int(s) for s in seeds})


def write_gallery(batch_dir: Path, manifest: dict) -> Path:
    page = TEMPLATE.replace("__TITLE__", html.escape(manifest.get("name", "batch")))
    page = page.replace("__MANIFEST__", json.dumps(manifest).replace("</", "<\\/"))
    out = batch_dir / "gallery.html"
    out.write_text(page)
    return out


def serve(batch_dir: Path, port: int = 8765, open_browser: bool = True) -> None:
    batch_dir = batch_dir.resolve()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(batch_dir), **kwargs)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self):  # noqa: N802
            if self.path.split("?")[0].rstrip("/") == "/keepers":
                self._json({"seeds": read_keepers(batch_dir)})
            else:
                super().do_GET()

        def _json(self, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") != "/keepers":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length) or b"{}")
                seeds = sorted({int(s) for s in data.get("seeds", [])})
            except (ValueError, TypeError, AttributeError):
                self.send_error(400, "expected {\"seeds\": [...]}")
                return
            (batch_dir / KEEPERS_FILE).write_text(json.dumps({"seeds": seeds}, indent=1))
            print(f"  keepers: {seeds or 'none'}")
            self._json({"ok": True, "seeds": seeds})

        def log_message(self, fmt, *args):
            pass  # keep the terminal quiet; saves are reported in do_POST

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler) as httpd:
        url = f"http://127.0.0.1:{port}/gallery.html"
        print(f"Serving {batch_dir}\n  open {url}\n  marks save to {batch_dir / KEEPERS_FILE}\n  Ctrl+C to stop")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print()


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · keepers</title>
<link rel="icon" href="data:,">
<style>
  :root {
    --bg: #0f0f13; --panel: #18181f; --line: #2a2a35; --text: #ececf1; --muted: #9a9aab;
    --keep: #ffcc33; --escape: #3ddc97; --trapped: #6aa8ff; --timeout: #9a9aab;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 14px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; padding-bottom: 96px; }
  header { padding: 20px 24px 8px; display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: baseline; }
  h1 { font-size: 20px; margin: 0; }
  .meta { color: var(--muted); }
  .controls { padding: 4px 24px 16px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  button, .chip { background: var(--panel); color: var(--text); border: 1px solid var(--line); border-radius: 8px;
                  padding: 6px 12px; font: inherit; cursor: pointer; }
  button:hover { border-color: #444456; }
  .chip.on { background: #2a2a38; border-color: #55556a; }
  .hint { color: var(--muted); margin-left: auto; font-size: 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 16px; padding: 0 24px; }
  .card { background: var(--panel); border: 2px solid var(--line); border-radius: 12px; overflow: hidden;
          position: relative; outline: none; }
  .card:focus-visible { border-color: #8888aa; }
  .card.keep { border-color: var(--keep); box-shadow: 0 0 0 1px var(--keep), 0 6px 24px rgba(255, 204, 51, .15); }
  .card video { display: block; width: 100%; aspect-ratio: 9 / 16; background: #000; object-fit: cover; cursor: pointer; }
  .info { display: flex; align-items: center; gap: 8px; padding: 8px 10px; flex-wrap: wrap; }
  .seed { font-weight: 700; }
  .badge { font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 99px; color: #0b0b0f; }
  .badge.escape { background: var(--escape); } .badge.trapped { background: var(--trapped); }
  .badge.timeout { background: var(--timeout); } .badge.loop { background: #c9a7ff; }
  .stats { color: var(--muted); font-size: 12px; width: 100%; }
  .star { margin-left: auto; font-size: 18px; line-height: 1; padding: 4px 10px; }
  .card.keep .star { background: var(--keep); color: #1a1400; border-color: var(--keep); }
  footer { position: fixed; left: 0; right: 0; bottom: 0; background: rgba(15, 15, 19, .96); border-top: 1px solid var(--line);
           padding: 12px 24px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; backdrop-filter: blur(6px); }
  footer code { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px;
                flex: 1 1 380px; overflow-x: auto; white-space: nowrap; font-size: 12.5px; }
  #count { font-weight: 700; min-width: 90px; }
  #saved { color: var(--muted); font-size: 12px; }
  @media (max-width: 520px) {
    header, .controls, .grid { padding-left: 16px; padding-right: 16px; }
    .grid { grid-template-columns: repeat(2, 1fr); gap: 10px; }
    footer { padding: 10px 16px; }
    .hint { display: none; }
  }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <span class="meta" id="meta"></span>
</header>
<div class="controls">
  <span class="chip on" data-filter="all">All</span>
  <span class="chip" data-filter="keep">Keepers</span>
  <span class="chip" data-filter="escape">Escape</span>
  <span class="chip" data-filter="trapped">Trapped</span>
  <button id="sound" title="Unmute the video under the cursor">Sound: off</button>
  <span class="hint">Hover to play · click ★ or press K to keep · ←/→ to move</span>
</div>
<main class="grid" id="grid"></main>
<footer>
  <span id="count">0 keepers</span>
  <code id="cmd"></code>
  <button id="copy">Copy command</button>
  <button id="download">Download keepers.json</button>
  <span id="saved"></span>
</footer>
<script>
const M = __MANIFEST__;
const STORE = "satisfy-keepers:" + (M.id || M.name);
const served = location.protocol.startsWith("http");
let keep = new Set();
let filter = "all";
let sound = false;

function loadLocal() {
  try { return new Set(JSON.parse(localStorage.getItem(STORE) || "[]")); } catch (e) { return new Set(); }
}
function saveLocal() {
  try { localStorage.setItem(STORE, JSON.stringify([...keep])); } catch (e) {}
}
function command() {
  const seeds = [...keep].sort((a, b) => a - b);
  const dir = M.dir || ".";
  if (!seeds.length) return "Mark keepers to build the final command";
  return served ? `python -m satisfy final ${dir}` : `python -m satisfy final ${dir} --seeds ${seeds.join(" ")}`;
}
let saveTimer = null;
function persist() {
  saveLocal();
  document.getElementById("count").textContent = `${keep.size} keeper${keep.size === 1 ? "" : "s"}`;
  document.getElementById("cmd").textContent = command();
  if (!served) { document.getElementById("saved").textContent = "saved in this browser"; return; }
  clearTimeout(saveTimer);
  document.getElementById("saved").textContent = "saving…";
  saveTimer = setTimeout(() => {
    fetch("keepers", { method: "POST", headers: { "Content-Type": "application/json" },
                       body: JSON.stringify({ seeds: [...keep].sort((a, b) => a - b) }) })
      .then(r => { document.getElementById("saved").textContent = r.ok ? "saved to keepers.json" : "save failed"; })
      .catch(() => { document.getElementById("saved").textContent = "save failed (server stopped?)"; });
  }, 200);
}
function toggle(seed) {
  keep.has(seed) ? keep.delete(seed) : keep.add(seed);
  document.querySelector(`.card[data-seed="${seed}"]`).classList.toggle("keep", keep.has(seed));
  applyFilter();
  persist();
}
function applyFilter() {
  for (const card of document.querySelectorAll(".card")) {
    const e = M.entries.find(x => String(x.seed) === card.dataset.seed);
    const show = filter === "all" || (filter === "keep" && keep.has(e.seed)) ||
                 (filter === "escape" && e.ending === "escape") ||
                 (filter === "trapped" && e.ending !== "escape");
    card.style.display = show ? "" : "none";
  }
}
function card(e) {
  const el = document.createElement("article");
  el.className = "card"; el.tabIndex = 0; el.dataset.seed = e.seed;
  const c = e.counts || {};
  const extra = c.spawn ? ` · ${c.spawn + 1} balls` : "";
  el.innerHTML = `
    <video src="${e.preview}" poster="${e.poster}" muted loop playsinline preload="none"></video>
    <div class="info">
      <span class="seed">seed ${e.seed}</span>
      <span class="badge ${e.ending}">${e.ending}</span>
      <button class="star" title="Keep (K)">★</button>
      <span class="stats">${e.length.toFixed(1)}s · ends at ${e.end_t.toFixed(1)}s · ${c.bounce || 0} bounces${extra} · ${e.palette}</span>
    </div>`;
  const v = el.querySelector("video");
  const play = () => { v.muted = !sound; v.play().catch(() => {}); };
  const stop = () => { v.pause(); };
  el.addEventListener("mouseenter", play);
  el.addEventListener("mouseleave", stop);
  el.addEventListener("focus", play);
  el.addEventListener("blur", stop);
  v.addEventListener("click", () => v.paused ? play() : stop());
  el.querySelector(".star").addEventListener("click", () => toggle(e.seed));
  return el;
}
function init(initial) {
  keep = initial;
  const grid = document.getElementById("grid");
  for (const e of M.entries) grid.appendChild(card(e));
  for (const s of keep) document.querySelector(`.card[data-seed="${s}"]`)?.classList.add("keep");
  const ends = M.entries.reduce((a, e) => (a[e.ending] = (a[e.ending] || 0) + 1, a), {});
  document.getElementById("meta").textContent =
    `${M.entries.length} previews · ${M.config.format} · ${M.config.sound_set} · ` +
    Object.entries(ends).map(([k, v]) => `${v} ${k}`).join(", ") + (M.created ? ` · ${M.created}` : "");
  applyFilter();
  persist();
}
document.querySelectorAll(".chip").forEach(ch => ch.addEventListener("click", () => {
  document.querySelectorAll(".chip").forEach(c => c.classList.remove("on"));
  ch.classList.add("on"); filter = ch.dataset.filter; applyFilter();
}));
document.getElementById("sound").addEventListener("click", ev => {
  sound = !sound; ev.target.textContent = `Sound: ${sound ? "on" : "off"}`;
  document.querySelectorAll("video").forEach(v => { if (!v.paused) v.muted = !sound; });
});
document.getElementById("copy").addEventListener("click", () => {
  navigator.clipboard?.writeText(command()).then(() => {
    document.getElementById("saved").textContent = "command copied";
  });
});
document.getElementById("download").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify({ seeds: [...keep].sort((a, b) => a - b) }, null, 1)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "keepers.json"; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});
document.addEventListener("keydown", ev => {
  const cards = [...document.querySelectorAll(".card")].filter(c => c.style.display !== "none");
  const i = cards.indexOf(document.activeElement);
  if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
    const next = cards[Math.max(0, Math.min(cards.length - 1, i + (ev.key === "ArrowRight" ? 1 : -1)))];
    next?.focus(); ev.preventDefault();
  } else if ((ev.key === "k" || ev.key === "K") && i >= 0) {
    toggle(Number(cards[i].dataset.seed));
  }
});
if (served) {
  // keepers.json on disk wins; marks made earlier from the file:// page are kept if it is empty
  fetch("keepers").then(r => r.ok ? r.json() : null)
    .then(d => init(d && d.seeds && d.seeds.length ? new Set(d.seeds.map(Number)) : loadLocal()))
    .catch(() => init(loadLocal()));
} else {
  init(loadLocal());
}
</script>
</body>
</html>
"""
