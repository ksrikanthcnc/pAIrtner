"""Shared responsive CSS for the admin dashboard (mobile-first, no external assets).

Breakpoints: base = 320px+, ``MQ_TABLET`` = 768px+, ``MQ_DESKTOP`` = 1024px+.
"""
from __future__ import annotations

MQ_TABLET = "@media (min-width:768px)"
MQ_DESKTOP = "@media (min-width:1024px)"

PALETTE = """
:root{
--bg:#f6f7f9;--surface:#fff;--text:#1c1f24;--muted:#6b7280;--border:#dde1e7;--head:#eef0f4;
--nav-bg:#1a1a2e;--nav-fg:#cfd6e0;--accent:#e94560;--link:#2b5cff;
--ok-bg:#e2f5e2;--ok-b:#8fcf8f;--warn-bg:#fff3cd;--warn-b:#e6cf7a;--err-bg:#fde2e2;--err-b:#e39a9a;
--fs-sm:12px;--fs-base:14px;--fs-lg:16px;--fs-h2:16px;--fs-h1:20px;--fs-stat:22px;
--gap:10px;--pad:12px;--radius:8px;--side-w:200px;
}
@media (prefers-color-scheme:dark){:root{
--bg:#14161a;--surface:#1e2127;--text:#e6e8eb;--muted:#9aa3af;--border:#333842;--head:#2a2e36;
--link:#8fb0ff;--ok-bg:#16301c;--warn-bg:#3a3213;--err-bg:#3b1a1a}}
"""

BASE = """
*{box-sizing:border-box}
body{font:var(--fs-base)/1.45 system-ui,sans-serif;margin:0;background:var(--bg);color:var(--text)}
a{color:var(--link)}
.topbar{position:sticky;top:0;z-index:10;background:var(--nav-bg);color:#fff;display:flex;
align-items:center;gap:12px;padding:8px var(--pad)}
.brand{font-weight:700;letter-spacing:.02em}
.brand::before{content:"♥ ";color:var(--accent)}
.burger{cursor:pointer;font-size:22px;line-height:1;padding:4px 8px;border-radius:6px;user-select:none}
.nav-toggle{position:absolute;opacity:0;pointer-events:none}
.nav-toggle:focus-visible+.topbar .burger{outline:2px solid #fff}
.side{display:none;background:var(--nav-bg);padding:4px var(--pad) var(--pad)}
.nav-toggle:checked~.side{display:block}
.side a{display:block;color:var(--nav-fg);text-decoration:none;padding:10px 8px;border-radius:6px}
.side a.on{color:#fff;font-weight:600;background:#ffffff1f}
.side form{margin:8px 0 0}
main{padding:var(--pad);max-width:1200px;margin:auto;overflow-wrap:anywhere}
h1{font-size:var(--fs-h1);margin:8px 0}
h2{font-size:var(--fs-h2);margin-top:22px}
.cards{display:grid;grid-template-columns:1fr;gap:var(--gap);margin:8px 0}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
padding:10px 14px;min-width:0}
.card b{display:block;font-size:var(--fs-stat)}
a.card{text-decoration:none;color:inherit}
.card .sub{color:var(--muted);font-size:var(--fs-sm)}
.tscroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:8px 0}
table{border-collapse:collapse;background:var(--surface);width:100%;min-width:480px}
th,td{border:1px solid var(--border);padding:4px 8px;text-align:left;vertical-align:top;
font-size:13px;word-break:break-word}
th{background:var(--head)}
pre{background:var(--surface);border:1px solid var(--border);padding:8px;overflow:auto}
.warn,.err,.ok{padding:8px;border-radius:6px;border:1px solid}
.warn{background:var(--warn-bg);border-color:var(--warn-b)}
.err{background:var(--err-bg);border-color:var(--err-b)}
.ok{background:var(--ok-bg);border-color:var(--ok-b)}
input[type=text],input[type=password]{padding:8px;width:100%;max-width:22em;font-size:16px}
button{padding:8px 14px;font-size:var(--fs-base);cursor:pointer}
.muted{color:var(--muted)}
.cols{display:grid;grid-template-columns:1fr;gap:var(--gap)}
.feed{list-style:none;margin:0;padding:0}
.feed li{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
padding:8px 12px;margin:6px 0}
.feed time{color:var(--muted);font-size:var(--fs-sm);display:block}
.dims{display:grid;grid-template-columns:1fr;gap:6px}
.dim{display:grid;grid-template-columns:7em 1fr 3em;gap:8px;align-items:center}
.dim .bar{background:var(--head);border-radius:999px;height:8px;overflow:hidden}
.dim .bar i{display:block;height:100%;background:var(--accent)}
"""

TABLET = MQ_TABLET + """{
.cards{grid-template-columns:repeat(2,1fr)}
.cols{grid-template-columns:repeat(2,1fr)}
main{padding:16px}
}
"""

DESKTOP = MQ_DESKTOP + """{
.burger{display:none}
.topbar{display:none}
.side{display:block;position:fixed;top:0;left:0;bottom:0;width:var(--side-w);padding-top:16px;overflow-y:auto}
.side::before{content:"♥ pAIrtner";display:block;color:#fff;font-weight:700;padding:0 8px 12px}
body>main,.shell>main{margin-left:var(--side-w);max-width:none}
.nav-toggle:checked~.side{display:block}
.cards{grid-template-columns:repeat(3,1fr)}
.cards.wide{grid-template-columns:repeat(4,1fr)}
.cols.three{grid-template-columns:repeat(3,1fr)}
table{min-width:0}
}
"""

LOGIN = """
.login{max-width:420px;margin:12vh auto 0}
"""

CSS = PALETTE + BASE + LOGIN + TABLET + DESKTOP
