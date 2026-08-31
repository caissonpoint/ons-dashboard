#!/usr/bin/env python3
"""Renders Wiki/*.md into a small static HTML site, styled to match
dashboard.py's own light/dark palette, so it can be deployed alongside
ons_dashboard.html at <site>/wiki/ and linked from the dashboard's own
"Wiki" nav button.

Usage: python3 build_wiki_site.py <src_dir_with_md> <out_dir>
  e.g. python3 build_wiki_site.py Wiki site/wiki
"""
import sys, re, pathlib
import markdown as md

SRC = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

PAGES = [
    ("Home.md", "index.html", "Home"),
    ("Using-the-Dashboard.md", "using-the-dashboard.html", "Using the Dashboard"),
    ("Architecture-and-Deployment.md", "architecture-and-deployment.html", "Architecture & Deployment"),
    ("Known-Limitations-and-Assumptions.md", "known-limitations.html", "Known Limitations"),
]

# Map internal wiki cross-links like (Using-the-Dashboard) or (Home) to their
# rendered .html filenames, so links keep working once rendered to HTML.
SLUG_TO_FILE = {stem.rsplit(".", 1)[0]: html for stem, html, _ in PAGES}
SLUG_TO_FILE["Home"] = "index.html"

def fix_internal_links(text: str) -> str:
    def repl(m):
        label, target = m.group(1), m.group(2)
        if target in SLUG_TO_FILE:
            return f"[{label}]({SLUG_TO_FILE[target]})"
        return m.group(0)
    return re.sub(r"\[([^\]]+)\]\(([A-Za-z0-9_-]+)\)", repl, text)

CSS = """
:root{
  color-scheme: light;
  --surface-1:#fcfcfb; --plane:#f4f4f1; --text-1:#0b0b0b; --text-2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
  --accent:#03183D; --wash:rgba(3,24,61,.08);
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --surface-1:#1a1a19; --plane:#0d0d0d; --text-1:#fff; --text-2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --accent:#4a78c2; --wash:rgba(74,120,194,.16);
}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--text-1);
  font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;}
.wrap{max-width:920px;margin:0 auto;padding:20px 20px 64px;display:flex;gap:32px}
aside{width:200px;flex:0 0 200px;position:sticky;top:20px;align-self:flex-start}
main{min-width:0;flex:1}
header.top{display:flex;flex-wrap:wrap;gap:12px;align-items:center;
  justify-content:space-between;margin-bottom:18px;padding:14px 20px;
  border-bottom:1px solid var(--ring);max-width:1160px;margin-left:auto;margin-right:auto}
header.top .brand{display:flex;align-items:baseline;gap:10px}
header.top h1{font-size:19px;margin:0;font-weight:700}
header.top .tag{color:var(--text-2);font-size:12.5px}
header.top .row{display:flex;gap:8px;align-items:center}
a{color:var(--accent)}
.navlink,button.iconBtn{border:1px solid var(--ring);background:var(--surface-1);
  color:var(--accent);border-radius:8px;padding:6px 11px;font-size:12.5px;
  font-weight:600;text-decoration:none;cursor:pointer}
.navlink:hover,button.iconBtn:hover{background:var(--wash)}
button.iconBtn{padding:6px 9px}
button.iconBtn svg{width:14px;height:14px;display:block}
aside nav{background:var(--surface-1);border:1px solid var(--ring);border-radius:10px;
  padding:14px}
aside nav .label{font-size:11px;text-transform:uppercase;letter-spacing:.04em;
  color:var(--muted);margin-bottom:8px}
aside nav a{display:block;padding:6px 4px;font-size:13.5px;color:var(--text-1);
  text-decoration:none;border-radius:6px}
aside nav a:hover{background:var(--wash);color:var(--accent)}
aside nav a.active{color:var(--accent);font-weight:700}
aside .back{display:block;margin-top:14px;font-size:12.5px}
main .card{background:var(--surface-1);border:1px solid var(--ring);border-radius:10px;
  padding:26px 30px}
main h1{font-size:26px;margin:0 0 6px}
main h2{font-size:18px;margin:28px 0 10px;padding-top:14px;border-top:1px solid var(--ring)}
main h2:first-of-type{border-top:none;padding-top:0}
main h3{font-size:15px;margin:20px 0 8px}
main p{margin:0 0 12px;color:var(--text-1)}
main li{margin-bottom:6px}
main ul,main ol{padding-left:22px;margin:0 0 14px}
main code{background:var(--wash);border-radius:4px;padding:1px 5px;font-size:12.5px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
main pre{background:var(--plane);border:1px solid var(--ring);border-radius:8px;
  padding:12px 14px;overflow-x:auto;font-size:12.5px;line-height:1.5}
main pre code{background:none;padding:0}
main table{border-collapse:collapse;width:100%;margin:10px 0 16px;font-size:13px}
main th,main td{border:1px solid var(--ring);padding:6px 9px;text-align:left;
  vertical-align:top}
main th{background:var(--wash);font-weight:700}
main hr{border:0;border-top:1px solid var(--ring);margin:22px 0}
footer.site{max-width:1160px;margin:28px auto 0;padding:0 20px;color:var(--muted);
  font-size:12px;line-height:1.6}
footer.site a{color:var(--muted)}
@media (max-width:720px){
  .wrap{flex-direction:column}
  aside{position:static;width:auto;flex:none}
}
"""

THEME_JS = """
(function(){
  var b=document.getElementById('themeBtn');
  var sun='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  var moon='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
  function paint(){
    var dark=document.documentElement.getAttribute('data-theme')==='dark';
    b.innerHTML = dark ? sun : moon;
    b.title = dark ? 'Switch to light mode' : 'Switch to dark mode';
  }
  var saved=null;
  try{ saved=localStorage.getItem('ons-wiki-theme'); }catch(e){}
  if(saved==='dark') document.documentElement.setAttribute('data-theme','dark');
  paint();
  b.addEventListener('click', function(){
    var dark=document.documentElement.getAttribute('data-theme')==='dark';
    if(dark){ document.documentElement.removeAttribute('data-theme'); }
    else{ document.documentElement.setAttribute('data-theme','dark'); }
    try{ localStorage.setItem('ons-wiki-theme', dark ? 'light' : 'dark'); }catch(e){}
    paint();
  });
})();
"""

nav_items = [(html, title) for _, html, title in PAGES]

def render_nav(current_html):
    items = []
    for html, title in nav_items:
        cls = ' class="active"' if html == current_html else ''
        items.append(f'<a href="{html}"{cls}>{title}</a>')
    return "\n".join(items)

def page_template(title, body_html, current_html):
    nav = render_nav(current_html)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — ONS Balances Wiki</title>
<style>{CSS}</style>
</head>
<body>
<header class="top">
  <div class="brand">
    <h1>ONS Balances — Wiki</h1>
    <span class="tag">gas-first ONS grid data dashboard</span>
  </div>
  <div class="row">
    <a class="navlink" href="../index.html">&larr; Back to dashboard</a>
    <button id="themeBtn" class="iconBtn" title="Toggle light/dark" aria-label="Toggle light/dark"></button>
  </div>
</header>
<div class="wrap">
  <aside>
    <nav>
      <div class="label">Pages</div>
      {nav}
      <a class="back" href="https://github.com/caissonpoint/ons-dashboard/blob/main/README.md" target="_blank" rel="noopener">README (GitHub) &#8599;</a>
    </nav>
  </aside>
  <main>
    <div class="card">
{body_html}
    </div>
  </main>
</div>
<footer class="site">
  <div class="wrap" style="padding:0;display:block;max-width:1160px">
    Source: <a href="https://github.com/caissonpoint/ons-dashboard" target="_blank" rel="noopener">caissonpoint/ons-dashboard</a> on GitHub.
    Data via <a href="https://dados.ons.org.br" target="_blank" rel="noopener">ONS Dados Abertos</a> (CC-BY).
    Questions or feedback: <a href="mailto:eb@gasbrazil.com">eb@gasbrazil.com</a>.
  </div>
</footer>
<script>{THEME_JS}</script>
</body>
</html>
"""

extensions = ["tables", "fenced_code", "sane_lists"]

for src_name, out_name, title in PAGES:
    src_path = SRC / src_name
    text = src_path.read_text(encoding="utf-8")
    text = fix_internal_links(text)
    body = md.markdown(text, extensions=extensions)
    html = page_template(title, body, out_name)
    (OUT / out_name).write_text(html, encoding="utf-8")
    print("wrote", OUT / out_name)

print("done")
