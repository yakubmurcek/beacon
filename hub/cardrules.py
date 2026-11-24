"""His rules about decision cards, and the log of what they caught (task 1903).

Most cards that made no sense came from our own instructions and code, so those are fixed at the source (the
lint, `put_off`, an ending with no question makes no card). What is left is learning: every card he 👎s teaches.

- The rules file (settings `paths.card_rules`, kept outside the repo: his own complaints, dated) seeds the list.
- His 👎 on a card is read once by Haiku against the list: either the rule it already breaks (a repeat, logged so it
  shows) or one new plain rule, added to the list. Taking the 👎 back drops the rule it taught.
- The list reaches the agents with no model in the way: `tasks ask` prints it under every card (and with every
  refusal), and the reword of a session's ending card writes under it.
- Every hold-back is logged (`card_checks`): a `tasks ask` the lint refused, an ending that asked nothing, a reworded
  card, a 👎 lesson. Labs' `bad_questions` shows them; the daily cap counts the model calls from it.
"""
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger('beacon.cardrules')

DAILY = int(os.environ.get('CARDRULES_DAILY', '30'))  # 👎 lessons a day (he taps a few at most)
WAIT_FOR_WORDS = 180  # a bare 👎 waits this long for the words he types in its box, then is read without them
RULE_MAX = 240

SCHEMA = """
CREATE TABLE IF NOT EXISTS card_rules (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL,
  source TEXT NOT NULL,              -- file | flag
  qid INTEGER,                       -- the card his 👎 taught it from
  active INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS card_checks (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  qid INTEGER,
  session_id TEXT,
  asked_by TEXT,
  kind TEXT NOT NULL,                -- ask | ending | flag
  verdict TEXT NOT NULL,             -- refused | dropped | reworded | learned | repeat | taken-back | skipped | failed
  called INTEGER NOT NULL DEFAULT 0, -- a model call was made (counts toward the cap)
  rules TEXT NOT NULL DEFAULT '[]',  -- the rule ids it broke or taught
  why TEXT,
  question TEXT
);
CREATE INDEX IF NOT EXISTS card_checks_ts ON card_checks(ts);
"""

LEARN_PROMPT = '''The owner tapped 👎 on a decision card: it made no sense to him as asked. Below are the rules already kept
about cards, the card, and his words (maybe none). Does one of the rules already say what is wrong with it?
Name a rule only if following it would have prevented exactly what his words complain about; when unsure,
write a new rule. If not covered, write the new rule: one plain sentence any agent can follow on any card (not about this
card's topic), at most 200 characters, in English.
Reply with JSON only: {{"covered": <rule number or null>, "rule": "<the new rule, or empty>"}}

RULES:
{rules}

CARD:
{card}

HIS WORDS: {note}'''


def clip(v, n):
    v = re.sub(r'\s+', ' ', str(v or '')).strip()
    return v if len(v) <= n else v[:n - 1].rstrip() + '…'


def card_text(q):
    """A card as he sees it: question, context, buttons, steps, what to look at."""
    opts = [o if isinstance(o, str) else (o.get('label') or '') + (f" ({o['detail']})" if o.get('detail') else '')
            for o in (q.get('options') or [])]
    lines = [f"Question: {q.get('question') or ''}", f"Kind: {q.get('kind') or 'decision'}"]
    if (q.get('context') or '').strip():
        lines.append('Context:\n' + q['context'].strip()[:2500])
    lines.append('Buttons: ' + (' | '.join(opts) if opts else '(none: he types an answer or drops it)'))
    if q.get('default_answer'):
        lines.append(f"Agent's pick: {q['default_answer']}")
    steps = [s if isinstance(s, str) else s.get('text', '') for s in (q.get('steps') or [])]
    if steps:
        lines.append('Steps for his hands: ' + ' / '.join(steps))
    see = q.get('see') or []
    lines.append(f'Something to open or look at: {len(see)} link(s)/picture(s)' if see else
                 'Something to open or look at: none' + (f" ({q['see_none']})" if q.get('see_none') else ''))
    return '\n'.join(lines)


def read_rules_file(path):
    """The rules file: one rule per `- ` line under its first heading list; anything else is for people."""
    try:
        with open(os.path.expanduser(path), encoding='utf-8') as f:
            body = f.read()
    except OSError:
        return []
    m = re.search(r'^## Rules\s*$(.*?)(?=^## |\Z)', body, re.M | re.S)  # only the Rules section, when it has one
    return [clip(x.group(1), RULE_MAX) for x in re.finditer(r'^\s*[-*]\s+(.+)$', m.group(1) if m else body, re.M)]


class CardRules:
    def __init__(self, store, get_card, model=None, rules_file=None, daily=DAILY, background=True, wait=WAIT_FOR_WORDS):
        self.store = store
        self.get_card = get_card  # qid -> the card as it is now (Questions.get)
        self.model = model  # prompt -> dict (titles.ask_haiku on the live hub); None: a 👎 is only logged
        self.rules_file = rules_file
        self.daily, self.background, self.wait = daily, background, wait
        self.lock = threading.Lock()
        with store.connect() as con:
            con.executescript(SCHEMA)

    # ------------------------------------------------------------ rules

    def file_rules(self):
        return read_rules_file(self.rules_file) if self.rules_file else []

    def rules(self):
        """Numbered as agents see them: the file's rules first (f1, f2…), then the ones his 👎 taught (by id)."""
        out = [{'id': f'f{i}', 'text': t, 'source': 'file'} for i, t in enumerate(self.file_rules(), 1)]
        with self.store.connect() as con:
            out += [{'id': str(r['id']), 'text': r['text'], 'source': 'flag', 'qid': r['qid'], 'created_at': r['created_at']}
                    for r in con.execute("SELECT * FROM card_rules WHERE active = 1 AND source = 'flag' ORDER BY id")]
        return out

    def rules_block(self, rules=None):
        """Numbered 1..N for a model (it answers with a plain number); `rule_at` maps the number back."""
        return '\n'.join(f"{i}. {r['text']}" for i, r in enumerate(rules if rules is not None else self.rules(), 1))

    @staticmethod
    def rule_at(rules, n):
        try:
            n = int(str(n).strip().lstrip('fF#'))
        except (TypeError, ValueError):
            return None
        return rules[n - 1]['id'] if 1 <= n <= len(rules) else None

    def add_rule(self, text, qid=None):
        text = clip(text, RULE_MAX)
        if len(text) < 8:
            return None
        with self.store.connect() as con:
            hit = con.execute("SELECT id FROM card_rules WHERE lower(text) = lower(?) AND active = 1", (text,)).fetchone()
            if hit:
                return hit['id']
            return con.execute("INSERT INTO card_rules (text, source, qid, created_at) VALUES (?, 'flag', ?, ?)",
                               (text, qid, time.time())).lastrowid

    # ------------------------------------------------------------ the log and the cap

    def log(self, kind, verdict, q=None, called=False, rules=(), why=None, session_id=None, asked_by=None):
        q = q or {}
        with self.store.connect() as con:
            con.execute('INSERT INTO card_checks (ts, qid, session_id, asked_by, kind, verdict, called, rules, why, question) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (time.time(), q.get('id'), session_id or q.get('session_id'), asked_by or q.get('asked_by'), kind,
                         verdict, int(called), json.dumps([str(x) for x in rules]), clip(why, 500) or None,
                         clip(q.get('question'), 300)))

    def calls_today(self):
        with self.store.connect() as con:
            return con.execute('SELECT COUNT(*) FROM card_checks WHERE called = 1 AND ts > ?',
                               (time.time() - 86400,)).fetchone()[0]

    def checks(self, since=None, limit=200):
        since = since or time.time() - 30 * 86400
        with self.store.connect() as con:
            rows = con.execute('SELECT * FROM card_checks WHERE ts >= ? ORDER BY ts DESC, id DESC LIMIT ?',
                               (since, limit)).fetchall()
        return [{**dict(r), 'rules': json.loads(r['rules'] or '[]')} for r in rows]

    def summary(self):
        """For /api/questions/rules: the rules, today's lessons against the cap, what was held back lately."""
        return {'rules': self.rules(), 'calls_today': self.calls_today(), 'daily': self.daily,
                'checks': self.checks(time.time() - 7 * 86400, 100)}

    # ------------------------------------------------------------ his 👎

    def lesson(self, qid):
        """The last thing his 👎 on this card did: a lesson (learned/repeat) still standing, or None."""
        with self.store.connect() as con:
            r = con.execute("SELECT verdict FROM card_checks WHERE kind = 'flag' AND qid = ? "
                            "AND verdict IN ('learned', 'repeat', 'taken-back') ORDER BY id DESC LIMIT 1", (qid,)).fetchone()
        return r['verdict'] if r and r['verdict'] != 'taken-back' else None

    def flagged(self, q, note=None):
        """His 👎 (the tap, then maybe his words in its box): read once per card. With words, now; a bare tap waits
        for them a few minutes, then is read from the card alone."""
        if note:
            return self.run(self.learn, q['id'], note)
        if self.background:
            t = threading.Timer(self.wait, self._safe, args=(self.learn, q['id'], None, True))
            t.daemon = True
            t.start()
        else:
            self.learn(q['id'], None, True)

    def taken_back(self, q):
        """He took his 👎 back: the rule it taught goes too."""
        with self.store.connect() as con:
            con.execute("UPDATE card_rules SET active = 0 WHERE source = 'flag' AND qid = ?", (q['id'],))
        if self.lesson(q['id']):
            self.log('flag', 'taken-back', q)

    def learn(self, qid, note=None, bare=False):
        """One Haiku read of a 👎'd card: the rule it broke (a repeat) or a new rule from it. Returns the rule id."""
        with self.lock:  # the bare tap's timer and his words may land together: one lesson per card
            q = self.get_card(qid)
            if not q or not q.get('flagged_at') or self.lesson(qid):
                return None
            if bare and q.get('flag'):  # his words arrived meanwhile and were (or are being) read
                return None
            note = note or q.get('flag')
            if self.model is None or self.calls_today() >= self.daily:
                self.log('flag', 'skipped', q, why=note or ('no model' if self.model is None else 'daily cap reached'))
                return None
            rules = self.rules()
            try:
                got = self.model(LEARN_PROMPT.format(rules=self.rules_block(rules) or '(none yet)', card=card_text(q),
                                                     note=clip(note, 500) or '(none: work out what is wrong from the card)'))
            except Exception:  # noqa: BLE001 - a failed lesson is logged, the 👎 still reached its agent
                log.exception('learn failed')
                got = None
            if not isinstance(got, dict):
                self.log('flag', 'failed', q, called=True, why=note)
                return None
            covered = self.rule_at(rules, got.get('covered'))
            if covered:
                self.log('flag', 'repeat', q, called=True, rules=[covered], why=note)
                return covered
            rid = self.add_rule(got.get('rule'), qid)
            self.log('flag', 'learned' if rid else 'failed', q, called=True, rules=[rid] if rid else [], why=note)
            return rid

    def run(self, fn, *args):
        if not self.background:
            return fn(*args)
        threading.Thread(target=self._safe, args=(fn, *args), daemon=True).start()

    @staticmethod
    def _safe(fn, *args):
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception('_safe failed')
