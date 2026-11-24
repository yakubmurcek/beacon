"""Endings (task 1857): every session's last reply is one card in the feed on http://beacon/decisions.

The owner runs dozens of sessions an hour and never opens them to learn what they did (2026-11-22). The Stop hook
(`tasks hook-stop`) posts each reply: its first line (✅ done / ⚠️ stuck / ❓ needs you, his reply format), the
suggested next step and its buttons. One card per session: a later reply replaces it.

- needs  (❓): a card in the decision deck, the one pile he looks at (2026-11-23, ?255). The hub files it as an open
          decision of kind `ending` (`card`): the reply's own line is the question, its buttons are the session's own;
          a decision the session files itself replaces it, and the session's next reply withdraws it.
- stuck  (⚠️, "not yet + why"): mostly a status, so a deck card only when the session gave buttons to pick from;
          otherwise it sits in the log with the finished ones. A card with no question on it read as noise (2026-11-23).
- done   (✅ or no mark): one-line result, the next step as a button, "Got it"; off the deck, under "Finished".

A button tap (`act`) becomes an answered decision of kind `ending` on that session, so answer-dispatcher carries
it into the session the same way it carries his decision answers.

The nightly backstop (client/endings-backstop on the home server) asks `missed()` for sessions whose turn ended (seen in
the machines' live snapshots) with no ending posted, and posts those it reads with one Haiku call.
"""
import hashlib
import json
import re
import threading
import time
import traceback

import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS endings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL UNIQUE,  -- "Claude Code:<uuid>"
  agent TEXT,
  machine TEXT,
  project TEXT,
  title TEXT,
  state TEXT NOT NULL,              -- needs | stuck | done
  line TEXT NOT NULL,               -- the reply's first line, without its mark
  next TEXT,                        -- the suggested next step (the reply's last line)
  actions TEXT NOT NULL DEFAULT '[]',
  reply TEXT,                       -- the reply's start, for the run a tap starts
  cwd TEXT,
  source TEXT NOT NULL DEFAULT 'hook',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  cleared_at REAL,
  acted TEXT,                       -- the button he tapped
  question_id INTEGER               -- the decision that tap became
);
CREATE INDEX IF NOT EXISTS endings_open ON endings(cleared_at, updated_at);
CREATE TABLE IF NOT EXISTS ending_seen (
  session_id TEXT PRIMARY KEY,
  agent TEXT,
  machine TEXT,
  project TEXT,
  title TEXT,
  line TEXT,
  last REAL NOT NULL               -- when its turn ended (the live snapshot's last log row)
);
"""

STATES = ('needs', 'stuck', 'done')
MARKS = (('❓', 'needs'), ('⚠', 'stuck'), ('✅', 'done'))
LINE_MAX, NEXT_MAX, LABEL_MAX, REPLY_MAX, WORDS_MAX = 240, 300, 40, 6000, 4000
KEEP = {'done': 3 * 86400, 'stuck': 7 * 86400, 'needs': 7 * 86400}  # an untouched card leaves the feed after this
BACKFILL = 6 * 3600  # a hub start cards only replies this fresh: an older one was settled elsewhere or went stale
SEEN_SLACK = 180  # a hook post this long before the live snapshot's last row still covers that turn
KIND = 'ending'
CARD = 'ending-card:'  # source prefix of the deck card a needs/stuck ending files
ASKS = ('needs', 'stuck')
CARD_RULE = 'r3'  # a bump re-cards open replies: an answered one could come back
URL = re.compile(r'\S+://\S+')  # a link's ?query is no question
GO_LABEL = 'Go ahead'  # client/tasks: a card's button when the reply gave a next step but no buttons
BODY_MAX = 1500
REWORD_DAILY = 40  # model calls a day that turn a session's ❓ reply into a decision; past it the plain card stays
REWORD_PROMPT = '''A coding agent ended its turn asking its owner something. The owner answers on his phone, from a card,
in seconds, without opening the session. Turn the reply into that card. Reply with JSON only:
{{"question": "<one short question that ends with ?, at most 90 characters, plain words, no ids>",
  "options": [{{"label": "<2-4 words>", "detail": "<what happens then, at most 80 characters>"}}],
  "pick": "<the label the agent recommends>",
  "why": ["<2-3 short bullets: what it is about and why it matters>"]}}
2 options, a third only when it is a really different outcome; the agent's own pick first. Never a "not yet" or "later" option.
If the reply asks for information he must type (a number, a name), options = [] and the question asks for it.
If the reply asks him nothing at all (a status, a result, a sentence of agreement), reply {{"status": true}} instead.
Write in the language of the reply. The card must keep these rules he gave about cards:
{rules}

SESSION: {name}
REPLY:
{reply}'''  # bumped when the card's wording changes, so open cards are rebuilt once


def manual(machine):
    """A machine outside Beacon's loop (`manual` in the settings' machines): he explores there by hand."""
    return bool((settings.MACHINES.get(str(machine or '')) or {}).get('manual'))


def clip(v, n):
    s = ' '.join(str(v or '').split())
    return s if len(s) <= n else s[:n - 1].rstrip() + '…'


def state_of(line):
    head = (line or '').lstrip().lstrip('*_ ')
    for mark, state in MARKS:
        if head.startswith(mark):
            return state
    return 'done'


def strip_mark(line):
    s = (line or '').strip()
    for mark, _ in MARKS:
        if s.lstrip('*_ ').startswith(mark):
            s = s.lstrip('*_ ')[len(mark):].lstrip('️').strip()
            break
    return s.replace('**', '').strip(' *_')


class Endings:
    def __init__(self, store, questions=None, titles=None, model=None, cardrules=None):
        self.store = store
        self.cardrules = cardrules  # hub/cardrules.py: his rules about cards, and the log of what they held back
        self.questions = questions
        self.titles = titles
        self.model = model  # prompt -> dict (titles.ask_haiku on the live hub); None leaves cards plain
        self.calls = []
        with store.connect() as con:
            con.executescript(SCHEMA)
            con.execute("DELETE FROM ending_seen WHERE session_id NOT LIKE '%:%'")  # raw ids from before clean_live
        if questions is not None:
            now = time.time()
            for e in self.feed():  # cards from before the deck took them (2026-11-23); a day-old reply came back as a
                # card for work another session had already shipped, so only fresh ones (2026-11-23)
                if e['state'] in ASKS and not e['open_question'] and now - e['updated_at'] <= BACKFILL:
                    self.card(self.get(e['id']))
            with store.connect() as con:  # cards dealt by an older rule: rebuilt (or withdrawn) under this one
                old = con.execute("SELECT source FROM questions WHERE kind = ? AND status = 'open' AND source LIKE ? "
                                  "AND source NOT LIKE ?", (KIND, CARD + '%', f'{CARD}%:{CARD_RULE}%')).fetchall()
            for r in old:
                eid = r['source'][len(CARD):].split(':', 1)[0]
                if eid.isdigit() and (e := self.get(int(eid))):
                    self.card(e)

    @staticmethod
    def row(r):
        e = dict(r)
        e['actions'] = json.loads(e['actions'] or '[]')
        return e

    def get(self, eid):
        with self.store.connect() as con:
            r = con.execute('SELECT * FROM endings WHERE id = ?', (eid,)).fetchone()
        return self.row(r) if r else None

    def by_session(self, sid):
        with self.store.connect() as con:
            r = con.execute('SELECT * FROM endings WHERE session_id = ?', (sid,)).fetchone()
        return self.row(r) if r else None

    # ------------------------------------------------------------ in
    def post(self, f):
        """One session's ending: replaces its card (and brings it back if he had cleared an older one)."""
        if not isinstance(f, dict):
            raise ValueError('body: expected a JSON object')
        sid = clip(f.get('session_id'), 200)
        if not sid:
            raise ValueError('session_id: required')
        if ':' not in sid:
            sid = f'Claude Code:{sid}'
        if manual(f.get('machine')):  # a box outside Beacon (an old client there still posts): no card
            return {'session_id': sid, 'skipped': 'machine outside Beacon'}
        raw = str(f.get('line') or '').strip()
        if not raw:
            raise ValueError('line: required')
        state = f.get('state') if f.get('state') in STATES else state_of(raw)
        line = clip(strip_mark(raw), LINE_MAX)
        if not line:
            raise ValueError('line: empty once its mark is gone')
        acts = f.get('actions') or []
        if not isinstance(acts, list):
            raise ValueError('actions: expected a list of labels')
        actions = []
        for a in acts:
            label = clip(a.get('label') if isinstance(a, dict) else a, LABEL_MAX)
            if label and label.casefold() not in {x.casefold() for x in actions}:
                actions.append(label)
        actions = actions[:2]
        live = self.live_row(sid) or {}
        now = time.time()
        cols = {
            'agent': clip(f.get('agent') or live.get('agent') or sid.split(':', 1)[0], 40),
            'machine': clip(live.get('machine') or f.get('machine'), 64),
            'project': clip(f.get('project') or live.get('project'), 80),
            'title': clip(f.get('title') or live.get('title'), 120) or None,
            'state': state, 'line': line,
            'next': clip(f.get('next'), NEXT_MAX) or None,
            'actions': json.dumps(actions, ensure_ascii=False),
            'reply': str(f.get('reply') or '')[:REPLY_MAX] or None,
            'cwd': clip(f.get('cwd'), 400) or None,
            'source': 'backstop' if f.get('source') == 'backstop' else 'hook',
            'updated_at': now,
        }
        with self.store.connect() as con:
            old = con.execute('SELECT * FROM endings WHERE session_id = ?', (sid,)).fetchone()
            if old and f.get('source') == 'backstop':
                seen = con.execute('SELECT last FROM ending_seen WHERE session_id = ?', (sid,)).fetchone()
                if not seen or old['updated_at'] >= seen['last'] - SEEN_SLACK:
                    return self.row(old)  # the hook already carded that turn: the backstop never overwrites it
            if old:
                # 2: the same reply again (a re-stop) keeps his "Got it"; a new turn is a new card, even with the same
                # first line ("✅ Done." twice), and a tap on it files a new decision.
                same = (old['line'] == line and old['state'] == state and old['next'] == cols['next']
                        and (old['reply'] or None) == cols['reply'])
                cols['cleared_at'] = old['cleared_at'] if same else None
                if not same:
                    cols.update(acted=None, question_id=None)
                sets = ', '.join(f'{k} = ?' for k in cols)
                con.execute(f'UPDATE endings SET {sets} WHERE id = ?', [*cols.values(), old['id']])
                eid = old['id']
            else:
                cols.update(session_id=sid, created_at=now)
                names = list(cols)
                eid = con.execute(f'INSERT INTO endings ({", ".join(names)}) VALUES ({", ".join("?" * len(names))})',
                                  [cols[n] for n in names]).lastrowid
        return self.card(self.get(eid))

    def card(self, e):
        """The deck card of a needs/stuck ending: one open decision per session, for its latest reply. A done reply,
        a cleared card or a decision the session filed itself leaves none. Returns the ending."""
        if self.questions is None:
            return e
        turn = hashlib.sha1('\x1f'.join(str(e.get(k) or '') for k in ('state', 'line', 'next', 'reply')).encode()).hexdigest()[:12]
        src = f"{CARD}{e['id']}:{CARD_RULE}{turn}"  # a re-stop of the same reply is the same card: an answered one never comes back
        with self.store.connect() as con:
            rows = con.execute("SELECT id, kind, source FROM questions WHERE session_id = ? AND status = 'open'",
                               (e['session_id'],)).fetchall()
        for r in rows:
            if r['kind'] == KIND and (r['source'] or '').startswith(CARD) and r['source'] != src:
                self.questions.withdraw(r['id'], 'the session replied again')
        if not self.carded(e) or any(r['kind'] != KIND for r in rows):
            return e
        if not self.asks(e):  # a ❓ that asks nothing (a status, "Yes, I agree") is no card (2026-11-23)
            if self.cardrules is not None and not self.dropped_before(src):
                self.cardrules.log('ending', 'dropped', {'question': e['line'], 'id': None}, why=src,
                                   session_id=e['session_id'], asked_by=e.get('title') or e.get('project'))
            return e
        # The session's own words are the question; no task-title prefix and no made-up button ("Go ahead",
        # "Try again"): with no buttons of its own he answers in words or drops it (2026-11-23).
        ctx = []
        name = e.get('title') or e.get('project')
        if name:
            ctx.append(f'From the session "{name}".')
        body = '\n'.join((e.get('reply') or '').strip().splitlines()[1:]).strip()  # the why: the reply under its line
        if body:
            ctx.append(body if len(body) <= BODY_MAX else body[:BODY_MAX - 1].rstrip() + '…')
        elif e.get('next'):
            ctx.append(f"Its next step: {e['next']}")
        ctx.append('Your answer goes back into that session; "Not needed" drops the card.')
        acts = self.actions_of(e)
        q = self.questions.create({
            'question': clip(e['line'], 280), 'options': acts, 'default': (acts or [None])[0],
            'kind': KIND, 'session_id': e['session_id'], 'project': e.get('project'), 'context': '\n\n'.join(ctx),
            'see_none': "a session's own reply: the session holds what to look at", 'source': src,
        }, 'endings')
        if self.model and e['state'] == 'needs' and not acts and e.get('reply') and self.budget_left():
            threading.Thread(target=self.reword, args=(q['id'], e, name), daemon=True).start()
        return e

    def asks(self, e):
        """Does the reply ask him anything: a question mark anywhere in it (links aside), or buttons of its own."""
        words = URL.sub('', ' '.join(str(e.get(k) or '') for k in ('line', 'next', 'reply')))
        return '?' in words or bool(self.actions_of(e))

    def dropped_before(self, src):
        """A re-stop of the same reply is logged once."""
        with self.store.connect() as con:
            return bool(con.execute("SELECT 1 FROM card_checks WHERE kind = 'ending' AND verdict = 'dropped' AND why = ?",
                                    (src,)).fetchone())

    def budget_left(self):
        now = time.time()
        self.calls = [t for t in self.calls if now - t < 86400]
        if len(self.calls) >= REWORD_DAILY:
            return False
        self.calls.append(now)
        return True

    def reword(self, qid, e, name):
        """A ❓ reply read as a decision: a question, the agent's options with its pick first, and why; the same
        shape as the cards agents file themselves (2026-11-23: "it's so much different from the decisions").
        The plain card stays when the model fails."""
        try:
            rules = self.cardrules.rules_block() if self.cardrules is not None else ''
            got = self.model(REWORD_PROMPT.format(name=name or 'a session', reply=(e.get('reply') or '')[:4000],
                                                  rules=rules or '(none yet)')) or {}
            if got.get('status'):  # it asks him nothing after all: off the deck
                if self.questions.withdraw(qid, 'the reply asks him nothing') and self.cardrules is not None:
                    self.cardrules.log('ending', 'dropped', self.questions.get(qid), why='reworded: the reply asks him nothing')
                return
            question = clip(got.get('question'), 200)
            if not question:
                return
            opts = [{'label': clip(o.get('label'), 60), 'detail': clip(o.get('detail'), 160)}
                    for o in (got.get('options') or [])[:3] if isinstance(o, dict) and o.get('label')]
            why = [clip(b, 200) for b in (got.get('why') or [])[:3] if isinstance(b, str) and b.strip()]
            ctx = [f'From the session "{name}".'] if name else []
            ctx.append('\n'.join(f'- {b}' for b in why) if why else '')
            ctx.append('Your answer goes back into that session; "Not needed" drops the card.')
            done = self.questions.reword(qid, question, opts, clip(got.get('pick'), 60) or None, '\n\n'.join(x for x in ctx if x))
            if done and self.cardrules is not None:
                self.cardrules.log('ending', 'reworded', done, why='a session reply rewritten as a decision under his rules')
        except Exception:  # noqa: BLE001 - the plain card is still there
            traceback.print_exc()

    @staticmethod
    def actions_of(e):
        """The session's own buttons: the Stop hook's stand-in "Go ahead" is no choice."""
        return [a for a in e['actions'] if a.casefold() != GO_LABEL.casefold()]

    def carded(self, e):
        """Does this ending go on the deck: a needs reply, or a stuck one with buttons of its own. Never one the
        nightly backstop wrote: a one-line guess at an old session, with no question in it (2026-11-23)."""
        if e['cleared_at'] or e['state'] not in ASKS or e.get('source') == 'backstop':
            return False
        return e['state'] == 'needs' or bool(self.actions_of(e))

    def live_row(self, sid):
        """The session as the machines' live snapshots see it (machine, project, name), else None."""
        try:
            for m in self.store.live_now()['machines']:
                for s in m['sessions']:
                    if s.get('id') == sid:
                        if self.titles:
                            self.titles.annotate([s])
                        return s
        except Exception:  # noqa: BLE001 - a card without its machine is still a card
            return None
        return None

    def saw(self, machine, sessions):
        """Every live snapshot: remember each session whose turn is over, for the backstop."""
        rows = []
        for s in sessions or []:
            if not isinstance(s, dict) or s.get('state') not in ('waiting', 'done') or not s.get('doing') or not s.get('last'):
                continue
            if str(s.get('doing')).startswith(('Stopped by you', 'Reading your message')):
                continue
            rows.append((str(s.get('id'))[:200], clip(s.get('agent'), 40), machine, clip(s.get('project'), 80),
                         clip(s.get('title'), 120), clip(s.get('doing'), LINE_MAX), float(s['last'])))
        if not rows:
            return
        with self.store.connect() as con:
            con.executemany('INSERT INTO ending_seen VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET '
                            'agent=excluded.agent, machine=excluded.machine, project=excluded.project, '
                            'title=COALESCE(excluded.title, ending_seen.title), line=excluded.line, last=excluded.last '
                            'WHERE excluded.last >= ending_seen.last', rows)

    def missed(self, hours=24, limit=60):
        """Sessions whose turn ended in the last `hours` with no ending posted for that turn (the backstop's list)."""
        since = time.time() - hours * 3600
        with self.store.connect() as con:
            rows = con.execute(
                'SELECT s.* FROM ending_seen s LEFT JOIN endings e ON e.session_id = s.session_id '
                'WHERE s.last >= ? AND (e.id IS NULL OR e.updated_at < s.last - ?) ORDER BY s.last DESC LIMIT ?',
                (since, SEEN_SLACK, limit)).fetchall()
        out = [dict(r, id=r['session_id']) for r in rows if not manual(r['machine'])]  # his by-hand box: no card
        if self.titles:  # the session's own name or task title, as everywhere on the hub
            try:
                self.titles.annotate(out)
            except Exception:  # noqa: BLE001
                pass
        return out

    # ------------------------------------------------------------ out
    def feed(self, include_cleared=False, limit=200):
        """The cards to show, newest first; `question_id` is set when the session has an open decision
        (the feed then shows that decision, not this ending)."""
        now = time.time()
        with self.store.connect() as con:
            where = '' if include_cleared else 'WHERE cleared_at IS NULL'
            rows = [self.row(r) for r in con.execute(f'SELECT * FROM endings {where} ORDER BY updated_at DESC LIMIT ?',
                                                     (limit,))]
            open_q, answered_q = {}, {}
            sids = [e['session_id'] for e in rows]
            for i in range(0, len(sids), 500):
                chunk = sids[i:i + 500]
                for r in con.execute(f'SELECT id, session_id, status, updated_at, kind FROM questions '
                                     f'WHERE session_id IN ({",".join("?" * len(chunk))})', chunk):
                    if r['status'] == 'open':
                        open_q.setdefault(r['session_id'], r['id'])
                    else:  # its own card too: answered on the deck, the reply is settled (a second tap ran it twice)
                        answered_q[r['session_id']] = max(answered_q.get(r['session_id'], 0), r['updated_at'] or 0)
        out = []
        self.name(rows)
        for e in rows:
            # Reads are open to the whole tailnet: the reply's text stays in the hub (a tap's run reads it), never here.
            e.pop('reply', None)
            e.pop('cwd', None)
            if not include_cleared and now - e['updated_at'] > KEEP[e['state']]:
                continue
            e['open_question'] = open_q.get(e['session_id'])
            # He settled a decision of this session after this reply (answered or withdrawn): the session moved on and
            # this reply ("waiting for your answer…") is stale until its next one.
            if not e['open_question'] and answered_q.get(e['session_id'], 0) >= e['updated_at']:
                continue
            out.append(e)
        return out

    def name(self, rows):
        """The session's own name or task title at read time (it is often named after its first reply), never
        a folder-ish "beacon-6c"."""
        if not self.titles or not rows:
            return
        tmp = [{'id': e['session_id'], 'project': e.get('project')} for e in rows]
        try:
            self.titles.annotate(tmp)
        except Exception:  # noqa: BLE001 - a card keeps the name it came with
            return
        for e, t in zip(rows, tmp):
            # A tap's own holder task ("Decide: ...", filed by the hub for the answer) is no name for the session.
            if t.get('title') and not t['title'].startswith('Decide: '):
                e['title'] = t['title']

    def clear(self, eid=None, state=None, ids=None):
        """"Got it" / Dismiss on one card, the cards he saw (`ids`, the done pile's "Clear all"), or every card of a state."""
        now = time.time()
        with self.store.connect() as con:
            if ids is not None:
                if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
                    raise ValueError('ids: expected a list of card numbers')
                n = sum(con.execute('UPDATE endings SET cleared_at = ? WHERE id = ? AND cleared_at IS NULL', (now, i)).rowcount
                        for i in ids[:500])
                return {'cleared': n}
            if eid is not None:
                n = con.execute('UPDATE endings SET cleared_at = ? WHERE id = ? AND cleared_at IS NULL', (now, eid)).rowcount
                return self.get(eid) if n or self.get(eid) else None
            if state not in STATES:
                raise ValueError('state: expected one of ' + ', '.join(STATES))
            n = con.execute('UPDATE endings SET cleared_at = ? WHERE state = ? AND cleared_at IS NULL', (now, state)).rowcount
        return {'cleared': n}

    def unclear_many(self, ids):
        """"Clear all"'s undo."""
        with self.store.connect() as con:
            for i in [i for i in ids or [] if isinstance(i, int)][:500]:
                con.execute('UPDATE endings SET cleared_at = NULL WHERE id = ?', (i,))
        return {'restored': len(ids or [])}

    def unclear(self, eid):
        """The toast's undo."""
        with self.store.connect() as con:
            con.execute('UPDATE endings SET cleared_at = NULL WHERE id = ?', (eid,))
        return self.get(eid)

    def act(self, eid, label, via='page', words=None):
        """A button tap, or his own words typed on the card: an answered decision on that session (kind `ending`),
        which answer-dispatcher carries back into it like any answer he gives. The card leaves the feed."""
        e = self.get(eid)
        if not e:
            return None
        words = str(words or '').strip()[:WORDS_MAX]
        label = words or clip(label, LABEL_MAX)
        if not label:
            raise ValueError('label: required')
        if e.get('question_id') and e.get('acted'):
            return {'ending': e, 'question': self.questions.get(e['question_id'])}  # a double tap: once
        with self.store.connect() as con:
            cards = [r['id'] for r in con.execute("SELECT id FROM questions WHERE session_id = ? AND status = 'open' "
                                                  "AND source LIKE ?", (e['session_id'], CARD + '%'))]
        for qid in cards:  # the tap answers this reply: its deck card goes
            self.questions.withdraw(qid, 'answered on the session card')
        name = e.get('title') or e.get('project') or 'Session'
        options = list(dict.fromkeys(e['actions'] if words else [*e['actions'], label])) or ['Reply']
        ctx = [f"The session's last reply said: {e['line']}"]
        if e.get('next'):
            ctx.append(f"Its suggested next step: {e['next']}")
        if words:
            ctx.append('He answered in his own words (not a button).')
        ctx.append(f"Session {e['session_id']}: its full reply is in its own log (ai-corpus has it too).")
        q = self.questions.create({
            'question': clip(f'{name}: {e["line"]}', 280), 'options': options, 'default': options[0],
            'kind': KIND, 'session_id': e['session_id'], 'project': e.get('project'),
            'context': '\n\n'.join(ctx), 'source': f"ending:{e['id']}:{e['updated_at']:.3f}",
        }, 'endings')
        if q['status'] == 'open':
            q = self.questions.answer(q['id'], {'answer': label}, via)
        with self.store.connect() as con:
            con.execute('UPDATE endings SET acted = ?, question_id = ?, cleared_at = ? WHERE id = ?',
                        (label, q['id'], time.time(), eid))
        return {'ending': self.get(eid), 'question': q}
