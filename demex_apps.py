#!/usr/bin/env python3
"""Disassemble Dynamics app family: makes each app installable as a desktop /
phone app (PWA) and gives every app a clean address on the bridge.

    /hub/        Disassemble Dynamics launcher (opens DEMEX by default)
    /demex/      DEMEX operations cockpit        (DEMEX.html)
    /estimatio/  Estimatio estimating            (hosted/Estimatio.html)
    /qualora/    Qualora prequalification        (apps/qualora/app.html when present)

Each app has its own scope so Windows, macOS (Chrome/Edge/Safari) and iPhone
treat them as separate apps on one server. The bridge calls into this module;
nothing here touches the app HTML files on disk -- the manifest link, iPhone
meta tags and service-worker registration are injected when a page is served.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

APPS = {
    "hub": {
        "name": "Disassemble Dynamics", "short": "DDD", "icon": "ddd", "theme": "#0A84FF",
        "file": "apps/hub/index.html", "desc": "Disassemble Dynamics app launcher",
    },
    "demex": {
        "name": "DEMEX", "short": "DEMEX", "icon": "demex", "theme": "#2AD1B8",
        "file": "DEMEX.html", "desc": "DEMEX - demolition and abatement operating system",
    },
    "estimatio": {
        "name": "Estimatio", "short": "Estimatio", "icon": "estimatio", "theme": "#3B9DFF",
        "file": "hosted/Estimatio.html", "desc": "Estimatio - flooring and demolition estimating",
    },
    "qualora": {
        "name": "Qualora", "short": "Qualora", "icon": "qualora", "theme": "#F3B23E",
        "file": "apps/qualora/app.html", "fallback": "apps/qualora/index.html",
        "desc": "Qualora - subcontractor prequalification and applications",
    },
}

# Static files the bridge may serve without sign-in (icons/manifests are not sensitive
# and the phone needs them before the key is entered to show a proper home-screen icon).
PUBLIC_PREFIX = "/apps/pwa/icons/"


def app_file(key: str) -> Path | None:
    """The HTML file to serve for an app, or None if the app is not deployed here."""
    a = APPS.get(key)
    if not a:
        return None
    p = ROOT / a["file"]
    if p.is_file():
        return p
    fb = a.get("fallback")
    return (ROOT / fb) if fb and (ROOT / fb).is_file() else None


def available(key: str) -> bool:
    a = APPS.get(key)
    if not a:
        return False
    if key == "qualora":
        return (ROOT / a["file"]).is_file() or bool(os.environ.get("QUALORA_URL"))
    return (ROOT / a["file"]).is_file()


def status() -> dict:
    return {k: {"name": v["name"], "path": f"/{k}/", "available": available(k)} for k, v in APPS.items()}


def manifest(key: str) -> str:
    a = APPS[key]
    i = a["icon"]
    m = {
        "id": f"/{key}/",
        "name": a["name"],
        "short_name": a["short"],
        "description": a["desc"],
        "start_url": f"/{key}/",
        "scope": f"/{key}/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0B111A",
        "theme_color": a["theme"],
        "icons": [
            {"src": f"/apps/pwa/icons/{i}-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": f"/apps/pwa/icons/{i}-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": f"/apps/pwa/icons/{i}-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    if key == "hub":
        m["shortcuts"] = [
            {"name": "DEMEX", "url": "/demex/", "icons": [{"src": "/apps/pwa/icons/demex-192.png", "sizes": "192x192"}]},
            {"name": "Estimatio", "url": "/estimatio/", "icons": [{"src": "/apps/pwa/icons/estimatio-192.png", "sizes": "192x192"}]},
            {"name": "Qualora", "url": "/qualora/", "icons": [{"src": "/apps/pwa/icons/qualora-192.png", "sizes": "192x192"}]},
        ]
    return json.dumps(m, indent=1)


def service_worker(key: str, version: str) -> str:
    """Network-first service worker: the app always loads the live page when the
    server is reachable, and falls back to the last good copy offline so the
    icon still opens something useful. API calls are never cached."""
    return f"""/* {APPS[key]['name']} service worker (generated) */
const CACHE = 'ddd-{key}-{version}';
const SHELL = ['/{key}/', '/{key}/manifest.webmanifest', '/apps/pwa/icons/{APPS[key]['icon']}-192.png'];
self.addEventListener('install', e => {{ self.skipWaiting(); e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL).catch(() => null))); }});
self.addEventListener('activate', e => {{ e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith('ddd-{key}-') && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); }});
self.addEventListener('fetch', e => {{
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.startsWith('/api/')) return;
  e.respondWith(fetch(e.request).then(r => {{
    if (r.ok && r.type === 'basic') {{ const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }}
    return r;
  }}).catch(() => caches.match(e.request).then(m => m || (e.request.mode === 'navigate' ? caches.match('/{key}/') : undefined))));
}});
"""


def head_snippet(key: str) -> str:
    a = APPS[key]
    i = a["icon"]
    return (
        f'<link rel="manifest" href="/{key}/manifest.webmanifest">'
        f'<meta name="theme-color" content="{a["theme"]}">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="mobile-web-app-capable" content="yes">'
        '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
        f'<meta name="apple-mobile-web-app-title" content="{a["short"]}">'
        f'<link rel="apple-touch-icon" href="/apps/pwa/icons/{i}-180.png">'
        f'<link rel="icon" type="image/png" sizes="192x192" href="/apps/pwa/icons/{i}-192.png">'
        f'<script>if("serviceWorker" in navigator)navigator.serviceWorker.register("/{key}/sw.js",{{scope:"/{key}/"}}).catch(function(){{}});</script>'
    )


def inject(html: str, key: str) -> str:
    """Add the PWA head tags right after <head> (or <meta charset>) without
    disturbing anything else in the page."""
    snip = head_snippet(key)
    low = html.lower()
    pos = low.find("<head>")
    if pos >= 0:
        pos += len("<head>")
        return html[:pos] + snip + html[pos:]
    pos = low.find("<html")
    if pos >= 0:
        end = low.find(">", pos)
        return html[:end + 1] + "<head>" + snip + "</head>" + html[end + 1:]
    return snip + html
