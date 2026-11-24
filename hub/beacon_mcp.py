"""Beacon as an app inside Claude and ChatGPT (MCP Apps), next to its own Labs page.

The bet being tested (2026-11-22): apps end up living inside a few assistant platforms instead of their own UI. So
the assistant's own model talks to him and decides what to show; Beacon only serves the live numbers and draws its
cards in the chat. Same engine as Labs (hub/labs.py: the data sources, the card catalog, `resolve`), same card
drawing code (sliced from web/labs.js and web/labs.css at read time, so the cards look the same everywhere).

Wire format: MCP over streamable HTTP, stateless, one JSON response per POST (no sessions, no SSE stream).
- tools: `beacon_numbers` (every source's current numbers, compact) and `beacon_show` (draw cards; carries
  `_meta.ui.resourceUri`, so the host renders the view below with the tool result).
- resource `ui://beacon/cards-v1`: one HTML page (mimeType `text/html;profile=mcp-app`) speaking the MCP Apps
  postMessage protocol by hand (ui/initialize, tool-result, size-changed); no outside scripts, so the default CSP holds.
Read-only: nothing here writes, and nothing reads mail or chats. Who may call it is the server's job (his login or
the token, like Knowledge).
"""

import os
import re

from labs import Labs
from settings import OWNER_NAME

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web')
UI_URI = 'ui://beacon/cards-v1'  # a new name whenever the page changes: hosts cache it
UI_MIME = 'text/html;profile=mcp-app'
VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
DRAWN = ('number', 'bars', 'line', 'list', 'progress', 'alert', 'suggest')

INSTRUCTIONS = (
    f"Beacon is {OWNER_NAME}'s own hub for his one-person AI studio: agents build games, apps and client sites on his "
    "machines. Answer his questions about it from beacon_numbers, then draw the answer with beacon_show: one short "
    "plain sentence from you, the cards show the proof. Plain words, no ids. 'Work value' is agent work at API list "
    "price; he pays a flat subscription, so never call it spending.")


def source_lines():
    return '\n'.join(f'- {name}: {desc}' for name, (desc, _) in Labs.SOURCES.items())


CARDS_HELP = f'''Cards, drawn in this order. Bind a card to a live source whenever one holds the numbers, so it stays
current; literal values only for what no source has.
- {{"type":"number","title":"…","source":"…","days":7}}  big number (a series shows its total vs the period before)
  literal: {{"type":"number","title":"…","value":12,"unit":"$|Kč|h|…","note":"…"}}
- {{"type":"bars","title":"…","source":"…","days":7}}  a bar per day or per item, the value written on each bar
  literal: {{"type":"bars","title":"…","unit":"…","items":[{{"label":"…","value":3}}]}}
- {{"type":"line","title":"…","source":"…","days":30}}  a trend over days, peak and today written on it
- {{"type":"list","title":"…","source":"…","limit":5}}  short rows; literal items [{{"title":"…","sub":"…","tone":"good|warn|bad"}}]
- {{"type":"progress","title":"…","value":3,"max":10,"label":"…"}}
- {{"type":"alert","tone":"bad|warn|good","text":"…"}}  only for something real he must not miss
- {{"type":"suggest","items":["…","…"]}}  2-3 follow-up questions he can tap, last
"size":"half" puts two cards side by side (use in pairs). 2-6 cards. "days" only for day-based sources (1-90).
Sources:
{source_lines()}'''

TOOLS = [
    {
        'name': 'beacon_numbers',
        'title': 'Beacon numbers',
        'description': (f"Live numbers from {OWNER_NAME}'s hub Beacon: agent work value and hours per day and project, new "
                        "real users, money earned, machines and their problems, decisions waiting on him, tasks, his "
                        "AI rank, background AI jobs. Call it first, then draw the answer with beacon_show."),
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'beacon_show',
        'title': 'Show Beacon cards',
        'description': 'Draw the answer as Beacon cards in the chat (big numbers, bars, trends, lists, alerts).\n'
                       + CARDS_HELP,
        'inputSchema': {
            'type': 'object',
            'properties': {
                'title': {'type': 'string', 'description': 'two to four words naming the view'},
                'cards': {'type': 'array', 'items': {'type': 'object'}, 'minItems': 1, 'maxItems': 8},
            },
            'required': ['cards'],
        },
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
        '_meta': {'ui': {'resourceUri': UI_URI}, 'openai/outputTemplate': UI_URI,
                  'openai/toolInvocation/invoking': 'Drawing…', 'openai/toolInvocation/invoked': 'Drawn'},
    },
]


def slice_between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


def ui_html():
    """The view: Labs' own card CSS and drawing code, with the MCP Apps handshake around it."""
    with open(os.path.join(WEB, 'tokens.css'), encoding='utf-8') as f:
        tokens = re.search(r'^:root \{(.*?)\n\}', f.read(), re.S | re.M).group(1)  # the Paper theme
    with open(os.path.join(WEB, 'labs.css'), encoding='utf-8') as f:
        css = slice_between(f.read(), '/* the cards */', '/* the composer')
    with open(os.path.join(WEB, 'labs.js'), encoding='utf-8') as f:
        js = slice_between(f.read(), '  function el(tag', '  // ------------------------------------------------------------ one answer')
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mcp_ui.html'), encoding='utf-8') as f:
        page = f.read()
    return page.replace('/*TOKENS*/', tokens).replace('/*CARDS_CSS*/', css).replace('//CARDS_JS', js)


def short(v):
    if isinstance(v, float):
        return f'{v:,.0f}' if abs(v) >= 100 else f'{v:g}'
    return str(v)


def summary(cards):
    """What the model reads back: each card's headline number, so its sentence matches the picture."""
    out = []
    for c in cards:
        d, t = c.get('data') or {}, c.get('title') or c['type']
        if d.get('kind') == 'series':
            out.append(f"{t}: {short(d.get('total'))} {d.get('unit', '')} ({d.get('span', '')}; before: {short(d.get('prev'))})")
        elif d.get('kind') == 'items':
            heads = '; '.join(x.get('title', '') for x in d.get('items', [])[:4])
            out.append(f"{t}: {d.get('count')} — {heads}")
        elif d.get('kind') == 'breakdown':
            top = ', '.join(f"{x['label']} {short(x['value'])}" for x in d.get('items', [])[:4])
            out.append(f"{t}: {top} {d.get('unit', '')}")
        elif d.get('kind') == 'metric':
            out.append(f"{t}: {d.get('value')} (next: {d.get('next')})")
        elif c['type'] == 'alert':
            out.append(f"alert: {c.get('text')}")
        elif c['type'] != 'suggest':
            out.append(f"{t}: {short(c.get('value', ''))} {c.get('unit', '')}".strip())
    return out


class BeaconMCP:
    def __init__(self, labs):
        self.labs = labs

    def handle(self, msg):
        """One JSON-RPC message in, its response out (None for a notification)."""
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or not isinstance(msg.get('method'), str):
            return {'jsonrpc': '2.0', 'id': msg.get('id') if isinstance(msg, dict) else None,
                    'error': {'code': -32600, 'message': 'invalid request'}}
        mid, method, params = msg.get('id'), msg['method'], msg.get('params') or {}
        if mid is None:
            return None  # notifications/initialized, cancelled …: nothing to answer
        try:
            result = self.dispatch(method, params)
        except LookupError as e:
            return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601 if e.args[0] == 'method' else -32602,
                                                          'message': e.args[1]}}
        return {'jsonrpc': '2.0', 'id': mid, 'result': result}

    def dispatch(self, method, params):
        if method == 'initialize':
            asked = params.get('protocolVersion')
            return {'protocolVersion': asked if asked in VERSIONS else VERSIONS[0],
                    'capabilities': {'tools': {'listChanged': False}, 'resources': {'listChanged': False},
                                     'extensions': {'io.modelcontextprotocol/ui': {'mimeTypes': [UI_MIME]}}},
                    'serverInfo': {'name': 'beacon', 'title': 'Beacon', 'version': '1.0.0'},
                    'instructions': INSTRUCTIONS}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': TOOLS}
        if method == 'tools/call':
            return self.call(params.get('name'), params.get('arguments') or {})
        if method == 'resources/list':
            return {'resources': [{'uri': UI_URI, 'name': 'beacon-cards', 'title': 'Beacon cards', 'mimeType': UI_MIME}]}
        if method == 'resources/templates/list':
            return {'resourceTemplates': []}
        if method == 'resources/read':
            if params.get('uri') != UI_URI:
                raise LookupError('params', 'unknown resource')
            return {'contents': [{'uri': UI_URI, 'mimeType': UI_MIME, 'text': ui_html(),
                                  '_meta': {'ui': {'prefersBorder': False}}}]}
        if method == 'prompts/list':
            return {'prompts': []}
        raise LookupError('method', f'unknown method {method}')

    def call(self, name, args):
        if name == 'beacon_numbers':
            text = f'Today is {self.labs.store.today()} (Europe/Prague).\n' + self.labs.digest()
            return {'content': [{'type': 'text', 'text': text}]}
        if name == 'beacon_show':
            raw = args.get('cards') if isinstance(args.get('cards'), list) else []
            cards = [c for c in (Labs.clean_card(x) for x in raw[:8]) if c and c['type'] in DRAWN]
            if not cards:
                return {'isError': True, 'content': [{'type': 'text', 'text': 'No drawable cards: use the types and '
                                                      'sources in the tool description.'}]}
            cards = self.labs.resolve(cards)
            title = str(args.get('title') or 'Beacon')[:60]
            lines = summary(cards)
            return {'content': [{'type': 'text', 'text': f'Drew "{title}" for him:\n' + '\n'.join(lines)}],
                    'structuredContent': {'title': title, 'cards': cards}}
        raise LookupError('params', f'unknown tool {name}')
