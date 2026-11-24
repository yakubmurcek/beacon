#!/usr/bin/env python3
"""Every tailnet name on every box, checked: the list behind Names on http://beacon/machines (task 1803).

Runs on the home server as the tailnet-check timer: that box holds TAILSCALE_API_KEY
and can open every name over the tailnet, the hub's server can't (its grants reach only its own names).

Sources, one per field:
  - names, comments, which devices host each and whether it is set up there: the Tailscale API
    (vip-services, vip-services/<name>/devices, devices);
  - what each box serves and whether the app behind answers, and when it was last used: the hub's
    /api/machines `serve` block, sampled by each collector (collector/host.py serve());
  - reachable now: the collector's probe of the app behind it, where a collector serves the name (set up
    there per the API), else an HTTP GET of http://<name>/ from here, the way his phone opens it;
  - the policy's autoApprovers: the live policy from the API (not the repo copy).

Plain rules, no model. Each run files at most MAX_ASK questions on /decisions (a short stable source
per card; state['asked'] maps it to the question, so a "keep" stays kept and a withdrawn card can come
back); a card no run carries for 3 runs is withdrawn. Nothing is ever removed here. The question carries
the exact removal steps, for the session his answer starts.

  tailnet_check.py            check, POST the list to the hub, file questions
  tailnet_check.py --dry      print what it would post and ask, change nothing
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = 'https://api.tailscale.com/api/v2/tailnet/-'
HUB = os.environ.get('HUB_URL', 'http://beacon')
STATE = os.environ.get('TAILNET_CHECK_STATE', '/var/lib/tailnet-check/state.json')
DOWN_SEC = 1800          # down this long is a question, not a blip
IDLE_SEC = 14 * 86400    # a preview nobody opened this long is stale
MAX_ASK = 5              # questions per run, at most
PROJECT = 'beacon'


# ---------------------------------------------------------------- I/O

def secret(name):
    v = os.environ.get(name)
    if v:
        return v
    return subprocess.run(['secrets', 'get', name], capture_output=True, text=True, check=True).stdout.strip()


def api(path, key, accept='application/json'):
    req = urllib.request.Request(API + path, headers={'Accept': accept})
    req.add_header('Authorization', 'Basic ' + __import__('base64').b64encode(f'{key}:'.encode()).decode())
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def hub(path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HUB + path, data=data, method='POST' if data else 'GET',
                                 headers={'Content-Type': 'application/json'})
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def probe(name):
    """True when http://<name>/ answers like a working app (any status but a gateway error)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(f'http://{name}/', headers={'Connection': 'close'}),
                                    timeout=6):
            return True
    except urllib.error.HTTPError as e:
        return e.code not in (502, 503, 504)
    except Exception:
        return False


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'names': {}}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


# ---------------------------------------------------------------- the list

def short(svc):
    return svc.removeprefix('svc:')


def build(services, hosts, devices, policy, machines, probes, state, now, kept=()):
    """(names, flags). services: API vip-services; hosts: {svc: [{nodeId, configured}]}; devices:
    {nodeId: machine}; policy: [svc, ...] from autoApprovers; machines: the hub's /api/machines;
    probes: {short name: reachable}; state: {names: {short: {first_seen, last_used, down_since}}}."""
    served = {}  # svc -> [{machine, backends, up, last_used}]
    for m in machines.get('machines') or []:
        for s in ((m.get('serve') or {}).get('names') or []):
            served.setdefault(s['name'], []).append(dict(s, machine=m['machine']))
    seen = state.setdefault('names', {})
    # A box that is switched off (he turned it off): its names are not broken, nothing to ask (task 1821).
    off = {m['machine'] for m in machines.get('machines') or []
           if (m.get('tailscale') or {}).get('online') is False or m.get('state') in ('asleep', 'offline')}
    names = []
    for svc in sorted(services, key=lambda s: s['name']):
        n = short(svc['name'])
        comment = svc.get('comment') or ''
        preview = comment.startswith('preview: ')
        on = [{'machine': devices.get(h.get('nodeId'), h.get('nodeId')),
               'ready': (h.get('configured') or '').startswith('ready')} for h in hosts.get(svc['name']) or []]
        by = served.get(svc['name']) or []
        st = seen.setdefault(n, {'first_seen': now})
        # The box's own count wins while it reports (it can correct itself); ours only bridges its absence.
        used = max([s.get('last_used') or 0 for s in by]) or None if by else st.get('last_used')
        st['last_used'] = used
        ups = [s.get('up') for s in by if s.get('up') is not None]
        up = any(ups) if ups else None  # None: no box could tell (a TCP-only name), so it gets probed
        # A box with a collector says itself whether the app answers; a probe over the tailnet would
        # read as a visit there (last used). Only names no collector can judge get probed.
        reachable = probes.get(n) if n in probes else bool(up and any(h['ready'] for h in on))
        box_off = not reachable and any(h['ready'] for h in on) and all(
            h['machine'] in off for h in on if h['ready'])
        if reachable or box_off:
            st.pop('down_since', None)  # back on and still down: a fresh wait before asking
        else:
            st.setdefault('down_since', now)
        if box_off:
            state_, why = 'off', f'{" and ".join(sorted(h["machine"] for h in on if h["ready"]))} is switched off'
        elif not any(h['ready'] for h in on) and not reachable:
            state_, why = 'dead', 'no box serves it any more'
        elif not reachable:
            state_, why = 'down', ('the app behind it is not answering' if up is False else 'it does not open')
        elif by and all(s.get('page') is False for s in by):
            state_, why = 'api', 'works, but has no web page: an API for an app'
        elif preview and now - (used or st['first_seen']) > IDLE_SEC:
            state_, why = 'idle', 'nobody opened it for two weeks'
        else:
            state_, why = 'ok', None
        names.append({
            'name': n, 'kind': 'preview' if preview else 'app',
            'what': (f'preview of {os.path.basename(comment[9:].rstrip("/"))}' if preview else
                     next((x['what'] for x in by if x.get('what')), None) or comment or None),
            'hosts': [h['machine'] for h in on], 'ready_on': [h['machine'] for h in on if h['ready']],
            'served_by': [{'machine': s['machine'], 'backends': s.get('backends') or [], 'up': s.get('up'), 'page': s.get('page')} for s in by],
            'reachable': reachable, 'last_used': used, 'used_known': any((m.get('serve') or {}).get('sampled')
                                                                        for m in machines.get('machines') or []
                                                                        if m['machine'] in [s['machine'] for s in by]),
            'in_policy': svc['name'] in policy, 'state': state_, 'why': why,
            'down_since': st.get('down_since'), 'first_seen': st['first_seen'],
        })
    defined = {s['name'] for s in services}
    for svc in sorted(set(policy) - defined):
        names.append({'name': short(svc), 'kind': 'policy', 'what': 'only a line in the access policy',
                      'hosts': [], 'ready_on': [], 'served_by': [], 'reachable': False, 'last_used': None,
                      'used_known': False, 'in_policy': True, 'state': 'dead', 'why': 'in the policy, no service',
                      'down_since': None, 'first_seen': None})
    for n in [n for n in seen if n not in {x['name'] for x in names}]:
        del seen[n]
    return names, flags(names, now, set(kept))


def key(kind, *parts):
    """A short stable question source: the hub caps it at 200 chars and a group's name list grows."""
    return f'tailnet:{kind}:' + hashlib.sha1('|'.join(parts).encode()).hexdigest()[:12]


def flags(names, now, kept=frozenset()):
    """What he should decide on, grouped so the deck gets a few cards, not one per name. kept: names
    (and name@box pairs) he already said to keep, left out of the grouped cards."""
    out = []
    backends = {}
    for x in names:
        for s in x['served_by']:
            for b in s['backends']:
                backends.setdefault((s['machine'], b), []).append(x['name'])
    for (machine, b), group in sorted(backends.items()):
        if len(group) > 1:
            g = sorted(set(group))
            if len(g) > 1:
                out.append({'kind': 'duplicate', 'names': g, 'source': key('dup', machine, b, *g),
                            'text': f'{" and ".join("http://" + n for n in g)} open the same app on {machine}'})
    for x in names:
        if len(x['ready_on']) > 1:
            out.append({'kind': 'two-boxes', 'names': [x['name']],
                        'source': key('two', x['name'], *sorted(x['ready_on'])),
                        'text': f'http://{x["name"]} is served by both {" and ".join(sorted(x["ready_on"]))}'})
    left = sorted(f'{x["name"]}@{h}' for x in names if x['ready_on'] for h in x['hosts']
                  if h not in x['ready_on'] and f'{x["name"]}@{h}' not in kept)
    if left:
        out.append({'kind': 'leftover', 'names': sorted({p.split('@')[0] for p in left}), 'pairs': left,
                    'source': key('left', *left),
                    'text': f'{len(left)} old approval{"s" * (len(left) > 1)} for a box that no longer serves the name ('
                            + ', '.join(p.replace('@', ' on ') for p in left) + ')'})
    groups = [('dead', 'preview', 'old preview links no longer open anything', 'an old preview link opens nothing'),
              ('dead', 'app', 'app links no longer open anything', 'this app link opens nothing'),
              ('dead', 'policy', 'names sit in the access policy with no service behind them',
               'this name sits in the access policy with no service behind it'),
              ('idle', 'preview', 'preview links nobody opened for two weeks', 'nobody opened this preview for two weeks')]
    for state_, kind, what, one in groups:
        g = sorted(x['name'] for x in names if x['state'] == state_ and x['kind'] == kind and x['name'] not in kept)
        if g:
            out.append({'kind': f'{state_}-{kind}', 'names': g, 'source': key(f'{state_}-{kind}', *g),
                        'text': f'{len(g)} {what}' if len(g) > 1 else f'http://{g[0]}: {one}'})
    for x in names:
        if x['state'] == 'down' and x['down_since'] and now - x['down_since'] >= DOWN_SEC:
            out.append({'kind': 'down', 'names': [x['name']],
                        'source': key('down', x['name'], str(int(x['down_since']))),
                        'text': f'http://{x["name"]} is down ({x["why"]})'})
    return out


def removal_steps(f, names):
    by = {x['name']: x for x in names}
    lines = []
    if f['kind'] == 'leftover':
        return '\n'.join(f'- `{p.split("@")[0]}` on {p.split("@")[1]}: `tailscale serve clear svc:{p.split("@")[0]}` there '
                         f'(if any), then POST {API}/vip-services/svc:{p.split("@")[0]}/device/<its nodeId>/approved '
                         '`{"approved":false}`.' for p in f['pairs'])
    for n in f['names']:
        x = by.get(n) or {}
        if x.get('kind') == 'policy':
            lines.append(f'- `{n}`: delete `svc:{n}` from linux-setup `hosts/tailscale/policy.hujson`, PR, then push '
                         'the policy (POST api/v2/tailnet/-/acl with If-Match etag).')
            continue
        hosts = ', '.join(x.get('hosts') or []) or 'no box'
        step = f'- `{n}` ({x.get("kind")}, on {hosts}): '
        if x.get('kind') == 'preview':
            step += f'`preview stop {n}` on its box, '
        step += (f'`tailscale serve clear svc:{n}` on each box, then '
                 f'`curl -X DELETE -u "$(secrets get TAILSCALE_API_KEY):" {API}/vip-services/svc:{n}`'  # gitleaks:allow (a command that reads the key, no key)
                 + ('; also drop it from policy.hujson autoApprovers' if x.get('in_policy') else '') + '.')
        lines.append(step)
    return '\n'.join(lines)


def cap(t):
    return t if t.startswith('http') else t[0].upper() + t[1:]


def question(f, names):
    by = {x['name']: x for x in names}
    listed = '\n'.join(f'- http://{n}: {by[n]["what"] or "no note"}, {by[n]["why"] or "opens fine"}'
                       for n in f['names'] if n in by)
    if f['kind'] == 'leftover':
        q = f'{cap(f["text"])}. Drop them?'
        opts = [{'label': 'Drop them', 'detail': 'the names keep working from the box that serves them'},
                {'label': 'Keep', 'detail': 'this is not asked again'}]
    elif f['kind'] in ('duplicate', 'two-boxes'):
        q = f'{cap(f["text"])}. Keep one name?'
        opts = [{'label': 'Keep one, remove the rest', 'detail': 'an agent removes the extra name, the app stays'},
                {'label': 'Keep both', 'detail': 'on purpose; this is not asked again'}]
    elif f['kind'] == 'down':
        q = f'{cap(f["text"])}. Fix it or remove the name?'
        opts = [{'label': 'Fix it', 'detail': 'an agent brings the app back'},
                {'label': 'Remove the name', 'detail': 'the app is no longer needed'}]
    else:
        q = f'{cap(f["text"])}. Remove them?' if len(f['names']) > 1 else \
            f'{cap(f["text"])}. Remove it?'
        opts = [{'label': 'Remove', 'detail': 'an agent deletes the names; nothing else changes'},
                {'label': 'Keep', 'detail': 'they stay; this is not asked again'}]
    # Below `---` is for the agent: the decision card folds it away (qcards.js).
    context = (f'{listed}\n\n🔎 Found by the tailnet names check (http://beacon/machines, Names).\n\n---\n'
               f'**How to remove** (only after his answer):\n{removal_steps(f, names)}')
    return {'question': q, 'options': opts, 'default': opts[1]['label'] if f['kind'] != 'down' else opts[0]['label'],
            'context': context, 'kind': 'cleanup', 'project': PROJECT, 'source': f['source']}


KEEPS = ('Keep', 'Keep both')


def kept_names(state, answers):
    """Names (and name@box pairs) he answered plain "Keep" for on a dead, unused or leftover card.
    answers: {question id: answer}. "Keep one, remove the rest" is no keep."""
    out = set()
    for rec in (state.get('asked') or {}).values():
        if answers.get(rec.get('qid')) in KEEPS and rec.get('kind', '').startswith(('dead-', 'idle-', 'leftover')):
            out.update(rec.get('pairs') or rec.get('names') or [])
    return out


def stale(open_qs, found, state, runs=3):
    """Open check questions no flag of this run carries for `runs` runs in a row (replaced by a bigger
    group, or no longer true). The wait keeps one bad run from withdrawing a card he is about to answer."""
    current = {f.get('question_id') for f in found}
    miss = state.setdefault('missing', {})
    out = []
    for q in open_qs:
        k = str(q['id'])
        if q['id'] in current:
            miss.pop(k, None)
            continue
        miss[k] = miss.get(k, 0) + 1
        if miss[k] >= runs:
            out.append(q)
            del miss[k]
    for k in [k for k in miss if int(k) not in {q['id'] for q in open_qs}]:
        del miss[k]
    return out


def ask(found, names, state, open_ids, answers, post, now):
    """Link each flag to its open question, mark the decided ones, ask the new ones (MAX_ASK a run).
    A source the hub already holds as withdrawn (the group came back) is asked again under a new one."""
    asked_map = state.setdefault('asked', {})
    asked = 0
    for f in found:
        rec = asked_map.get(f['source'])
        if rec and rec['qid'] in open_ids:
            f['question_id'] = rec['qid']
            continue
        if rec and rec['qid'] in answers:
            f['question_id'], f['decided'] = rec['qid'], answers[rec['qid']]
            continue
        if asked >= MAX_ASK:
            continue
        body = question(f, names)
        if rec:  # its card was withdrawn: a fresh source, or the hub hands back the withdrawn one
            body['source'] = f'{f["source"]}:{int(now)}'
        try:
            q = post(body)
            if q.get('status') == 'withdrawn':
                q = post(dict(body, source=f'{body["source"]}:{int(now)}'))
        except Exception as e:  # one refused card must not stop the list from reaching the page
            print(f'tailnet-check: could not ask "{body["question"][:60]}": {e}', file=sys.stderr)
            continue
        asked += 1
        asked_map[f['source']] = {'qid': q.get('id'), 'kind': f['kind'], 'names': f['names'], 'pairs': f.get('pairs')}
        f['question_id'] = q.get('id')
        if q.get('status') == 'answered':
            f['decided'] = q.get('answer')
    return asked


# ---------------------------------------------------------------- run

def collect():
    key = secret('TAILSCALE_API_KEY')
    services = api('/vip-services', key).get('vipServices') or []
    hosts = {s['name']: api(f'/vip-services/{s["name"]}/devices', key).get('hosts') or [] for s in services}
    devices = {d['nodeId']: d['name'].split('.')[0] for d in api('/devices', key).get('devices') or []}
    policy = list((api('/acl', key).get('autoApprovers') or {}).get('services') or {})
    machines = hub('/api/machines')
    judged = {s['name'] for m in machines.get('machines') or [] for s in ((m.get('serve') or {}).get('names') or [])
              if s.get('up') is not None}
    todo = [short(s['name']) for s in services if s['name'] not in judged]
    with ThreadPoolExecutor(8) as pool:
        probes = dict(zip(todo, pool.map(probe, todo)))
    return services, hosts, devices, policy, machines, probes


def main(dry=False):
    now = time.time()
    token = None if dry else secret('TASKS_TOKEN')
    try:
        services, hosts, devices, policy, machines, probes = collect()
    except Exception as e:  # say why on the page (keeping the last list), and fail so the unit alarms
        msg = f'{type(e).__name__}: {e}'[:300]
        print(f'tailnet-check: {msg}', file=sys.stderr)
        if not dry:
            old = hub('/api/names')
            hub('/api/names', dict(old, names=old.get('names') or [], error=msg, error_at=now), token)
        sys.exit(1)
    state = load_state()
    mine = lambda st: [q for q in hub(f'/api/questions?status={st}&project={PROJECT}').get('questions') or []
                       if (q.get('source') or '').startswith('tailnet:')]
    open_qs = [] if dry else mine('open')
    answers = {} if dry else {q['id']: q.get('answer') for q in mine('answered')}
    names, found = build(services, hosts, devices, policy, machines, probes, state, now, kept_names(state, answers))
    if dry:
        print(json.dumps({'names': names, 'flags': found, 'questions': [question(f, names) for f in found]}, indent=1))
        return
    asked = ask(found, names, state, {q['id'] for q in open_qs}, answers,
                lambda body: hub('/api/questions', body, token), now)
    for q in stale(open_qs, found, state):  # its decision task is dropped with it
        try:
            hub(f'/api/questions/{q["id"]}/withdraw', {'note': 'replaced by a newer card, or no longer true'}, token)
        except Exception as e:
            print(f'tailnet-check: withdraw failed: {e}', file=sys.stderr)
    save_state(state)
    hub('/api/names', {'at': now, 'names': names, 'flags': found}, token)
    print(f'tailnet-check: {len(names)} names, {len(found)} flags, {asked} asked', file=sys.stderr)


if __name__ == '__main__':
    main('--dry' in sys.argv)
