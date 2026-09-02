"""Shared light theme + page chrome for the consolidated Album Studio web app.

Every section (dashboard/people/storyboard/editor/export -- see `app.py`) renders its own
content HTML and hands it to `page_shell()`, which wraps it in the same step chrome, top
bar, and stylesheet, so the app reads as one guided workflow instead of five separate tools
glued together. Palette (user-specified 2026-09-01): emerald green, royal blue, ivory/white,
slate gray, earth brown -- assigned below to a light, high-contrast, readable UI:
emerald as the primary action/active-step color, royal blue for informational/"in
progress" states, slate for text/borders/structure, earth brown as a warm secondary
accent (tags, the brand mark), ivory/white for backgrounds. A muted terracotta is added
for error/failed states since the given palette has no red -- kept close in tone to the
earth brown so it doesn't clash.

Navigation (rebuilt 2026-09-01, user request): a horizontal step bar across the top
(Dashboard -> People -> Storyboard -> Spread Editor -> Export) replaces the old persistent
sidebar -- you start at step 1, and a Continue button at the bottom of each page's content
takes you to the next step, rather than a nav rail that lets you jump straight into a
screen whose data doesn't exist yet. Steps ahead of what the pipeline has actually produced
are locked (greyed out, unclickable) via the same `/status` "ready" map the old sidebar
used.

Layout is fluid (not a fixed frame): content grids use `auto-fill`/`minmax` so they reflow
at any browser window size (no separate mobile design -- just a resizable desktop window,
per user 2026-09-01).
"""

import html

# The step order IS the workflow: Dashboard (setup + run pipeline) -> Storyboard -> People
# -> Spread Editor -> Export -- matching the pipeline's actual checkpoint order (spread
# planning finishes and pauses before face detection/people clustering does, per
# project_app.CHECKPOINTS), reordered 2026-09-01 per user request. Each entry: (href, short
# label, icon path, one-line purpose shown under the label on the step bar).
NAV_ITEMS = [
    ("/", "Setup",
     '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
     '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
     "Choose photos & size, run the pipeline"),
    ("/storyboard/", "Storyboard",
     '<rect x="2" y="7" width="6" height="10" rx="1.2"/><rect x="9" y="7" width="6" height="10" rx="1.2"/>'
     '<rect x="16" y="7" width="6" height="10" rx="1.2"/>',
     "Review and reorder spreads"),
    ("/people/", "People",
     '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c0-3 2.5-5 5.5-5s5.5 2 5.5 5"/>'
     '<circle cx="17" cy="9" r="2.6"/><path d="M15.5 14.2c2.6.3 4.5 2.1 4.5 4.8"/>',
     "Review and label who's who"),
    ("/editor/", "Spread Editor",
     '<rect x="3" y="3" width="18" height="18" rx="2"/>'
     '<path d="M3 15.5l4.7-4.7a1.5 1.5 0 0 1 2.1 0L14 15"/><circle cx="15.7" cy="8.3" r="1.6"/>',
     "Fine-tune individual spreads"),
    ("/export/", "Export",
     '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 19h16"/>',
     "Save the finished album PDF"),
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
.shell{display:flex;flex-direction:column;min-height:100vh;width:100%;}
.brandbar{display:flex;align-items:center;gap:10px;padding:14px 28px 0;}
.brand-name{font-family:'Newsreader',Georgia,serif;font-size:18px;font-weight:600;color:var(--text);}
.brand-tag{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--brown);font-weight:600;}
.stepbar{display:flex;align-items:flex-start;gap:0;padding:16px 28px 18px;border-bottom:1px solid var(--border);
  background:var(--bg-elev);flex-wrap:wrap;row-gap:14px;}
.step{display:flex;align-items:center;gap:9px;color:var(--text-muted);position:relative;padding:2px 14px;}
.step-num{width:26px;height:26px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;
  font-size:12px;font-weight:700;background:var(--bg-elev-2);border:1px solid var(--border);color:var(--text-faint);}
.step-label{font-size:13px;font-weight:600;}
.step-sub{font-size:10.5px;color:var(--text-faint);font-weight:400;display:block;}
.step.active .step-num{background:var(--emerald);border-color:var(--emerald);color:var(--emerald-ink);}
.step.active .step-label{color:var(--emerald-strong);}
.step.locked{opacity:.4;cursor:not-allowed;}
.step-connector{width:28px;height:1px;background:var(--border);flex-shrink:0;margin-top:13px;}
.main{flex:1;display:flex;flex-direction:column;min-width:0;}
.topbar{min-height:56px;flex-shrink:0;display:flex;align-items:center;justify-content:space-between;gap:12px;
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
.content{flex:1;padding:26px 32px;display:flex;flex-direction:column;gap:22px;min-width:0;max-width:1200px;width:100%;margin:0 auto;}
.step-footer{display:flex;justify-content:space-between;align-items:center;padding-top:6px;border-top:1px solid var(--border-soft);}
.btn{display:inline-flex;align-items:center;gap:7px;border-radius:8px;font-size:13.5px;font-weight:600;
  padding:9px 16px;border:1px solid var(--border);background:var(--bg-elev);color:var(--text);cursor:pointer;}
.btn svg{width:15px;height:15px;}
.btn-primary{background:var(--emerald);border-color:var(--emerald);color:var(--emerald-ink);}
.btn-primary:hover{background:var(--emerald-strong);border-color:var(--emerald-strong);}
.btn-outline:hover{background:var(--bg-elev-2);}
.btn-danger{background:var(--danger-soft);border-color:var(--danger-soft);color:var(--danger);}
.btn[disabled],.btn.locked{opacity:.45;cursor:not-allowed;}
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
  .step-sub{display:none;}
  .step-connector{width:14px;}
  .stepbar{padding:14px 16px;}
  .content{padding:20px;}
  .topbar{padding:12px 20px;}
  .brandbar{padding:12px 16px 0;}
}
"""


def _stepbar_html(active_prefix: str) -> str:
    items = []
    n = len(NAV_ITEMS)
    for i, (href, label, icon, sub) in enumerate(NAV_ITEMS):
        active = "active" if href == active_prefix else ""
        # data-nav-href drives the readiness-lock script below -- step 1 ("/") has no
        # prerequisite and is never locked.
        items.append(
            f'<a class="step {active}" href="{href}" data-nav-href="{href}">'
            f'<span class="step-num">{i + 1}</span>'
            f'<span><span class="step-label">{html.escape(label)}</span>'
            f'<span class="step-sub">{html.escape(sub)}</span></span></a>'
        )
        if i < n - 1:
            items.append('<span class="step-connector"></span>')
    return "".join(items)


def _step_footer_html(active_prefix: str) -> str:
    hrefs = [item[0] for item in NAV_ITEMS]
    if active_prefix not in hrefs:
        return ""
    idx = hrefs.index(active_prefix)
    back = (
        f'<a class="btn btn-outline" href="{hrefs[idx - 1]}">&larr; Back: {html.escape(NAV_ITEMS[idx - 1][1])}</a>'
        if idx > 0 else "<span></span>"
    )
    if idx < len(hrefs) - 1:
        next_href, next_label = hrefs[idx + 1], NAV_ITEMS[idx + 1][1]
        forward = (
            f'<a class="btn btn-primary" id="continueBtn" href="{next_href}" data-nav-href="{next_href}">'
            f'Continue: {html.escape(next_label)} &rarr;</a>'
        )
    else:
        forward = "<span></span>"
    return f'<div class="step-footer">{back}{forward}</div>'


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
  <div class="brandbar"><span class="brand-name">Album Studio</span><span class="brand-tag">&middot; Local Workspace</span></div>
  <nav class="stepbar">{_stepbar_html(active_prefix)}</nav>
  <div class="main">
    <div class="topbar">
      <div><div class="topbar-title"><h2>{html.escape(title)}</h2></div><div class="topbar-sub">{subtitle}</div></div>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <div class="status-pill" id="busyPill" style="display:none;"><span class="status-dot loading"></span><span id="busyPillText"></span></div>
        <button class="btn btn-danger" id="globalStopBtn" style="display:none;">Stop &amp; Clear Project</button>
        <div class="status-pill" id="enginePill"><span class="status-dot" id="engineDot"></span><span id="engineText">AI engine: &hellip;</span></div>
      </div>
    </div>
    <div class="content">
{body_html}
{_step_footer_html(active_prefix)}
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

function pollNavReady() {{
  fetch('/status').then(r => r.json()).then(data => {{
    if (data.ready) {{
      document.querySelectorAll('[data-nav-href]').forEach(a => {{
        const href = a.dataset.navHref;
        const ready = href === '/' || data.ready[href] !== false;
        a.classList.toggle('locked', !ready);
        if (!ready && !a.dataset.lockWired) {{
          a.dataset.lockWired = '1';
          a.addEventListener('click', (e) => {{
            if (a.classList.contains('locked')) {{
              e.preventDefault();
              alert('This screen isn\\'t ready yet -- run the earlier pipeline stages first.');
            }}
          }});
        }}
      }});
    }}

    // Any running pipeline stage is shown + stoppable from every screen, not just Setup,
    // per user request (2026-09-02): each stage now has its own Start/Pause button on its
    // own step screen, but Stop is always whole-project, so it stays global here.
    const pill = document.getElementById('busyPill');
    const pillText = document.getElementById('busyPillText');
    const stopBtn = document.getElementById('globalStopBtn');
    if (!pill || !stopBtn) return;
    if (data.any_running) {{
      pill.style.display = 'inline-flex';
      const runningKey = data.jobs ? Object.keys(data.jobs).find(k => data.jobs[k].running) : null;
      pillText.textContent = runningKey ? ('Running: ' + runningKey) : 'A stage is running\\u2026';
      stopBtn.style.display = 'inline-flex';
    }} else {{
      pill.style.display = 'none';
      stopBtn.style.display = 'none';
    }}
    if (!stopBtn.dataset.wired) {{
      stopBtn.dataset.wired = '1';
      stopBtn.addEventListener('click', () => {{
        if (!confirm('This will permanently DELETE the project database and all exports (photos on disk are untouched). Continue?')) return;
        stopBtn.disabled = true;
        fetch('/stop-project', {{method: 'POST'}}).then(() => location.reload()).catch(() => {{ stopBtn.disabled = false; }});
      }});
    }}
  }}).catch(() => {{}});
}}
setInterval(pollNavReady, 3000);
pollNavReady();
{extra_script}
</script>
</body></html>"""
    return page.encode("utf-8")
