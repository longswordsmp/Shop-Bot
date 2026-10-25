"""Generate a local HTML preview of every UI state the bot can show.

Usage:
    python -m tools.build_preview                      # all features, writes preview/out/index.html
    python -m tools.build_preview --enabled-only       # only features enabled in settings.toml
    python -m tools.build_preview --out some/file.html

The preview is a LOCAL SIMULATION rendered from the same view functions and specs the bot sends to
Discord. It is not a Discord server, and clicking in it proves nothing about Discord behaviour.
Text that is not configured yet is shown as ``⟦content.key⟧``. Sample data (product names etc.) is
synthetic and bracketed. No secrets or environment variables are read.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import html
import json
import re
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from shopbot.config.content import ContentFileError
from shopbot.config.settings import SettingsError
from shopbot.ui import custom_ids
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.routing import Route, Router
from shopbot.ui.spec import (
    ButtonSpec,
    EmbedSpec,
    MessageSpec,
    ModalSpec,
    PreviewState,
    SelectSpec,
)
from tools.offline import ROOT, build_offline_app

DEFAULT_OUT = ROOT / "preview" / "out" / "index.html"
_MARKER_RE = re.compile(r"⟦([^⟧]+)⟧")

# --------------------------------------------------------------------------------------------------
# Discord-flavoured markdown (subset) -> HTML. Input is escaped first; only known constructs are added.
# --------------------------------------------------------------------------------------------------


def _inline(text: str) -> str:
    code_spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        code_spans.append(f"<code>{match.group(1)}</code>")
        return f"\x00{len(code_spans) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", stash, text)
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<u>\1</u>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"\|\|(.+?)\|\|", r'<span class="spoiler">\1</span>', text)
    text = re.sub(r"&lt;@!?(\d+)&gt;", r'<span class="mention">@user</span>', text)
    text = re.sub(r"&lt;@&amp;(\d+)&gt;", r'<span class="mention">@role</span>', text)
    text = re.sub(r"&lt;#(\d+)&gt;", r'<span class="mention">#channel</span>', text)
    text = re.sub(r"&lt;a?:(\w+):\d+&gt;", r'<span class="cemoji">:\1:</span>', text)
    text = re.sub(
        r"&lt;t:(\d+)(?::[tTdDfFR])?&gt;",
        lambda m: f'<span class="ts">{datetime.fromtimestamp(int(m.group(1)), tz=UTC):%Y-%m-%d %H:%M} UTC</span>',
        text,
    )
    text = _MARKER_RE.sub(r'<span class="missing" title="Not configured yet">⟦\1⟧</span>', text)
    for index, span in enumerate(code_spans):
        text = text.replace(f"\x00{index}\x00", span)
    return text


def markdown(source: str | None) -> str:
    if not source:
        return ""
    escaped = html.escape(source, quote=False)
    blocks: list[str] = []
    parts = re.split(r"```(?:[\w+-]+\n)?(.*?)```", escaped, flags=re.S)
    for index, part in enumerate(parts):
        if index % 2 == 1:
            blocks.append(f"<pre><code>{part.strip(chr(10))}</code></pre>")
            continue
        lines_out: list[str] = []
        in_list = False
        for line in part.split("\n"):
            heading = re.match(r"^(#{1,3}) (.+)$", line)
            subtext = re.match(r"^-# (.+)$", line)
            quote = re.match(r"^&gt; ?(.*)$", line)
            bullet = re.match(r"^\s*[-*] (.+)$", line)
            if bullet and not subtext:
                if not in_list:
                    lines_out.append("<ul>")
                    in_list = True
                lines_out.append(f"<li>{_inline(bullet.group(1))}</li>")
                continue
            if in_list:
                lines_out.append("</ul>")
                in_list = False
            if heading:
                level = len(heading.group(1))
                lines_out.append(f"<h{level + 2} class='md-h'>{_inline(heading.group(2))}</h{level + 2}>")
            elif subtext:
                lines_out.append(f"<div class='subtext'>{_inline(subtext.group(1))}</div>")
            elif quote:
                lines_out.append(f"<blockquote>{_inline(quote.group(1))}</blockquote>")
            else:
                lines_out.append(_inline(line) + "<br>")
        if in_list:
            lines_out.append("</ul>")
        blocks.append("".join(lines_out))
    result = "".join(blocks)
    return re.sub(r"(<br>)+$", "", result)


def plain(text: str | None) -> str:
    """Escaped text for single-line elements (titles, labels), with missing-content markers highlighted."""
    if not text:
        return ""
    return _MARKER_RE.sub(r'<span class="missing" title="Not configured yet">⟦\1⟧</span>', html.escape(text))


# --------------------------------------------------------------------------------------------------
# Spec renderers
# --------------------------------------------------------------------------------------------------


def _color(value: int | None) -> str:
    return f"#{value:06x}" if value is not None else "#1e1f22"


def render_embed(embed: EmbedSpec) -> str:
    parts: list[str] = [f'<div class="embed" style="border-left-color:{_color(embed.color)}"><div class="embed-grid">']
    parts.append('<div class="embed-body">')
    if embed.author_name:
        icon = (
            f'<img class="author-icon" src="{html.escape(embed.author_icon_url)}" alt="">'
            if embed.author_icon_url
            else ""
        )
        parts.append(f'<div class="embed-author">{icon}<span>{plain(embed.author_name)}</span></div>')
    if embed.title:
        title = plain(embed.title)
        if embed.url:
            title = f'<a href="{html.escape(embed.url)}" target="_blank" rel="noopener">{title}</a>'
        parts.append(f'<div class="embed-title">{title}</div>')
    if embed.description:
        parts.append(f'<div class="embed-desc">{markdown(embed.description)}</div>')
    if embed.fields:
        parts.append('<div class="embed-fields">')
        for field in embed.fields:
            cls = "field inline" if field.inline else "field"
            parts.append(
                f'<div class="{cls}"><div class="field-name">{plain(field.name)}</div>'
                f'<div class="field-value">{markdown(field.value)}</div></div>'
            )
        parts.append("</div>")
    if embed.image_url:
        parts.append(f'<img class="embed-image" src="{html.escape(embed.image_url)}" alt="image" loading="lazy">')
    footer_bits = []
    if embed.footer_text:
        footer_bits.append(plain(embed.footer_text).replace("\n", "<br>"))
    if embed.timestamp is not None:
        footer_bits.append(f"{datetime.fromtimestamp(embed.timestamp, tz=UTC):%Y-%m-%d %H:%M} UTC")
    if footer_bits:
        icon = (
            f'<img class="footer-icon" src="{html.escape(embed.footer_icon_url)}" alt="">'
            if embed.footer_icon_url and embed.footer_text
            else ""
        )
        parts.append(f'<div class="embed-footer">{icon}<span>{" • ".join(footer_bits)}</span></div>')
    parts.append("</div>")
    if embed.thumbnail_url:
        parts.append(
            f'<img class="embed-thumb" src="{html.escape(embed.thumbnail_url)}" alt="thumbnail" loading="lazy">'
        )
    parts.append("</div></div>")
    return "".join(parts)


def _route_title(custom_id: str | None, kind: str, router: Router) -> str:
    if not custom_id:
        return ""
    parsed = custom_ids.parse(custom_id)
    known: dict[tuple[str, str, str], Route] = {(r.kind, r.route, r.action): r for r in router.routes()}
    if parsed is None or (kind, parsed[0], parsed[1]) not in known:
        return f"{custom_id} — NO HANDLER REGISTERED"
    route = known[(kind, parsed[0], parsed[1])]
    access = "" if route.access == "any" else f" ({route.access} only)"
    return f"{custom_id} → handler {route.handler.__module__}.{route.handler.__name__}{access}"


def render_components(spec: MessageSpec, router: Router) -> str:
    rows: list[str] = []
    for row in spec.rows:
        items: list[str] = []
        for component in row:
            if isinstance(component, ButtonSpec):
                emoji = f'<span class="emoji">{plain(component.emoji)}</span>' if component.emoji else ""
                label = plain(component.label)
                ext = ' <span class="ext">↗</span>' if component.style == "link" else ""
                title = (
                    html.escape(f"Opens {component.url}")
                    if component.style == "link"
                    else html.escape(_route_title(component.custom_id, "component", router))
                )
                disabled = " disabled" if component.disabled else ""
                items.append(
                    f'<button class="btn {component.style}{disabled}" title="{title}" '
                    f'data-route="{title}">{emoji}{label}{ext}</button>'
                )
            elif isinstance(component, SelectSpec):
                options = "".join(
                    f'<div class="opt{" default" if o.default else ""}">'
                    f"{f'<span class=emoji>{plain(o.emoji)}</span>' if o.emoji else ''}"
                    f'<div><div class="opt-label">{plain(o.label)}</div>'
                    f"{f'<div class=opt-desc>{plain(o.description)}</div>' if o.description else ''}</div></div>"
                    for o in component.options
                )
                title = html.escape(_route_title(component.custom_id, "component", router))
                placeholder = plain(component.placeholder) or "&nbsp;"
                items.append(
                    f'<details class="select" title="{title}"><summary data-route="{title}">'
                    f'<span>{placeholder}</span><span class="chev">⌄</span></summary>'
                    f'<div class="options">{options}</div></details>'
                )
        rows.append(f'<div class="row">{"".join(items)}</div>')
    return "".join(rows)


def _inline_attachments(spec: MessageSpec) -> tuple[EmbedSpec, ...]:
    """Embeds whose ``attachment://`` images point at the message's own files, as data URIs the page can show."""
    files = {f.filename: f for f in spec.files or ()}
    if not files:
        return spec.embeds

    def resolve(url: str | None) -> str | None:
        item = files.get(url[len("attachment://") :]) if url and url.startswith("attachment://") else None
        if item is None or item.data is None:
            return url
        encoded = base64.b64encode(item.data).decode("ascii")
        return f"data:{item.content_type or 'application/octet-stream'};base64,{encoded}"

    return tuple(
        replace(e, image_url=resolve(e.image_url), thumbnail_url=resolve(e.thumbnail_url)) for e in spec.embeds
    )


def render_message(spec: MessageSpec, router: Router) -> str:
    mention = f'<div class="msg-content">{_inline(html.escape(spec.mention_text))}</div>' if spec.mention_text else ""
    embeds = "".join(render_embed(e) for e in _inline_attachments(spec))
    return (
        '<div class="message"><div class="avatar">B</div><div class="msg-main">'
        '<div class="msg-head"><span class="bot-name">Bot</span><span class="bot-tag">APP</span>'
        '<span class="msg-time">Today</span></div>'
        f"{mention}{embeds}{render_components(spec, router)}</div></div>"
    )


def render_modal(spec: ModalSpec, router: Router) -> str:
    inputs: list[str] = []
    for item in spec.inputs:
        required = '<span class="req">*</span>' if item.required else ""
        description = f'<div class="input-desc">{plain(item.description)}</div>' if item.description else ""
        value = html.escape(item.default or "")
        placeholder = html.escape(item.placeholder or "")
        limits = []
        if item.min_length:
            limits.append(f"min {item.min_length}")
        if item.max_length:
            limits.append(f"max {item.max_length}")
        hint = f'<div class="input-hint">{", ".join(limits)} characters</div>' if limits else ""
        control = (
            f'<textarea placeholder="{placeholder}" rows="4">{value}</textarea>'
            if item.style == "paragraph"
            else f'<input placeholder="{placeholder}" value="{value}">'
        )
        if item.placeholder and _MARKER_RE.search(item.placeholder):
            control += f'<div class="input-desc">placeholder: {plain(item.placeholder)}</div>'
        inputs.append(
            f'<label class="input"><div class="input-label">{plain(item.label)}{required}</div>{description}{control}{hint}</label>'
        )
    title = html.escape(_route_title(spec.custom_id, "modal", router))
    return (
        f'<div class="modal-backdrop"><div class="modal" title="{title}">'
        f'<div class="modal-title">{plain(spec.title)}</div>{"".join(inputs)}'
        '<div class="modal-actions"><span class="modal-chrome">Discord-provided buttons:</span>'
        f'<button class="btn secondary">Cancel</button><button class="btn primary" data-route="{title}">Submit</button>'
        "</div></div></div>"
    )


@dataclass
class RenderedState:
    id: str
    feature: str
    name: str
    note: str
    kind: str
    html: str
    violations: list[str]
    missing: list[str]


def render_state(index: int, state: PreviewState, router: Router) -> RenderedState:
    if isinstance(state.spec, ModalSpec):
        body = render_modal(state.spec, router)
        violations = [str(v) for v in validate_modal(state.spec)]
        kind = "form"
        text_blob = json.dumps(
            [state.spec.title, *[(i.label, i.description, i.placeholder) for i in state.spec.inputs]]
        )
    else:
        body = render_message(state.spec, router)
        violations = [str(v) for v in validate_message(state.spec)]
        kind = "message"
        text_blob = json.dumps(
            [
                [e.title, e.description, e.footer_text, e.author_name, [(f.name, f.value) for f in e.fields]]
                for e in state.spec.embeds
            ]
            + [
                [
                    getattr(c, "label", None),
                    getattr(c, "placeholder", None),
                    [(o.label, o.description) for o in getattr(c, "options", ())],
                ]
                for row in state.spec.rows
                for c in row
            ],
            ensure_ascii=False,
        )
    missing = sorted(set(_MARKER_RE.findall(text_blob)))
    return RenderedState(f"s{index}", state.feature, state.name, state.note, kind, body, violations, missing)


# --------------------------------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------------------------------

CSS = """
:root{--bg:#313338;--bg2:#2b2d31;--bg3:#1e1f22;--text:#dbdee1;--muted:#949ba4;--head:#f2f3f5;--link:#00a8fc;
--blurple:#5865f2;--green:#248046;--red:#da373c;--grey:#4e5058;--warn:#f0b232;--ok:#23a55a}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--text);font:15px/1.375 "gg sans","Noto Sans","Helvetica Neue",Helvetica,Arial,sans-serif;
display:grid;grid-template-rows:auto 1fr;grid-template-columns:300px 1fr;grid-template-areas:"banner banner" "nav main"}
.banner{grid-area:banner;background:#4a3a0a;color:#ffe8a3;padding:10px 16px;font-size:13px;border-bottom:1px solid #6b5212}
.banner strong{color:#fff}
nav{grid-area:nav;background:var(--bg2);overflow-y:auto;padding:12px 8px;border-right:1px solid var(--bg3)}
nav h2{font-size:12px;text-transform:uppercase;letter-spacing:.02em;color:var(--muted);margin:14px 8px 6px}
nav a{display:flex;justify-content:space-between;gap:6px;color:var(--muted);text-decoration:none;padding:6px 8px;border-radius:4px;font-size:14px}
nav a:hover{background:#35373c;color:var(--text)}nav a.active{background:#404249;color:#fff}
nav .badge{font-size:11px;border-radius:8px;padding:0 6px;background:#3f4147;color:var(--muted);white-space:nowrap}
nav .badge.bad{background:#5c1e1e;color:#ffb3b3}nav .badge.miss{background:#4a3a0a;color:#ffe8a3}
main{grid-area:main;overflow-y:auto;padding:20px 24px 80px}
.overview table{border-collapse:collapse;margin-top:8px}.overview td,.overview th{padding:6px 12px;border-bottom:1px solid #3f4147;text-align:left}
.state{display:none}.state.active{display:block}
.state-head h1{font-size:20px;color:var(--head);margin:0 0 4px}.state-head .feat{color:var(--muted);font-size:13px}
.note{background:var(--bg2);border-left:3px solid var(--blurple);padding:8px 12px;margin:12px 0;border-radius:4px;color:var(--text)}
.checks{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 16px;font-size:13px}
.chip{padding:3px 8px;border-radius:10px;background:#1f3b2a;color:#9ff0bc}.chip.bad{background:#5c1e1e;color:#ffb3b3}
.chip.miss{background:#4a3a0a;color:#ffe8a3}
details.diag{margin:6px 0;font-size:13px;color:var(--muted)}details.diag li{font-family:ui-monospace,monospace}
.channel{background:var(--bg);border:1px solid var(--bg3);border-radius:8px;padding:16px 0;max-width:820px}
.message{display:flex;gap:16px;padding:2px 16px}.avatar{width:40px;height:40px;border-radius:50%;background:var(--blurple);
color:#fff;display:grid;place-items:center;font-weight:700;flex:none}.msg-main{min-width:0;flex:1}
.msg-head{display:flex;align-items:center;gap:6px}.bot-name{color:var(--head);font-weight:600}
.bot-tag{background:var(--blurple);color:#fff;font-size:10px;font-weight:600;padding:1px 4px;border-radius:3px}
.msg-time{color:var(--muted);font-size:12px}.msg-content{margin-top:2px}
.embed{background:var(--bg2);border-left:4px solid;border-radius:4px;max-width:520px;margin-top:6px;padding:8px 16px 16px 12px}
.embed-grid{display:flex;gap:16px}.embed-body{min-width:0;flex:1}
.embed-author{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:600;color:var(--head);margin-top:8px}
.author-icon,.footer-icon{width:20px;height:20px;border-radius:50%;object-fit:cover}
.embed-title{font-weight:600;color:var(--head);margin-top:8px}.embed-title a{color:var(--link)}
.embed-desc{font-size:14px;margin-top:8px;white-space:normal;overflow-wrap:anywhere}
.embed-fields{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.field{grid-column:1/-1;font-size:14px}.field.inline{grid-column:auto}.field-name{font-weight:600;color:var(--head);margin-bottom:2px}
.field-value{overflow-wrap:anywhere}
.embed-image{display:block;max-width:100%;max-height:300px;border-radius:4px;margin-top:16px;background:#1e1f22;min-height:40px}
.embed-thumb{width:80px;height:80px;object-fit:cover;border-radius:4px;margin-top:8px;background:#1e1f22}
.embed-footer{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px;margin-top:8px}
.row{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.btn{border:0;border-radius:3px;color:#fff;font:500 14px/16px inherit;padding:7px 16px;min-height:32px;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.btn.primary{background:var(--blurple)}.btn.secondary,.btn.link{background:var(--grey)}.btn.success{background:var(--green)}
.btn.danger{background:var(--red)}.btn.disabled{opacity:.5;cursor:not-allowed}.btn .ext{font-size:12px}
details.select{background:var(--bg3);border-radius:4px;width:100%;max-width:520px}
details.select summary{list-style:none;display:flex;justify-content:space-between;padding:10px 12px;cursor:pointer;color:var(--muted)}
details.select summary::-webkit-details-marker{display:none}
.options{border-top:1px solid #111214;max-height:280px;overflow:auto}.opt{display:flex;gap:8px;padding:8px 12px}
.opt:hover{background:#35373c}.opt.default{background:#404249}.opt-label{color:var(--text)}.opt-desc{color:var(--muted);font-size:13px}
.modal-backdrop{background:rgba(0,0,0,.55);padding:24px;border-radius:8px;max-width:820px}
.modal{background:var(--bg);border-radius:8px;max-width:460px;margin:0 auto;padding:20px;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.modal-title{font-size:20px;font-weight:600;color:var(--head);margin-bottom:16px}
.input{display:block;margin-bottom:14px}.input-label{font-size:13px;font-weight:600;color:var(--head);margin-bottom:4px}
.req{color:var(--red);margin-left:3px}.input-desc{font-size:12px;color:var(--muted);margin-bottom:6px}
.input-hint{font-size:11px;color:var(--muted);margin-top:4px}
.input input,.input textarea{width:100%;background:var(--bg3);border:0;border-radius:4px;color:var(--text);padding:10px;font:inherit;resize:vertical}
.modal-actions{display:flex;justify-content:flex-end;align-items:center;gap:8px;margin-top:8px}
.modal-chrome{color:var(--muted);font-size:11px;margin-right:auto}
code{background:#2b2d31;border-radius:3px;padding:0 3px;font-size:85%}pre{background:var(--bg3);border-radius:4px;padding:8px;overflow:auto}
pre code{background:none}a{color:var(--link)}blockquote{border-left:4px solid #4e5058;margin:2px 0;padding-left:10px}
.mention{background:rgba(88,101,242,.3);color:#c9cdfb;border-radius:3px;padding:0 2px}
.missing{background:#4a3a0a;color:#ffe8a3;border:1px dashed #f0b232;border-radius:3px;padding:0 3px;font-family:ui-monospace,monospace;font-size:90%}
.subtext{font-size:12px;color:var(--muted)}.md-h{margin:4px 0;color:var(--head)}ul{margin:2px 0;padding-left:20px}
.spoiler{background:#1e1f22;color:#1e1f22}.spoiler:hover{color:inherit}
.nav-buttons{display:flex;gap:8px;margin-top:20px}
#toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:#111214;color:#fff;padding:10px 14px;border-radius:6px;
font:13px ui-monospace,monospace;max-width:90vw;display:none;box-shadow:0 4px 12px rgba(0,0,0,.5)}
@media(max-width:800px){body{grid-template-columns:1fr;grid-template-areas:"banner" "nav" "main"}nav{max-height:40vh}}
"""

JS = """
const states=[...document.querySelectorAll('.state')];const links=[...document.querySelectorAll('nav a[data-target]')];
function show(id){states.forEach(s=>s.classList.toggle('active',s.id===id));links.forEach(a=>a.classList.toggle('active',a.dataset.target===id));
const el=document.getElementById(id);if(el)el.scrollIntoView({block:'start'});history.replaceState(null,'','#'+id);}
links.forEach(a=>a.addEventListener('click',e=>{e.preventDefault();show(a.dataset.target);}));
function step(d){const i=states.findIndex(s=>s.classList.contains('active'));const n=states[Math.min(states.length-1,Math.max(0,i+d))];if(n)show(n.id);}
document.addEventListener('keydown',e=>{if(e.key==='ArrowDown'||e.key==='j')step(1);if(e.key==='ArrowUp'||e.key==='k')step(-1);});
const toast=document.getElementById('toast');let tt;
document.addEventListener('click',e=>{const t=e.target.closest('[data-route]');if(!t||!t.dataset.route)return;
toast.textContent='Simulation only. '+t.dataset.route;toast.style.display='block';clearTimeout(tt);tt=setTimeout(()=>toast.style.display='none',4000);});
window.addEventListener('hashchange',()=>{const id=location.hash.slice(1)||'overview';if(!document.getElementById(id))return;states.forEach(s=>s.classList.toggle('active',s.id===id));links.forEach(a=>a.classList.toggle('active',a.dataset.target===id));});
show(location.hash?location.hash.slice(1):'overview');
"""


def build_html(states: list[RenderedState], *, generated: str, readiness_rows: list[tuple[str, str, str, int]]) -> str:
    by_feature: dict[str, list[RenderedState]] = {}
    for state in states:
        by_feature.setdefault(state.feature, []).append(state)
    nav = ['<a href="#overview" data-target="overview"><span>Overview</span></a>']
    for feature, items in by_feature.items():
        nav.append(f"<h2>{html.escape(feature)}</h2>")
        for state in items:
            badge = ""
            if state.violations:
                badge = f'<span class="badge bad">{len(state.violations)} limit</span>'
            elif state.missing:
                badge = f'<span class="badge miss">{len(state.missing)} text</span>'
            nav.append(
                f'<a href="#{state.id}" data-target="{state.id}"><span>{html.escape(state.name)}</span>{badge}</a>'
            )
    total_violations = sum(len(s.violations) for s in states)
    missing_keys = sorted({k for s in states for k in s.missing})
    rows = "".join(
        f"<tr><td>{html.escape(n)}</td><td>{e}</td><td>{r}</td><td>{p}</td></tr>" for n, e, r, p in readiness_rows
    )
    overview = (
        '<section class="state overview" id="overview"><div class="state-head"><h1>Preview overview</h1>'
        f'<div class="feat">Generated {html.escape(generated)}</div></div>'
        '<div class="note">This page is a <strong>local simulation</strong> built from the same view code and message '
        "specifications the bot sends to Discord. It is not a Discord server; clicking here proves nothing about "
        "Discord behaviour. Hover or click a control to see which handler would receive it. "
        "Highlighted <span class='missing'>⟦key⟧</span> text is not configured yet - the bot never shows such a "
        "message to customers. Sample data in brackets is synthetic.</div>"
        f'<div class="checks"><span class="chip">{len(states)} states</span>'
        f'<span class="chip{" bad" if total_violations else ""}">{total_violations} limit violations</span>'
        f'<span class="chip{" miss" if missing_keys else ""}">{len(missing_keys)} unconfigured keys shown</span></div>'
        "<table><tr><th>Feature</th><th>Enabled in settings</th><th>Ready</th><th>Open problems</th></tr>"
        f"{rows}</table>"
        '<p class="feat">Keys: arrow up/down to move between states.</p></section>'
    )
    sections = [overview]
    for state in states:
        chips = (
            f'<span class="chip bad">{len(state.violations)} Discord limit violations</span>'
            if state.violations
            else '<span class="chip">Within Discord limits</span>'
        )
        if state.missing:
            chips += f'<span class="chip miss">{len(state.missing)} keys not configured</span>'
        diag = ""
        if state.violations:
            diag += (
                "<details class='diag' open><summary>Limit violations</summary><ul>"
                + "".join(f"<li>{html.escape(v)}</li>" for v in state.violations)
                + "</ul></details>"
            )
        if state.missing:
            diag += (
                "<details class='diag'><summary>Unconfigured content keys</summary><ul>"
                + "".join(f"<li>{html.escape(k)}</li>" for k in state.missing)
                + "</ul></details>"
            )
        container = state.html if state.kind == "form" else f'<div class="channel">{state.html}</div>'
        sections.append(
            f'<section class="state" id="{state.id}"><div class="state-head"><div class="feat">'
            f"{html.escape(state.feature)} · {state.kind}</div><h1>{html.escape(state.name)}</h1></div>"
            f'<div class="note">{html.escape(state.note)}</div><div class="checks">{chips}</div>{diag}{container}'
            '<div class="nav-buttons"><button class="btn secondary" onclick="step(-1)">Previous state</button>'
            '<button class="btn secondary" onclick="step(1)">Next state</button></div></section>'
        )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Bot UI Preview</title><style>" + CSS + "</style></head><body>"
        "<div class='banner'><strong>LOCAL SIMULATION</strong> — rendered offline from the bot's real view code. "
        "Not a functioning Discord server; nothing here is sent anywhere.</div>"
        f"<nav>{''.join(nav)}</nav><main>{''.join(sections)}</main><div id='toast'></div>"
        "<script>" + JS + "</script></body></html>"
    )


async def collect(
    config_dir: Path, *, enabled_only: bool
) -> tuple[list[RenderedState], list[tuple[str, str, str, int]]]:
    app = await build_offline_app(config_dir, content_mode="markers", all_features=not enabled_only)
    real = await build_offline_app(config_dir, content_mode="actual", all_features=False)
    try:
        rendered: list[RenderedState] = []
        index = 0
        for module in app.modules:
            if module.preview is None or (enabled_only and not app.readiness.is_enabled(module.name)):
                continue
            try:
                states = module.preview(app)
            except Exception as exc:
                note = f"Preview for this feature failed to build: {type(exc).__name__}: {exc}"
                states = [PreviewState(module.name, "(preview error)", note, MessageSpec())]
            for state in states:
                rendered.append(render_state(index, state, app.router))
                index += 1
        readiness_rows = [
            (s.name, "yes" if s.enabled else "no", "yes" if s.ready else "no", len(s.problems))
            for s in real.readiness.statuses.values()
        ]
        return rendered, readiness_rows
    finally:
        await app.db.close()
        await real.db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--enabled-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        states, readiness_rows = asyncio.run(collect(args.config_dir, enabled_only=args.enabled_only))
    except (SettingsError, ContentFileError) as exc:
        print("Configuration cannot be loaded:")
        for error in exc.errors:
            print(f"  - {error}")
        return 2
    generated = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_html(states, generated=generated, readiness_rows=readiness_rows), encoding="utf-8")
    violations = sum(len(s.violations) for s in states)
    print(f"Wrote {args.out} ({len(states)} states, {violations} limit violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
