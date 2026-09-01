"""Shared light theme + page chrome for the consolidated Album Studio web app.

Every section (dashboard/people/storyboard/editor/export -- see `app.py`) renders its own
content HTML and hands it to `page_shell()`, which wraps it in the same sidebar nav, top
bar, and stylesheet, so the app reads as one product instead of four separate tools glued
together. Palette (user-specified 2026-09-01): emerald green, royal blue, ivory/white,
slate gray, earth brown -- assigned below to a light, high-contrast, readable UI:
emerald as the primary action/active-nav color, royal blue for informational/"in
progress" states, slate for text/borders/structure, earth brown as a warm secondary
accent (tags, the brand mark), ivory/white for backgrounds. A muted terracotta is added
for error/failed states since the given palette has no red -- kept close in tone to the
earth brown so it doesn't clash.

Layout is fluid (not a fixed frame): the sidebar collapses to an icon-only rail below
~880px viewport width, and content grids use `auto-fill`/`minmax` so they reflow at any
browser window size (no separate mobile design -- just a resizable desktop window, per
user 2026-09-01).
"""

import html

NAV_ITEMS = [
    ("/", "Dashboard",
     '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
     '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'),
    ("/people/", "People",
     '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c0-3 2.5-5 5.5-5s5.5 2 5.5 5"/>'
     '<circle cx="17" cy="9" r="2.6"/><path d="M15.5 14.2c2.6.3 4.5 2.1 4.5 4.8"/>'),
    ("/storyboard/", "Storyboard",
     '<rect x="2" y="7" width="6" height="10" rx="1.2"/><rect x="9" y="7" width="6" height="10" rx="1.2"/>'
     '<rect x="16" y="7" width="6" height="10" rx="1.2"/>'),
    ("/editor/", "Spread Editor",
     '<rect x="3" y="3" width="18" height="18" rx="2"/>'
     '<path d="M3 15.5l4.7-4.7a1.5 1.5 0 0 1 2.1 0L14 15"/><circle cx="15.7" cy="8.3" r="1.6"/>'),
    ("/export/", "Export",
     '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 19h16"/>'),
]

THEME_CSS = """
:root{
  --bg:oklch(97.5% 0.014 88);
  --bg-elev:oklch(99.3% 0.005 88);
  --bg-elev-2:oklch(94.5% 0.014 88);
  --sidebar:oklch(99.3% 0.005 88);
  --border:oklch(87% 0.014 80);
  --border-soft:oklch(91.5% 0.012 80);
  --text:oklch(32% 0.02 255);
  --text-muted:oklch(48% 0.025 255);
  --text-faint:oklch(62% 0.02 255);
  --emerald:oklch(56% 0.12 155);
  --emerald-strong:oklch(46% 0.12 155);
  --emerald-soft:oklch(56% 0.12 155 / 0.13);
  --emerald-ink:oklch(98% 0.01 155);
  --royal:oklch(56% 0.16 262);
  --royal-soft:oklch(56% 0.16 262 / 0.13);
  --brown:oklch(48% 0.06 55);
  --brown-soft:oklch(48% 0.06 55 / 0.13);
  --danger:oklch(53% 0.15 30);
  --danger-soft:oklch(53% 0.15 30 / 0.12);
}
*{box-sizing:border-box;}
html,body{height:100%;}
body{margin:0;font-family:'IBM Plex Sans',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--text);}
a{color:var(--royal);text-decoration:none;} a:hover{color:var(--emerald-strong);}
h1,h2,h3{font-family:'Newsreader',Georgia,serif;font-weight:600;margin:0;}
.shell{display:flex;min-height:100vh;width:100%;}
.sidebar{width:232px;flex-shrink:0;background:var(--sidebar);border-right:1px solid var(--border);
  display:flex;flex-direction:column;padding:22px 14px;gap:26px;position:sticky;top:0;height:100vh;overflow-y:auto;}
.brand{padding:0 10px;display:flex;flex-direction:column;gap:2px;}
.brand-name{font-family:'Newsreader',Georgia,serif;font-size:19px;font-weight:600;color:var(--text);}
.brand-tag{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--brown);font-weight:600;}
.nav{display:flex;flex-direction:column;gap:3px;}
.nav-item{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:8px;color:var(--text-muted);
  font-size:13.5px;font-weight:500;}
.nav-item svg{width:17px;height:17px;flex-shrink:0;}
.nav-item.active{background:var(--emerald-soft);color:var(--emerald-strong);font-weight:600;}
.nav-item.active svg{color:var(--emerald);}
.nav-footer{margin-top:auto;padding-top:14px;border-top:1px solid var(--border-soft);font-size:11px;color:var(--text-faint);}
.main{flex:1;display:flex;flex-direction:column;min-width:0;}
.topbar{min-height:60px;flex-shrink:0;display:flex;align-items:center;justify-content:space-between;gap:12px;
  padding:12px 28px;border-bottom:1px solid var(--border);background:var(--bg-elev);flex-wrap:wrap;}
.topbar-title{font-size:17px;}
.topbar-sub{font-size:12px;color:var(--text-faint);margin-top:2px;}
.status-pill{display:inline-flex;align-items:center;gap:7px;padding:6px 12px 6px 9px;border-radius:999px;
  background:var(--bg-elev-2);border:1px solid var(--border);font-size:12px;color:var(--text-muted);font-weight:500;
  white-space:nowrap;}
.status-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0;background:var(--text-faint);}
.status-dot.loading{background:var(--royal);animation:pulse 1.4s ease-in-out infinite;}
.status-dot.ready{background:var(--emerald);}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:.3;}}
.content{flex:1;padding:26px 32px;display:flex;flex-direction:column;gap:22px;min-width:0;}
.btn{display:inline-flex;align-items:center;gap:7px;border-radius:8px;font-size:13.5px;font-weight:600;
  padding:9px 16px;border:1px solid var(--border);background:var(--bg-elev);color:var(--text);cursor:pointer;}
.btn svg{width:15px;height:15px;}
.btn-primary{background:var(--emerald);border-color:var(--emerald);color:var(--emerald-ink);}
.btn-primary:hover{background:var(--emerald-strong);border-color:var(--emerald-strong);}
.btn-outline:hover{background:var(--bg-elev-2);}
.btn-danger{background:var(--danger-soft);border-color:var(--danger-soft);color:var(--danger);}
.btn[disabled]{opacity:.45;cursor:default;}
.card{background:var(--bg-elev);border:1px solid var(--border);border-radius:12px;}
.section-title{font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--text-faint);}
input[type=text]{font-family:inherit;font-size:13.5px;padding:8px 11px;border:1px solid var(--border);
  border-radius:7px;background:var(--bg-elev);color:var(--text);}
.grid-fill{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px;}
.badge-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0;}
.badge-dot.pending{background:var(--border);}
.badge-dot.running{background:var(--royal);animation:pulse 1.4s ease-in-out infinite;}
.badge-dot.done{background:var(--emerald);}
.badge-dot.failed{background:var(--danger);}
@media (max-width: 880px){
  .sidebar{width:72px;padding:16px 10px;}
  .brand-tag,.nav-item span,.nav-footer{display:none;}
  .nav-item{justify-content:center;padding:12px;}
  .content{padding:20px;}
  .topbar{padding:12px 20px;}
}
"""


def _nav_html(active_prefix: str) -> str:
    items = []
    for href, label, icon in NAV_ITEMS:
        active = "active" if href == active_prefix else ""
        items.append(
            f'<a class="nav-item {active}" href="{href}">'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round">{icon}</svg>'
            f"<span>{html.escape(label)}</span></a>"
        )
    return "".join(items)


def page_shell(active_prefix: str, title: str, subtitle: str, body_html: str,
               extra_head: str = "", extra_script: str = "") -> bytes:
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)} &mdash; Album Studio</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{THEME_CSS}</style>
{extra_head}
</head>
<body>
<div class="shell">
  <aside class="sidebar">
    <div class="brand"><div class="brand-name">Album Studio</div><div class="brand-tag">Local Workspace</div></div>
    <nav class="nav">{_nav_html(active_prefix)}</nav>
    <div class="nav-footer">Fully local &mdash; no cloud upload.</div>
  </aside>
  <div class="main">
    <div class="topbar">
      <div><div class="topbar-title"><h2>{html.escape(title)}</h2></div><div class="topbar-sub">{subtitle}</div></div>
      <div class="status-pill" id="enginePill"><span class="status-dot" id="engineDot"></span><span id="engineText">AI engine: &hellip;</span></div>
    </div>
    <div class="content">
{body_html}
    </div>
  </div>
</div>
<script>
function pollEngine() {{
  fetch('/api/engine-status').then(r => r.json()).then(data => {{
    const dot = document.getElementById('engineDot');
    const text = document.getElementById('engineText');
    dot.className = 'status-dot ' + data.state;
    const label = {{idle: 'Idle', loading: 'Loading model\\u2026', ready: 'Ready'}}[data.state] || data.state;
    text.textContent = 'AI engine: ' + label;
  }}).catch(() => {{}});
}}
setInterval(pollEngine, 3000);
pollEngine();
{extra_script}
</script>
</body></html>"""
    return page.encode("utf-8")
