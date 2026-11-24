"""Standing rules learned from his own repeated answers (task 1764, 2026-11-22).

No checkbox: when he answers the same kind of question the same way 2+ times, the hub proposes a
rule to him as a question, with the evidence (which questions, when, what he said). Only his yes
on that proposal makes it active. An active rule answers a new matching question itself, and every
use is logged with the reason it matched (http://beacon/decisions, Rules), one click turns a rule off or
takes a use back.

Two guards no model can talk around (code, not the model):
  - a question touching money, sending, publishing, deleting, going live, other people or accounts
    (the gate's risk words, hub/gate.py) is never answered by a rule learned from repeats, and such
    repeats are never proposed; only a rule he wrote in his own words (`tasks answer --always`,
    source "his-words") may answer one;
  - the model only judges "is this the same decision?" and says why; unsure means ask him.
Model: Sonnet 5.5 at medium effort through `claude -p` (his subscription, no hooks, no tools).
"""
import datetime
import json
import os
import re
import subprocess
import threading
import time
import traceback

try:
    from . import gate
except ImportError:  # run as a script from hub/
    import gate

MODEL = os.environ.get('RULES_MODEL', 'claude-sonnet-5-5')
EFFORT = os.environ.get('RULES_EFFORT', 'medium')
CLAUDE = os.environ.get('RULES_CLAUDE') or os.path.expanduser('~/.local/bin/claude')
REPEATS = 2           # same answer this many times -> propose
SIMILAR = 0.25        # word overlap (Jaccard) for "the same kind of question", before the model looks
PROPOSAL_KIND = 'rule'
STATUSES = ('proposed', 'active', 'off', 'declined')
NOT_COUNTED = (PROPOSAL_KIND, 'test', 'feedback')  # rule proposals, test pings and his page feedback are not decisions
HIS_VIA = ('page', 'task', 'chat', 'cli')  # answered by him (not by a rule)
SWEEP_DAYS, SWEEP_REPEATS, SWEEP_CALLS = 30, 3, 5  # the evening sweep: last 3 the same within 30 days, 5 model calls

SCHEMA = '''
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    project TEXT,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    answer TEXT NOT NULL,
    source TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    proposal_qid INTEGER,
    why TEXT,
    takes_default INTEGER NOT NULL DEFAULT 0,
    fired INTEGER NOT NULL DEFAULT 0,
    last_fired_at REAL,
    created_at REAL NOT NULL,
    decided_at REAL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_uses (
    id INTEGER PRIMARY KEY,
    rule_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    why TEXT NOT NULL,
    at REAL NOT NULL,
    undone_at REAL
);
CREATE INDEX IF NOT EXISTS rule_uses_rule ON rule_uses(rule_id, at);
CREATE TABLE IF NOT EXISTS rule_sweeps (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    days INTEGER NOT NULL,
    answers INTEGER NOT NULL,
    repeated INTEGER NOT NULL,
    risky INTEGER NOT NULL,
    covered INTEGER NOT NULL,
    model_calls INTEGER NOT NULL,
    proposed TEXT NOT NULL DEFAULT '[]'
);
'''
STOP = set('the a an to of and or for in on is it be do i we you this that with should which what how my me '
           'a je to se na v ve z do pro ze s k o jak co mam mame by bych ktery ktera ktere nebo jen uz'.split())


def words(text):
    return {w for w in re.findall(r'[a-z0-9]{3,}', gate.fold(text or '')) if w not in STOP}


def overlap(a, b):
    a, b = words(a), words(b)
    return len(a & b) / len(a | b) if a and b else 0.0


def norm_answer(a):
    a = gate.fold(re.sub(r'\(recommended\)', '', str(a or ''), flags=re.I))
    return ' '.join(re.findall(r'[a-z0-9]+', a))


SUGGESTION = "the agent's suggestion"


def answer_key(q):
    """What he decided, comparable across questions: taking the agent's pick is one answer, whatever
    its words were; otherwise his words, folded."""
    if q.get('matched_default'):
        return '=default'
    return norm_answer(q['answer'])


def risky(*texts):
    """The code check: {category: word} when any of these texts touches a risk category."""
    return gate.risk_words('\n'.join(str(t or '') for t in texts))


def ask_model(prompt, timeout=120):
    """One Sonnet call, JSON back, or None (no model, timeout, bad output: the caller asks him)."""
    try:
        r = subprocess.run([CLAUDE, '-p', prompt, '--model', MODEL, '--effort', EFFORT, '--setting-sources', '',
                            '--tools', '', '--strict-mcp-config', '--no-chrome', '--output-format', 'json'],
                           capture_output=True, text=True, timeout=timeout, cwd='/tmp',
                           env={**os.environ, 'HOME': os.path.expanduser('~'), 'TASKS_HOOKS': '0'}, stdin=subprocess.DEVNULL)
        out = json.loads(r.stdout).get('result') or ''
        m = re.search(r'\{.*\}', out, re.S)
        return json.loads(m.group(0)) if m else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


SAME_PROMPT = '''You check whether a person keeps making the SAME decision, so an assistant could stop asking.
Below are questions agents asked him and his answers. Decide: are these the same decision (same
situation, his answer would carry over to the next such question), or only similar words?
Be strict: different stakes, different projects that matter, or answers that depend on details = not the same.
Reply with JSON only: {{"same": true|false, "summary": "<the standing rule in one short sentence, e.g. 'Internal tool
setup choices: take the agent's suggestion'>", "answer": "<what to answer next time, short>", "why": "<one sentence>"}}

{items}'''

MATCH_PROMPT = '''An assistant has standing rules a person approved. A new question arrived. Does exactly one rule
decide it, so the assistant may answer it with that rule without asking him? Be strict: if the question
differs in stakes or needs details the rule does not cover, or you are unsure, it does not match.
Reply with JSON only: {{"match": true|false, "rule_id": <id or null>, "answer": "<the answer the rule gives, using the
question's own option label when one fits>", "why": "<one sentence naming the rule and what in the question fits it>"}}

Rules:
{rules}

New question (project {project}, kind {kind}):
{question}
Options: {options}
Agent's suggestion: {default}'''


class Rules:
    def __init__(self, store, questions, model=ask_model, background=True):
        self.store, self.questions, self.model, self.background = store, questions, model, background
        with store.connect() as con:
            con.executescript(SCHEMA)
        questions.on_created = self.on_created
        questions.on_answered = self.on_answered
        self.lock = threading.Lock()

    # ------------------------------------------------------------ reads

    @staticmethod
    def row(r):
        x = dict(r)
        x['evidence'] = json.loads(x['evidence'] or '[]')
        return x

    def get(self, rid):
        with self.store.connect() as con:
            r = con.execute('SELECT * FROM rules WHERE id = ?', (rid,)).fetchone()
        return self.row(r) if r else None

    def list(self, uses=10):
        with self.store.connect() as con:
            rules = [self.row(r) for r in con.execute('SELECT * FROM rules ORDER BY status = ? DESC, updated_at DESC', ('active',))]
            for x in rules:
                x['uses'] = [dict(u) for u in con.execute(
                    'SELECT u.*, q.question, q.answer FROM rule_uses u JOIN questions q ON q.id = u.question_id '
                    'WHERE u.rule_id = ? ORDER BY u.at DESC LIMIT ?', (x['id'], uses))]
                n = con.execute('SELECT COUNT(*) AS n, SUM(undone_at IS NOT NULL) AS back FROM rule_uses WHERE rule_id = ?',
                                (x['id'],)).fetchone()
                x['taken_back'] = n['back'] or 0
                x['answered'] = (n['n'] or 0) - x['taken_back']  # `fired` never goes down when he takes a use back
                x['evidence_questions'] = [dict(q) for q in con.execute(
                    f'SELECT id, question, answer, answered_at, project FROM questions WHERE id IN '
                    f'({",".join("?" * len(x["evidence"])) or "NULL"}) ORDER BY answered_at', x['evidence'])]
        return rules

    def annotate(self, rows):
        """History: each question a rule answered carries that rule ({id, summary}); a use he took
        back carries none."""
        ids = [x['id'] for x in rows if x.get('answered_via') == 'rule']
        if not ids:
            return rows
        with self.store.connect() as con:
            hits = {r['question_id']: {'id': r['rule_id'], 'summary': r['summary']} for r in con.execute(
                f'SELECT u.question_id, u.rule_id, r.summary FROM rule_uses u JOIN rules r ON r.id = u.rule_id '
                f'WHERE u.undone_at IS NULL AND u.question_id IN ({",".join("?" * len(ids))}) ORDER BY u.at', ids)}
        for x in rows:
            if x['id'] in hits:
                x['rule'] = hits[x['id']]
        return rows

    def decided(self, limit=50):
        """Every question a rule answered for him, newest first: the rule, the answer, why, and
        whether he took it back. Learned lists them so he can see and undo each one."""
        with self.store.connect() as con:
            return [dict(r) for r in con.execute(
                'SELECT u.id, u.question_id, u.rule_id, u.why, u.at, u.undone_at, q.question, q.answer, q.project, '
                'q.applied_at, r.summary FROM rule_uses u JOIN questions q ON q.id = u.question_id '
                'JOIN rules r ON r.id = u.rule_id ORDER BY u.at DESC LIMIT ?', (limit,))]

    def last_sweep(self):
        """The evening review's last run: what it checked and what it proposed (None before the first)."""
        with self.store.connect() as con:
            r = con.execute('SELECT * FROM rule_sweeps ORDER BY at DESC LIMIT 1').fetchone()
        if not r:
            return None
        x = dict(r)
        x['proposed'] = json.loads(x['proposed'] or '[]')
        return x

    def weekly(self, now=None):
        """Per week (Monday, his time zone), from his first answer to this week: how many questions he
        answered himself and how many a rule answered for him. Empty weeks are zeros."""
        tz = getattr(self.store, 'tz', None) or datetime.timezone.utc
        with self.store.connect() as con:
            # A rule's answer he took back after the agent had it stays "via rule" on the question: it counts as his.
            rows = con.execute(f'SELECT answered_at, answered_via = ? AND EXISTS (SELECT 1 FROM rule_uses u WHERE u.question_id = q.id '
                               f'AND u.undone_at IS NULL) AS auto FROM questions q WHERE status = ? AND answered_at IS NOT NULL '
                               f'AND kind NOT IN ({",".join("?" * len(NOT_COUNTED))})', ('rule', 'answered', *NOT_COUNTED)).fetchall()

        def monday(ts):
            d = datetime.datetime.fromtimestamp(ts, tz).date()
            return d - datetime.timedelta(days=d.weekday())
        this = monday(now or time.time())
        weeks, d = {}, min([monday(r['answered_at']) for r in rows] + [this])
        while d <= this:
            weeks[d] = {'week': d.isoformat(), 'him': 0, 'auto': 0}
            d += datetime.timedelta(days=7)
        for r in rows:
            w = weeks.get(monday(r['answered_at']))
            if w:
                w['auto' if r['auto'] else 'him'] += 1
        return list(weeks.values())

    # ------------------------------------------------------------ writes (his)

    def update(self, rid, fields):
        """He edits a rule's words or answer, or turns it off / on."""
        x = self.get(rid)
        if not x:
            return None
        sets, params = [], []
        if 'status' in fields:
            if fields['status'] not in ('active', 'off'):
                raise ValueError('status: active or off')
            if fields['status'] == 'active' and x['status'] not in ('active', 'off'):
                raise ValueError('status: a proposal becomes active only by his yes on it')
            sets.append('status = ?')
            params.append(fields['status'])
        for k in ('summary', 'answer'):
            if fields.get(k):
                sets.append(f'{k} = ?')
                params.append(str(fields[k]).strip()[:400])
        if not sets:
            return x
        with self.store.connect() as con:
            con.execute(f'UPDATE rules SET {", ".join(sets)}, updated_at = ? WHERE id = ?', (*params, time.time(), rid))
        return self.get(rid)

    def undo_use(self, uid):
        """Take one automatic answer back: the question opens again for him (if its asker hasn't
        taken the answer yet), and the use is marked undone."""
        with self.store.connect() as con:
            u = con.execute('SELECT * FROM rule_uses WHERE id = ?', (uid,)).fetchone()
        if not u:
            return None
        q = self.questions.get(u['question_id'])
        reopened = False
        if q and q['status'] == 'answered' and not q['applied_at']:
            self.questions.reopen(q['id'])
            reopened = True
        with self.store.connect() as con:
            con.execute('UPDATE rule_uses SET undone_at = ? WHERE id = ?', (time.time(), uid))
        return {'id': uid, 'reopened': reopened, 'question': u['question_id']}

    def add_his_words(self, q, answer):
        """`tasks answer --always`: a rule he said in his own words. The only kind that may answer a
        risky question later; it is active at once (he said it)."""
        now = time.time()
        with self.store.connect() as con:
            cur = con.execute('INSERT INTO rules (project, kind, summary, answer, source, evidence, status, why, created_at, '
                              'decided_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                              (q['project'], q['kind'], f'{q["question"]} → {answer}'[:400], answer[:400], 'his-words',
                               json.dumps([q['id']]), 'active', 'he said to keep it', now, now, now))
        return self.get(cur.lastrowid)

    # ------------------------------------------------------------ the two jobs

    def run(self, fn, *args):
        if not self.background:
            return fn(*args)
        threading.Thread(target=self._safe, args=(fn, *args), daemon=True).start()

    @staticmethod
    def _safe(fn, *args):
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - a learning job never breaks asking or answering
            traceback.print_exc()

    def on_answered(self, q):
        if q['kind'] == PROPOSAL_KIND and (q.get('source') or '').startswith('rule-proposal:'):
            return self.decide_proposal(q)
        if q.get('standing'):  # his own words via the CLI's --always
            return self.add_his_words(q, q['answer'])
        if q['answered_via'] in ('rule',) or q['kind'] in NOT_COUNTED:  # never a rule about rule proposals
            return None
        self.run(self.propose_from, q)

    def on_created(self, q):
        if q['status'] != 'open' or q['kind'] in (PROPOSAL_KIND, 'task', 'ending'):  # a session's own reply: his to answer
            return
        self.run(self.apply_to, q)

    def decide_proposal(self, q):
        rid = int(q['source'].split(':', 1)[1])
        yes = not gate.NOT_YES.search(gate.fold(q['answer'])) and not norm_answer(q['answer']).startswith(('ne', 'no'))
        with self.store.connect() as con:
            con.execute('UPDATE rules SET status = ?, decided_at = ?, updated_at = ? WHERE id = ? AND status = ?',
                        ('active' if yes else 'declined', time.time(), time.time(), rid, 'proposed'))
        self.questions.applied(q['id'])  # carried out here: answer-dispatcher must not start a run (it once wrote CLAUDE.md lines)
        return self.get(rid)

    def similar_answered(self, q, since=None):
        """His earlier answers to this kind of question in this project, with the same answer."""
        rows = self.questions.list('answered', project=q['project'], kind=q['kind'], since=since, limit=500)
        mine = answer_key(q)
        # Same project and kind, same answer, and either similar words or both "took the agent's pick"
        # (his picks rarely share words: the model then judges whether it is really one decision).
        return [x for x in rows if x['id'] != q['id'] and x['answered_via'] in HIS_VIA
                and answer_key(x) == mine and (mine == '=default' or overlap(x['question'] + ' ' + (x['context'] or '')[:300],
                                                                q['question'] + ' ' + (q['context'] or '')[:300]) >= SIMILAR)]

    def propose_from(self, q):
        """After an answer: if he gave it 2+ times to this kind of question, propose a rule with the
        evidence. Risky ones never: the code check says no before the model is asked."""
        with self.lock:
            same = self.similar_answered(q)
            group = sorted([*same, q], key=lambda x: x['answered_at'] or 0)
            if len(group) < REPEATS or self.any_risky(group) or self.covered([x['id'] for x in group]):
                return None
            return self.propose(group, q['project'], q['kind'])

    @staticmethod
    def any_risky(group):
        return any(risky(x['question'], x['context'], x['answer'], x['default_answer'],
                         *[o['label'] for o in x['options']]) for x in group)

    def covered(self, ids):
        """One proposal per group: none while a rule (any status) already holds one of these answers."""
        with self.store.connect() as con:
            return any(set(json.loads(r['evidence'])) & set(ids) for r in con.execute('SELECT evidence FROM rules'))

    def propose(self, group, project, kind):
        """Ask the model whether these answers are one decision; if so, file the rule as proposed and
        ask him on http://beacon/decisions. project None = every project (the evening sweep)."""
        items = '\n'.join(f'- #{x["id"]} ({time.strftime("%Y-%m-%d", time.localtime(x["answered_at"]))}, {x["project"]}): '
                          f'{x["question"]} | suggested: {x["default_answer"] or "-"} | he answered: {x["answer"]}'
                          for x in group)
        v = self.model(SAME_PROMPT.format(items=items))
        if not v or not v.get('same') or not v.get('summary') or not v.get('answer'):
            return None
        takes = answer_key(group[-1]) == '=default'
        if takes:
            v['answer'] = SUGGESTION
        ids = [x['id'] for x in group]
        now = time.time()
        with self.store.connect() as con:
            cur = con.execute('INSERT INTO rules (project, kind, summary, answer, source, evidence, status, why, '
                              'takes_default, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                              (project, kind, str(v['summary'])[:400], str(v['answer'])[:400], 'proposed',
                               json.dumps(ids), 'proposed', str(v.get('why') or '')[:400], int(takes), now, now))
            rid = cur.lastrowid
        evidence = '\n'.join(f'- {time.strftime("%-d. %-m.", time.localtime(x["answered_at"]))} „{x["question"]}“ → **{x["answer"]}**'
                             for x in group)
        p = self.questions.create({
            'question': f'Mám to brát jako pravidlo? {v["summary"]}'[:400],
            'context': f'Odpověděl jsi takhle {len(group)}×{"" if project else " (napříč projekty)"}:\n{evidence}\n\n'
                       f'Příště bych sám odpověděl: **{v["answer"]}** '
                       f'(a zapsal proč). Rizikové věci (peníze, posílání, zveřejnění, mazání, ostrý provoz, lidi) '
                       f'tak nikdy neodpovím. Pravidlo jde kdykoli vypnout v Decisions → Learned.',
            'options': ['Ano, ať to platí', 'Ne, ptej se dál'], 'default': 'Ne, ptej se dál', 'kind': PROPOSAL_KIND,
            'project': project or group[-1]['project'], 'asked_by': 'hub rules', 'source': f'rule-proposal:{rid}'}, 'hub rules')
        with self.store.connect() as con:
            con.execute('UPDATE rules SET proposal_qid = ? WHERE id = ?', (p['id'], rid))
        return self.get(rid)

    def sweep(self, days=SWEEP_DAYS, need=SWEEP_REPEATS, calls=SWEEP_CALLS, now=None):
        """The evening review (rules-review on the home server calls POST /api/rules/sweep): past answers the
        per-answer check missed. A kind of question whose last `need` answers in `days` were all the same
        (or all took the agent's pick), in one project or across projects, becomes a proposal like any
        other: same risk guard, his yes turns it on, it shows on Learned. At most `calls` model calls."""
        now = now or time.time()
        rows = [x for x in self.questions.list('answered', since=now - days * 86400, limit=5000)
                if x['kind'] not in NOT_COUNTED and x['answered_via'] in HIS_VIA]
        groups = {}
        for x in sorted(rows, key=lambda x: x['answered_at'] or 0):
            groups.setdefault((x['project'], x['kind']), []).append(x)
            groups.setdefault((None, x['kind']), []).append(x)
        out, asked, repeated, risky_n, covered_n = [], 0, 0, 0, 0
        # One project before every project: the narrower rule first; a group already held by a rule is skipped.
        for (project, kind), items in sorted(groups.items(), key=lambda g: (g[0][0] is None, g[0][1], g[0][0] or '')):
            last = items[-need:]
            if len(last) < need or len({answer_key(x) for x in last}) != 1:
                continue
            if project is None and len({x['project'] for x in last}) == 1:
                continue  # all from one project: that is the project's group, not a cross-project rule
            repeated += 1
            if self.any_risky(last):
                risky_n += 1
                continue
            with self.lock:
                if self.covered([x['id'] for x in last]):
                    covered_n += 1
                    continue
                if asked >= calls:
                    break
                asked += 1
                r = self.propose(last, project, kind)
            if r:
                out.append(r)
        proposed = [{'id': r['id'], 'summary': r['summary'], 'project': r['project'], 'kind': r['kind']} for r in out]
        with self.store.connect() as con:  # Learned shows the last run, so he sees it still runs and what it found
            con.execute('INSERT INTO rule_sweeps (at, days, answers, repeated, risky, covered, model_calls, proposed) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                        (now, days, len(rows), repeated, risky_n, covered_n, asked, json.dumps(proposed)))
        return {'proposed': proposed, 'model_calls': asked, 'answers': len(rows), 'repeated': repeated,
                'risky': risky_n, 'covered': covered_n}

    def candidates(self, q):
        with self.store.connect() as con:
            return [self.row(r) for r in con.execute(
                'SELECT * FROM rules WHERE status = ? AND kind = ? AND (project = ? OR project IS NULL)',
                ('active', q['kind'], q['project']))]

    def apply_to(self, q):
        """A new question: an active rule that decides it answers it, logged with why. A risky
        question only ever by a rule in his own words; unsure means he gets asked as usual."""
        rules = self.candidates(q)
        if not rules:
            return None
        hit = risky(q['question'], q['context'], q['default_answer'], *[o['label'] for o in q['options']])
        if hit:
            rules = [r for r in rules if r['source'] == 'his-words']
            if not rules:
                return None
        listing = '\n'.join(f'#{r["id"]}: {r["summary"]} -> answer: {r["answer"]}' for r in rules)
        v = self.model(MATCH_PROMPT.format(rules=listing, project=q['project'], kind=q['kind'], question=q['question'],
                                           options=', '.join(o['label'] for o in q['options']) or '-',
                                           default=q['default_answer'] or '-'))
        if not v or not v.get('match') or not v.get('answer') or not v.get('why'):
            return None
        rule = next((r for r in rules if r['id'] == v.get('rule_id')), None)
        if not rule:
            return None
        answer = str(v['answer'])[:400]
        if rule['takes_default']:
            if not q['default_answer']:
                return None  # a rule to take the agent's pick, and no pick to take
            answer = q['default_answer']
        now = time.time()
        why = str(v['why'])[:400]
        try:
            self.questions.answer(q['id'], {'answer': answer, 'note': f'Pravidlo #{rule["id"]}: {why}'},
                                  via='rule')
        except ValueError:  # answered or withdrawn meanwhile
            return None
        with self.store.connect() as con:
            con.execute('INSERT INTO rule_uses (rule_id, question_id, why, at) VALUES (?, ?, ?, ?)', (rule['id'], q['id'], why, now))
            con.execute('UPDATE rules SET fired = fired + 1, last_fired_at = ?, updated_at = ? WHERE id = ?', (now, now, rule['id']))
        return self.questions.get(q['id'])
