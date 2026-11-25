"""Built-in RYNA Mobile Companion Web Bridge (Wi-Fi / Hotspot PWA).

Lets the user open RYNA on any Android phone or iPhone over the same Wi-Fi or phone hotspot
(or Tailscale) with zero extra installations, talk to local Ollama, customize RYNA's avatar,
and control the Windows PC remotely.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import socket
import threading
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageOps

if TYPE_CHECKING:
    from app.desktop.robot_window import RynaRobotDesktop


def get_lan_ip() -> str:
    """Detect primary LAN / Wi-Fi IPv4 address for phone connection."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


MOBILE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="theme-color" content="#060d19">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.json">
<link rel="icon" href="/api/avatar.png">
<link rel="apple-touch-icon" href="/api/avatar.png">
<title>RYNA Mobile</title>
<style>
  :root { --bg:#060d19; --card:#0b1629; --inp:#10203a; --bd:#1e3a5f; --cy:#22d3ee; --tx:#eef5ff; --mu:#94a3b8; --gr:#34d399; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:var(--bg); color:var(--tx); display:flex; flex-direction:column; min-height:100dvh; }
  header { padding:12px 16px; background:var(--card); border-bottom:1px solid var(--bd); display:flex; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:10; }
  .brand { font-weight:800; letter-spacing:.6px; font-size:16px; display:flex; align-items:center; gap:8px; }
  .dot { width:9px; height:9px; border-radius:50%; background:var(--gr); box-shadow:0 0 8px var(--gr); }
  .pill { font-size:11px; color:var(--cy); background:var(--inp); padding:4px 9px; border-radius:99px; border:1px solid var(--bd); font-weight:700; }
  main { flex:1; padding:12px; max-width:560px; width:100%; margin:0 auto; display:flex; flex-direction:column; gap:10px; }
  .hero { background:radial-gradient(circle at 50% 35%, #112a4a 0%, var(--card) 72%); border:1px solid var(--bd); border-radius:16px; padding:12px; text-align:center; }
  .hero img { width:112px; height:112px; object-fit:contain; filter:drop-shadow(0 6px 14px rgba(34,211,238,.35)); }
  .sub { font-size:11px; color:var(--mu); margin-top:4px; }
  .chat { background:var(--card); border:1px solid var(--bd); border-radius:16px; padding:10px; height:230px; overflow-y:auto; display:flex; flex-direction:column; gap:8px; }
  .msg { padding:9px 12px; border-radius:12px; font-size:14px; line-height:1.4; max-width:88%; white-space:pre-wrap; word-break:break-word; }
  .msg.u { align-self:flex-end; background:#0e3a5d; border:1px solid #1d5d8f; }
  .msg.a { align-self:flex-start; background:var(--inp); border:1px solid var(--bd); }
  .bar { display:flex; gap:8px; }
  .bar input { flex:1; background:var(--inp); color:var(--tx); border:1px solid var(--bd); border-radius:12px; padding:12px; font-size:15px; outline:none; }
  .btn { background:var(--inp); color:var(--cy); border:1px solid var(--bd); border-radius:12px; padding:10px 14px; font-weight:700; font-size:13px; cursor:pointer; }
  .btn.pri { background:var(--cy); color:#04101f; border-color:var(--cy); }
  .btn.mic.on { background:#ef4444; color:#fff; border-color:#ef4444; animation:pulse 1s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.65} }
  .card { background:var(--card); border:1px solid var(--bd); border-radius:14px; padding:10px; }
  .card h3 { margin:0 0 8px; font-size:11px; color:var(--cy); letter-spacing:.7px; }
  .grid { display:grid; grid-template-columns:repeat(4,1fr); gap:6px; }
  .grid button { padding:9px 4px; font-size:12px; }
  .swatches { display:flex; gap:7px; justify-content:space-between; }
  .sw { flex:1; height:30px; border-radius:8px; border:2px solid rgba(255,255,255,.18); cursor:pointer; font-size:10px; font-weight:800; color:#04101f; }
  footer { text-align:center; font-size:10px; color:var(--mu); padding:8px; }
</style>
</head>
<body>
<header>
  <div class="brand"><span class="dot"></span>RYNA MOBILE</div>
  <div class="pill" id="st">CONNECTING...</div>
</header>
<main>
  <div class="hero">
    <img id="av" src="/api/avatar.png" alt="RYNA">
    <div class="sub" id="info">Connected to your Windows PC &amp; Local Ollama</div>
  </div>

  <div class="chat" id="chat">
    <div class="msg a">Hi! I'm RYNA on your phone. Type or tap 🎤 to control your PC or ask Ollama anything.</div>
  </div>

  <form class="bar" onsubmit="sendChat(event)">
    <button type="button" class="btn mic" id="micBtn" onclick="toggleMic()">🎤</button>
    <input id="inp" placeholder="Ask RYNA or control PC..." autocomplete="off">
    <button type="submit" class="btn pri">Send</button>
  </form>

  <div class="card">
    <h3>PC QUICK REMOTE</h3>
    <div class="grid">
      <button class="btn" onclick="quick('volume_up')">🔊 Vol +</button>
      <button class="btn" onclick="quick('volume_down')">🔉 Vol -</button>
      <button class="btn" onclick="quick('mute')">🔇 Mute</button>
      <button class="btn" onclick="quick('desktop')">🖥 Desktop</button>
      <button class="btn" onclick="quick('screenshot')">📸 Screen</button>
      <button class="btn" onclick="quick('health')">💚 Health</button>
      <button class="btn" onclick="quick('pose','sit')">🪑 Sit</button>
      <button class="btn" onclick="quick('pose','walk')">🚶 Walk</button>
    </div>
  </div>

  <div class="card">
    <h3>LIVE AVATAR COLOR</h3>
    <div class="swatches">
      <button class="sw" style="background:#94a3b8" onclick="setColor('original',0)">Orig</button>
      <button class="sw" style="background:#00e5ff" onclick="setColor('#00e5ff',180)"></button>
      <button class="sw" style="background:#2979ff" onclick="setColor('#2979ff',220)"></button>
      <button class="sw" style="background:#00e676" onclick="setColor('#00e676',140)"></button>
      <button class="sw" style="background:#ffab00" onclick="setColor('#ffab00',40)"></button>
      <button class="sw" style="background:#ff1744" onclick="setColor('#ff1744',0)"></button>
      <button class="sw" style="background:#d500f9" onclick="setColor('#d500f9',280)"></button>
      <button class="sw" style="background:#ff4081" onclick="setColor('#ff4081',330)"></button>
    </div>
  </div>
</main>
<footer>RYNA v1.0.0 · Copyright (c) 2026 Arinde Maurice · Tip: Tap browser menu → "Add to Home Screen"</footer>
<script>
const chat = document.getElementById('chat');
const inp = document.getElementById('inp');
function addMsg(txt, cls){
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = txt;
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
}
async function refreshStatus(){
  try {
    const r = await fetch('/api/status');
    const j = await r.json();
    document.getElementById('st').textContent = (j.pose || 'READY').toUpperCase();
    document.getElementById('info').textContent = j.model + (j.battery ? ' · ' + j.battery : '');
  } catch(e) {
    document.getElementById('st').textContent = 'OFFLINE';
  }
}
async function sendChat(e){
  if(e) e.preventDefault();
  const t = inp.value.trim();
  if(!t) return;
  inp.value = '';
  addMsg(t, 'u');
  try {
    const r = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:t})});
    const j = await r.json();
    const reply = j.reply || 'Done.';
    addMsg(reply, 'a');
    if(window.speechSynthesis && reply.length < 260){
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(new SpeechSynthesisUtterance(reply));
    }
    refreshStatus();
  } catch(err) {
    addMsg('Connection error: ' + err, 'a');
  }
}
async function quick(action, value){
  try {
    const r = await fetch('/api/quick', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action, value})});
    const j = await r.json();
    if(j.detail) addMsg(j.detail, 'a');
    refreshStatus();
  } catch(e){}
}
async function setColor(color, hue){
  await fetch('/api/quick', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'color', color, hue})});
  document.getElementById('av').src = '/api/avatar.png?t=' + Date.now();
}
let rec = null;
function toggleMic(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!SR){ alert('Voice input requires Chrome on Android or Safari on iPhone.'); return; }
  const btn = document.getElementById('micBtn');
  if(rec){ rec.stop(); rec = null; btn.classList.remove('on'); return; }
  rec = new SR();
  rec.lang = 'en-US';
  btn.classList.add('on');
  rec.onresult = (ev) => {
    inp.value = ev.results[0][0].transcript;
    sendChat();
  };
  rec.onend = () => { btn.classList.remove('on'); rec = null; };
  rec.start();
}
refreshStatus();
setInterval(refreshStatus, 5000);
</script>
</body>
</html>
"""


class RynaPhoneBridge:
    """Thread-safe local Wi-Fi HTTP/PWA companion server embedded in RYNA."""

    def __init__(self, robot: "RynaRobotDesktop", port: int = 8765) -> None:
        self.robot = robot
        self.core = robot.core
        self.preferred_port = port
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{get_lan_ip()}:{self.port}"

    def render_avatar_png(self) -> bytes:
        path = self.robot.assets_dir / "ryna_sit.png"
        if not path.is_file():
            path = self.robot.robot_png
        with Image.open(path) as raw:
            img = raw.convert("RGBA")
        img.thumbnail((220, 220), Image.Resampling.LANCZOS)
        col = str(getattr(self.robot, "accent_color", "") or "").strip()
        if col.lower() not in ("original", "default", "none", "") and len(col.lstrip("#")) == 6:
            hx = col.lstrip("#")
            tr, tg, tb = (int(hx[i : i + 2], 16) for i in (0, 2, 4))
            r, g, b, a = img.split()
            rgb = Image.merge("RGB", (r, g, b))
            colored = ImageOps.colorize(
                ImageOps.grayscale(rgb),
                black=(10, 10, 15),
                mid=(tr, tg, tb),
                white=(min(255, int(tr * 0.4 + 153)), min(255, int(tg * 0.4 + 153)), min(255, int(tb * 0.4 + 153))),
                midpoint=120,
            )
            sr, sg, sb = Image.blend(rgb, colored, alpha=0.88).split()
            img = Image.merge("RGBA", (sr, sg, sb, a))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def start(self) -> None:
        bridge = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: object) -> None:
                return

            def _send_json(self, payload: dict[str, Any], code: int = 200) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                if self.path == "/" or self.path.startswith("/?"):
                    raw = MOBILE_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if self.path == "/manifest.json":
                    manifest = {
                        "name": "RYNA Mobile Companion",
                        "short_name": "RYNA",
                        "start_url": "/",
                        "display": "standalone",
                        "background_color": "#060d19",
                        "theme_color": "#060d19",
                        "icons": [{"src": "/api/avatar.png", "sizes": "192x192", "type": "image/png"}],
                    }
                    self._send_json(manifest)
                    return
                if self.path.startswith("/api/avatar.png"):
                    png = bridge.render_avatar_png()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(png)))
                    self.end_headers()
                    self.wfile.write(png)
                    return
                if self.path == "/api/status":
                    bat = bridge.core.computer.battery_status()
                    op = bridge.core.ai.operating_mode(online=False)
                    self._send_json(
                        {
                            "ok": True,
                            "pose": bridge.robot.current_action,
                            "state": bridge.robot.state,
                            "model": op.get("active_model") or op.get("label") or "Local Ollama",
                            "battery": f"{bat['percent']}%" if bat.get("has_battery") else "",
                            "url": bridge.url,
                        }
                    )
                    return
                self._send_json({"error": "Not found"}, 404)

            def do_POST(self) -> None:
                ln = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(ln).decode("utf-8") or "{}") if ln > 0 else {}
                if self.path == "/api/chat":
                    text = str(body.get("text", "")).strip()
                    if not text:
                        self._send_json({"error": "Empty message"}, 400)
                        return
                    res = bridge.core.send_chat(bridge.robot.active_conv_id, text)
                    bridge.robot.active_conv_id = res.get("conversation_id", bridge.robot.active_conv_id)
                    msg = res.get("assistant_message") or {}
                    reply = str(msg.get("content", "")).strip() or "Done."
                    bridge.robot.root.after(
                        0,
                        lambda r=reply, t=text: bridge.robot._show_bubble(r, dictation_text=f"📱 {t}", show_mic_bar=False, duration=10.0),
                    )
                    self._send_json({"ok": True, "reply": reply})
                    return

                if self.path == "/api/quick":
                    act = str(body.get("action", "")).strip()
                    val = str(body.get("value", "")).strip()
                    detail = ""
                    if act == "volume_up":
                        bridge.core.computer.volume_control("up", 5)
                        detail = "Turned PC volume up."
                    elif act == "volume_down":
                        bridge.core.computer.volume_control("down", 5)
                        detail = "Turned PC volume down."
                    elif act == "mute":
                        bridge.core.computer.volume_control("mute")
                        detail = "Toggled PC mute."
                    elif act == "desktop":
                        bridge.core.computer.computer_shortcut("show_desktop")
                        detail = "Showing Windows desktop."
                    elif act == "screenshot":
                        cap = bridge.core.computer.capture_screen()
                        detail = f"Captured PC screen ({cap['width']}x{cap['height']})."
                    elif act == "health":
                        detail = bridge.core.sys_mgr.system_info_summary(battery_info=bridge.core.computer.battery_status())
                    elif act == "pose" and val in ("sit", "walk", "stand", "sleep"):
                        def _set_p() -> None:
                            bridge.robot.manual_pose = val if val != "walk" else None
                            if val == "walk":
                                bridge.robot.walk_enabled = True
                            bridge.robot.current_action = val
                            bridge.robot._redraw_now()

                        bridge.robot.root.after(0, _set_p)
                        detail = f"RYNA pose set to {val}."
                    elif act == "color":
                        col = str(body.get("color", "#00e5ff"))
                        hue = int(body.get("hue", 180))
                        bridge.robot.root.after(0, lambda c=col, h=hue: bridge.robot.reload_avatar(hue_shift=h, accent_color=c))
                        detail = f"Updated RYNA color to {col}."
                    self._send_json({"ok": True, "detail": detail})
                    return

                self._send_json({"error": "Not found"}, 404)

        for candidate_port in (self.preferred_port, 8766, 0):
            try:
                srv = ThreadingHTTPServer(("0.0.0.0", candidate_port), _Handler)
                self._server = srv
                self.port = int(srv.server_address[1])
                break
            except OSError:
                continue

        if self._server is None:
            return

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.core.logger.info("RYNA Mobile Companion ready at %s", self.url)

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
