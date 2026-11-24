"""Questions: what agents need him to decide, all on one page (http://beacon/decisions), and every
answer kept as a decision, so a later review can turn answers he keeps repeating into standing
rules and stop the asking.

An agent asks with its own pick as the default and goes on working with it (`tasks ask`); only a
question with no safe default blocks: that agent does everything else and ends its turn, and
answer-dispatcher resumes it when he answers (task 1884). Three more ways in, so the page holds every question:
  - a `⏳` note on a task (the task agent's and the router's questions) becomes a question; his
    note on that task, wherever he wrote it (Telegram replies land as his notes), answers it;
  - an AskUserQuestion he answered in a chat is logged as an answered one (a PostToolUse hook);
  - `tasks answer` from a terminal.
The log is the answered ones: what was asked, the agent's default, his answer, and the kind.
"""
import base64
import hashlib
import json
import logging
import os
import re
import threading
import time

from settings import OWNER_ID, OWNER_NAME

try:
    from . import gate, pictures
    from .store import fold
except ImportError:  # run as a script from hub/
    import gate
    import pictures
    from store import fold

log = logging.getLogger('beacon.questions')

STATUSES = ('open', 'answered', 'withdrawn')
VIA = ('page', 'chat', 'task', 'cli', 'rule')  # rule: a standing rule he approved answered it (hub/rules.py)
KIND_RE = re.compile(r'[^a-z0-9]+')
QUESTION_MAX, OPTION_MAX, OPTIONS_MAX, TEXT_MAX = 400, 200, 8, 20000
ASK_NOTE = re.compile(r'^⏳\s*(?:Needs you:)?\s*', re.I)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    project TEXT NOT NULL,
    question TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    options TEXT NOT NULL DEFAULT '[]',
    default_answer TEXT,
    kind TEXT NOT NULL,
    blocking INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    answer TEXT,
    answer_note TEXT,
    standing INTEGER NOT NULL DEFAULT 0,
    answered_via TEXT,
    answered_at REAL,
    applied_at REAL,
    asked_by TEXT,
    session_id TEXT,
    task_id INTEGER,
    source TEXT UNIQUE,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS questions_status ON questions(status, updated_at);
CREATE INDEX IF NOT EXISTS questions_task ON questions(task_id, status);
CREATE TABLE IF NOT EXISTS gate_log (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    machine TEXT,
    session TEXT,
    tool TEXT,
    category TEXT,
    what TEXT,
    input TEXT,
    cwd TEXT,
    mode TEXT,
    approved TEXT,
    error TEXT,
    received_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS gate_log_ts ON gate_log(ts);
'''
# Columns added after the table first shipped (ALTER, once).
COLUMNS = (('blocking_reason', 'TEXT'), ('asked_blocking', 'INTEGER NOT NULL DEFAULT 0'),
           ('gate_blocking', 'INTEGER'), ('taken_by', 'TEXT'), ('waited_at', 'REAL'),
           ('see', "TEXT NOT NULL DEFAULT '[]'"), ('see_none', 'TEXT'), ('harvested_at', 'REAL'),
           ('handoff_at', 'REAL'), ('handoff_reason', 'TEXT'), ('project_asked', 'INTEGER NOT NULL DEFAULT 0'),
           ('steps', "TEXT NOT NULL DEFAULT '[]'"), ('lint', "TEXT NOT NULL DEFAULT '[]'"),
           ('flag', 'TEXT'), ('flagged_at', 'REAL'), ('later_until', 'REAL'))
# A card he can't answer without opening something (2026-11-22, four bare "Kdy to bude opravené?"): checked on every
# ask by these words, logged with his own 👎 flags in one place (`bad()`, Labs "bad_questions"); `tasks ask` is
# refused on them so the asking agent fixes the card before he ever sees it.
TASK_HEAD = re.compile(r'^Task #\d+: [^\n]*$')
# What he needs to see or try to decide (2026-11-22: "I can't decide without seeing it", and opening
# Claude to look is what Beacon exists to avoid): links shown as a big "Open it" button, pictures shown on the card.
STEPS_MAX, STEP_MAX = 12, 160  # a card he does by hand: a short checklist, each step one tap or one field
SEE_MAX, PICTURE_MAX = 12, 8 * 1024 * 1024  # a slideshow, not a stack (2026-11-22)
# Fewer pixels than ~1000 x 500 and he can't read it on any screen (2026-11-24: 390 x 179 game shots, "I can't see anything").
PICTURE_MIN_PIXELS = 500_000
PICTURE_TYPES = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp', 'gif': 'image/gif'}
IMAGE_URL = re.compile(r'\.(png|jpe?g|webp|gif)(\?.*)?$', re.I)
# A voice or sound to judge by ear (2026-11-23: "I need the voice actually there to be able to play it")
AUDIO_TYPES = {'mp3': 'audio/mpeg', 'm4a': 'audio/mp4', 'wav': 'audio/wav', 'ogg': 'audio/ogg'}
AUDIO_URL = re.compile(r'\.(mp3|m4a|wav|ogg)(\?.*)?$', re.I)
FEEDBACK_KIND = 'feedback'  # his marked-up screenshot from a page's feedback button: work to start, not a decision
DECISION_TAG = 'decision'  # the small task filed for a question asked outside any task (task 1776)
LATER_SECONDS = 5 * 3600  # his Later: quiet this long on every device (2026-11-24: "at least 5 hours")
FLAG_ANSWER = '👎 Makes no sense, ask again as a new card with what I need:'
DISPATCHER = 'answer-dispatcher'  # client/answer-dispatcher: its run works the task and closes it itself
ANSWER_AUTHOR = 'decisions'  # his page answer, copied onto the task as a note: not the owner's id, which would answer again
APPROVAL_VIA = ('page', 'task')  # his own hands: the questions page, a task note (Telegram). An agent logs cli and chat.
APPROVAL_SECONDS = 24 * 3600


def answered_via(requested, default, hands):
    """The channel an answer came through. A caller picks among the agent channels freely, but only his
    own login (`hands`) may say page or task: those count as his approval for risky actions."""
    via = requested or default
    if via not in VIA:
        raise ValueError('via: expected one of ' + ', '.join(VIA))
    if via in APPROVAL_VIA and not hands:
        raise ValueError(f'via: {via} is his own hands; an agent logs cli or chat')
    return via


def text(v, name, limit, required=False):
    if v is None or v == '':
        if required:
            raise ValueError(f'{name}: required')
        return None
    if not isinstance(v, str):
        raise ValueError(f'{name}: expected text')
    v = v.strip()
    if required and not v:
        raise ValueError(f'{name}: required')
    if len(v) > limit:
        raise ValueError(f'{name}: at most {limit} characters')
    return v or None


# "Not now" as an option is an answer, and an answer wakes an agent run only to learn he put it off. The deck's
# Later button already puts a card off and wakes nothing, so such options are dropped (2026-11-22).
# "Not yet" and "Haven't tried yet" are the same non-answer (cards ?277/?282, 2026-11-23).
PUT_OFF = re.compile(r"^(not (now|yet)|later|maybe later|skip for now|remind me later|(i )?haven'?t (tried|done|checked)( it)? yet"
                     r"|ne ?teď|teď ne|později|zatím ne|ještě ne|ještě jsem (to )?nezkusil)[.!]?$", re.I)


def put_off(label):
    return bool(PUT_OFF.match(' '.join(re.sub(r'\(recommended\)', '', str(label or ''), flags=re.I).split())))


def created_at(v):
    """An asked time (epoch seconds), or None for now."""
    if not v:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError('created_at: expected epoch seconds') from None


def default_of(default, options):
    """The agent's pick: the one it named, else the option marked (recommended), else the first; None when put off."""
    if put_off(default):
        default = None
    if default is None and options:
        picked = [o['label'] for o in options if '(recommended)' in o['label'].lower()]
        default = picked[0] if picked else options[0]['label']
    return default


def check_options(v):
    """[{label, detail}], from plain strings or objects; the agent's pick usually comes first. "Not now" and its
    kin are dropped: the deck's Later button does that without waking an agent."""
    if v in (None, ''):
        return []
    if not isinstance(v, list) or len(v) > OPTIONS_MAX:
        raise ValueError(f'options: expected a list of at most {OPTIONS_MAX}')
    out = []
    for o in v:
        if isinstance(o, str):
            o = {'label': o}
        if not isinstance(o, dict):
            raise ValueError('options: each is a label or {label, detail}')
        label = text(o.get('label'), 'options: label', OPTION_MAX, required=True)
        detail = text(o.get('detail'), 'options: detail', 1000)
        if put_off(label):
            continue
        if label.lower() not in (x['label'].lower() for x in out):
            out.append({'label': label, 'detail': detail} if detail else {'label': label})
    return out


def kind_of(v):
    """A short lowercase slug that groups like questions (merge, scope, design, naming...)."""
    k = KIND_RE.sub('-', str(v or '').lower()).strip('-')[:30].strip('-')
    return k or 'other'


def same(a, b):
    norm = lambda s: ' '.join(re.sub(r'\(recommended\)', '', str(s or ''), flags=re.I).lower().split()).strip(' .!')
    return bool(a) and bool(b) and norm(a) == norm(b)



def long_hub_link(s):
    """A long tailnet name in a card ("we dont use those ugly links no more", 2026-11-24): only short names, or ''."""
    m = re.search(r'\b[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\b', s or '', re.I)
    return m.group(0) if m else ''


# Whether a card still matters is judged twice a day by client/cards-check (a model reads it next to its task and
# the machines; moot ones leave with the reason, task 1989). Age is only the fallback: a card nobody answered in 7
# days leaves the deck with a note on its task (a "send it today" card from 2026-11-18 was still up 6 days later).
STALE_DAYS = 7
# A session's open card older than this, on the same task as a newer one he just answered, is behind the work
# (?328 "plug in a cable" stayed after the pad worked, 2026-11-23). Cards asked together, in one go, are kept.
SUPERSEDED_SEC = 600
# His own words on a task (an answer that is not the agent's pick), from the last days, are shown to an agent
# before it asks again on that task (?292, ?294 asked what he had answered; a TikTok slide card went 8 rounds).
SAID_DAYS, SAID_MAX = 7, 5
SAID_WHY = 'he already said on task {}'
STALE_NOTE = f'not answered in {STALE_DAYS} days: the card left the deck'


def said_text(task_id, said):
    """The refusal that shows an asking agent what he already said on its task."""
    lines = [f'he already answered on this task (#{task_id}) in his own words; read it before asking him again:']
    for q in said:
        when = time.strftime('%m-%d %H:%M', time.localtime(q['answered_at'] or q['created_at']))
        lines.append(f"- {when} „{q['question'][:120]}“ → he said: {' '.join(q['answer'].split())[:400]}")
    lines.append('If his words already answer your question, act on them and do not ask. Showing him something to look '
                 'at or approve again? First have a fresh reviewer (a helper that never saw your work) check it against '
                 'each point above, fix what it finds, and say on the card what changed for each. Then run the same '
                 '`tasks ask` again: it passes this time.')
    return '\n'.join(lines)


class Questions:
    def __init__(self, store, tasks=None):
        self.store = store
        self.tasks = tasks
        self.on_created = self.on_answered = None  # hub/rules.py listens: propose rules, answer by them
        self.cardrules = None  # hub/cardrules.py, set by the server: his 👎 teaches it, refusals are logged there
        self.harvesting = False  # the live hub (server.py) turns it on: tests never run `gh`
        with store.connect() as con:
            con.executescript(SCHEMA)
            have = {r[1] for r in con.execute('PRAGMA table_info(questions)')}
            for name, decl in COLUMNS:
                if name not in have:
                    con.execute(f'ALTER TABLE questions ADD COLUMN {name} {decl}')
        if tasks is not None:
            tasks.on_note = self.task_note
            tasks.on_close = self.withdraw_closed
            n = self.withdraw_closed()
            if n:
                log.info('withdrew %s open on tasks already closed', n)
        self.expired_at = 0
        self.expiring = threading.Lock()
        n = self.expire_stale()
        if n:
            log.info('%s open card(s) older than %s days left the deck', n, STALE_DAYS)

    @staticmethod
    def row(r):
        q = dict(r)
        q['options'] = [o for o in json.loads(q['options'] or '[]') if not put_off(o.get('label'))]
        q['blocking'], q['standing'] = bool(q['blocking']), bool(q['standing'])
        q['asked_blocking'] = bool(q.get('asked_blocking'))
        q['project_asked'] = bool(q.get('project_asked'))
        q['gate_blocking'] = None if q.get('gate_blocking') is None else bool(q['gate_blocking'])
        q['matched_default'] = same(q['answer'], q['default_answer']) if q['status'] == 'answered' else None
        q['see'] = json.loads(q.get('see') or '[]')
        q['steps'] = json.loads(q.get('steps') or '[]')
        q['lint'] = json.loads(q.get('lint') or '[]')
        # A question he must look at or try, with nothing on the card to look at: the card says so.
        q['needs_see'] = not q['see'] and not any(x.get('url') for x in q['steps']) and not q.get('see_none') and bool(gate.rules.needs_look(
            '\n'.join([q['question'], q['kind'] or '', *[o['label'] + ' ' + (o.get('detail') or '') for o in q['options']]])))
        return q

    # ------------------------------------------------------------ what he sees

    def files_dir(self):
        return os.path.join(os.path.dirname(os.path.abspath(self.store.path)), 'question-files')

    def check_see(self, v, to_read=True):
        """[{url|picture|audio, label}] from links (str or {url, label}) and uploaded pictures or sounds ({name, data: base64}).

        to_read: the card waits for him to look and decide, so a picture too small to read is refused; a card already
        answered (his own feedback screenshot, logged) keeps whatever it got."""
        if v in (None, ''):
            return []
        if not isinstance(v, list) or len(v) > SEE_MAX:
            raise ValueError(f'see: expected a list of at most {SEE_MAX}')
        out = []
        for x in v:
            if isinstance(x, str):
                x = {'url': x}
            if not isinstance(x, dict):
                raise ValueError('see: each is a link or {url, label} or {name, data}')
            label = text(x.get('label'), 'see: label', 120)
            if x.get('data') is not None:
                name = text(x.get('name'), 'see: name', 200, required=True)
                ext = name.rsplit('.', 1)[-1].lower()
                if ext not in PICTURE_TYPES and ext not in AUDIO_TYPES:
                    raise ValueError('see: a picture is png, jpg, webp or gif; a sound is mp3, m4a, wav or ogg')
                try:
                    data = base64.b64decode(x['data'], validate=True)
                except (ValueError, TypeError):
                    raise ValueError('see: data is not base64')
                if not data or len(data) > PICTURE_MAX:
                    raise ValueError('see: a picture is at most 8 MB')
                kind = 'audio' if ext in AUDIO_TYPES else 'picture'
                wh = pictures.size(data) if kind == 'picture' else None
                if to_read and wh and wh[0] * wh[1] < PICTURE_MIN_PIXELS:
                    raise ValueError(f'see: {name} is {wh[0]} x {wh[1]} px, too small to read: send the full window '
                                     'at deviceScaleFactor 2 (at least about 1000 x 500 px)')
                out.append({kind: self.save_picture(data, ext), **({'label': label} if label else {})})
                continue
            url = text(x.get('url'), 'see: url', 2000, required=True)
            if not re.match(r'^https?://', url):
                raise ValueError('see: a link starts with http:// or https://')
            item = {'picture': url} if IMAGE_URL.search(url) else {'audio': url} if AUDIO_URL.search(url) else {'url': url}
            out.append({**item, **({'label': label} if label else {})})
        return out

    @staticmethod
    def check_steps(v):
        """[{text, url?}]: what he does by hand, in order (2026-11-22: "a direct link and clear steps").
        A step is "text" or "text :: https://…" or {text, url}."""
        if v in (None, ''):
            return []
        if not isinstance(v, list) or len(v) > STEPS_MAX:
            raise ValueError(f'steps: expected a list of at most {STEPS_MAX}')
        out = []
        for x in v:
            if isinstance(x, str):
                t, _, u = x.rpartition('::') if re.search(r'::\s*(https?://|mailto:)', x) else (x, '', '')
                x = {'text': t, 'url': u}
            if not isinstance(x, dict):
                raise ValueError('steps: each is "text", "text :: url" or {text, url}')
            step = {'text': text(x.get('text'), 'steps: text', STEP_MAX, required=True)}
            url = text(x.get('url'), 'steps: url', 4000)
            if url:
                if not re.match(r'^(https?://|mailto:)', url):  # mailto: a mail he sends himself, filled in for him
                    raise ValueError('steps: a link starts with http://, https:// or mailto:')
                step['url'] = url
            out.append(step)
        return out

    def save_picture(self, data, ext):
        fname = f'{hashlib.sha256(data).hexdigest()[:16]}.{ext}'
        os.makedirs(self.files_dir(), exist_ok=True)
        with open(os.path.join(self.files_dir(), fname), 'wb') as f:
            f.write(data)
        return f'/api/questions/file/{fname}'

    def harvest(self, qid):
        """Fetch the screenshots a question only links to (a PR body, a GitHub image link) onto its card."""
        q = self.get(qid)
        if not q or q['status'] != 'open':
            return None
        text_ = '\n'.join([q['question'], q.get('context') or '', *[x.get('url') or '' for x in q['see']]])
        room = pictures.HARVEST_MAX - sum(1 for x in q['see'] if x.get('picture'))
        got = pictures.harvest(text_, room) if room > 0 and 'github.com' in text_ else []
        new = [{'picture': self.save_picture(data, ext), **({'label': label} if label else {})} for data, ext, label in got]
        with self.store.connect() as con:
            r = con.execute('SELECT see FROM questions WHERE id = ?', (qid,)).fetchone()
            see = json.loads(r['see'] or '[]')
            have = {x.get('picture') for x in see}
            see = (see + [x for x in new if x['picture'] not in have])[:SEE_MAX]
            con.execute('UPDATE questions SET see = ?, harvested_at = ?, updated_at = ? WHERE id = ?',
                        (json.dumps(see), time.time(), time.time(), qid))
        return self.get(qid)

    def harvest_later(self, qid):
        """In the background: `tasks ask` returns at once, the pictures land a few seconds later."""
        if self.harvesting:
            threading.Thread(target=self.notify, args=(self.harvest, qid), daemon=True).start()

    def harvest_open(self):
        """Once at start: open questions asked before this existed (or while gh was down) get their pictures."""
        with self.store.connect() as con:
            ids = [r['id'] for r in con.execute("SELECT id FROM questions WHERE status = 'open' AND harvested_at IS NULL "
                                                "AND (question || context || see) LIKE '%github.com%'")]
        for qid in ids:
            self.notify(self.harvest, qid)

    def add_see(self, qid, fields):
        """More to look at, added to an asked question (the agent found the preview, a backfill); `steps` and `context` replace."""
        q = self.get(qid)
        if not q:
            return None
        see = (q['see'] + self.check_see(fields.get('see')))[:SEE_MAX]
        # Steps are one checklist: new ones replace the old, so a rewrite never doubles them.
        steps = self.check_steps(fields['steps']) if 'steps' in fields else q['steps']
        # A card asked without what he needs (the mail's text, why) gets it backfilled: the context is replaced.
        context = text(fields.get('context'), 'context', TEXT_MAX) if fields.get('context') else q['context']
        # The asker holds the work after all: the same card turns blocking (raised only, never lowered). Withdrawing
        # and asking again showed him the card twice (four times on 2026-11-23).
        raise_ = fields.get('blocking') is True and q['status'] == 'open'
        with self.store.connect() as con:
            con.execute('UPDATE questions SET see = ?, steps = ?, context = ?, updated_at = ?' + (
                        ', blocking = 1, asked_blocking = 1' if raise_ else '') + ' WHERE id = ?',
                        (json.dumps(see), json.dumps(steps), context, time.time(), qid))
        self.harvest_later(qid)  # a PR link added later brings its screenshots too
        return self.get(qid)

    def file(self, name):
        """(bytes, content type) of a picture or sound uploaded for a question, else None."""
        if not re.fullmatch(r'[0-9a-f]{16}\.(png|jpe?g|webp|gif|mp3|m4a|wav|ogg)', name or ''):
            return None
        try:
            with open(os.path.join(self.files_dir(), name), 'rb') as f:
                return f.read(), {**PICTURE_TYPES, **AUDIO_TYPES}[name.rsplit('.', 1)[-1]]
        except OSError:
            return None

    # ------------------------------------------------------------ card quality

    def lint(self, question, context, options, default, steps, see):
        """Why a new card would leave him guessing (plain words, for the asker), or [] when it reads on its own."""
        why = []
        if not (context or '').strip() or TASK_HEAD.match((context or '').strip()):
            why.append('no context: say what it is about and what each answer does (-c, 2-4 bullets)')
        if not options and not default and not steps:
            why.append('nothing to tap: give 2-4 options or a default he can accept in one tap')
        with self.store.connect() as con:
            twins = con.execute("SELECT COUNT(*) FROM questions WHERE status = 'open' AND question = ?", (question,)).fetchone()[0]
        if twins:
            why.append(f'the same words as {twins} open card(s): name the thing it is about in the question itself')
        long = long_hub_link(' '.join([question or '', context or '', json.dumps(steps or []), json.dumps(see or [])]))
        if long:
            why.append(f'a long hub link ({long}): use the short one, http://{long.split(".")[0]}/<page>')
        return why

    def flag(self, qid, note=None):
        """His 👎 on a card: a bad question, kept with his words (one tap; again with no note takes it back)."""
        q = self.get(qid)
        if not q:
            return None
        note = text(note, 'note', 500)
        on = bool(note) or not q.get('flagged_at')
        with self.store.connect() as con:
            con.execute('UPDATE questions SET flag = ?, flagged_at = ?, updated_at = ? WHERE id = ?',
                        (note if on else None, time.time() if on else None, time.time(), qid))
        if self.cardrules is not None:  # his 👎 teaches the rules every agent's cards are written under
            (self.cardrules.flagged(q, note) if on else self.cardrules.taken_back(q))
        if on and note and q['status'] == 'open':
            # His words close the card (2026-11-24: "the card doesn't disappear"): they go back as its answer, so the
            # asker or answer-dispatcher wakes and asks again as a new card. The task comment below says how.
            self.answer(qid, {'answer': f'{FLAG_ANSWER} {note}'}, via='page')
            return self.get(qid)
        if on and q.get('task_id') and self.tasks is not None:  # the asking session's task hears it: ask it again, better
            self.tasks.comment(q['task_id'], f"👎 He flagged card ?{qid} as a bad question" + (f': {note}' if note else '')
                               + '. Ask it again with what he needs to answer it (what it is about, what each answer does).',
                               ANSWER_AUTHOR)
        return self.get(qid)

    def bad(self, since=None, limit=100):
        """The one log of bad cards: his flags first, then the ones the lint caught, newest first."""
        since = since or time.time() - 30 * 86400
        with self.store.connect() as con:
            rows = con.execute("SELECT * FROM questions WHERE created_at >= ? AND (flagged_at IS NOT NULL OR lint != '[]') "
                               'ORDER BY flagged_at IS NULL, COALESCE(flagged_at, created_at) DESC LIMIT ?',
                               (since, limit)).fetchall()
        return [self.row(r) for r in rows]

    # ------------------------------------------------------------ writes

    def create(self, fields, author='agent', hands=True):
        """File a question. `hands`: the caller is his own login (an HTTP request with the token is not; code
        inside the hub is), which alone may file it born answered through page or task.
        Everything is checked before anything is written: a refused ask files no task and no card."""
        ask = self.read_ask(fields, hands)
        if ask['source']:
            with self.store.connect() as con:
                hit = con.execute('SELECT id FROM questions WHERE source = ?', (ask['source'],)).fetchone()
            if hit:
                return self.get(hit['id'])
        if ask['task_id'] is None and self.tasks is not None:
            # Every question belongs to a task, so an answer always has work to resume (task 1776): the
            # asking session's own task, else a small one filed for it (below, once the ask passed).
            ask['task_id'] = self.session_task(ask['session_id'])
            if ask['task_id'] is not None and not ask['project_asked']:
                ask['project'] = self.tasks.get(ask['task_id'])['project']
        lint = [] if ask['answer'] else self.lint(ask['question'], ask['context'], ask['options'], ask['default'],
                                                  ask['steps'], ask['see'])
        if fields.get('strict') and not ask['answer']:
            self.refuse_if_unfit(ask, lint, author)
        if ask['task_id'] is None and self.tasks is not None and not ask['answer']:
            ask['task_id'] = self.file_task(ask['question'], fields.get('context'), ask['project'], ask['session_id'])
        cols = self.question_row(ask, fields, lint, author)
        with self.store.connect() as con:
            names = list(cols)
            cur = con.execute(f'INSERT INTO questions ({", ".join(names)}) VALUES ({", ".join("?" * len(names))})',
                              [cols[n] for n in names])
            qid = cur.lastrowid
        if not ask['answer'] and ask['session_id'] and ask['kind'] != 'ending':
            self.withdraw_ending_cards(ask['session_id'], qid)
        q = self.get(qid)
        self.notify(self.on_answered if ask['answer'] else self.on_created, q)
        if not ask['answer']:
            self.harvest_later(qid)
        return q

    def read_ask(self, fields, hands):
        """The asked fields, each validated; raises ValueError naming the first bad one. Writes nothing."""
        if not isinstance(fields, dict):
            raise ValueError('body: expected a JSON object')
        ask = {
            'question': text(fields.get('question'), 'question', QUESTION_MAX, required=True),
            'options': check_options(fields.get('options')),
            'see': self.check_see(fields.get('see'), to_read=not fields.get('answer')),  # a bad picture refuses the whole ask
            'steps': self.check_steps(fields.get('steps')),
        }
        ask['default'] = default_of(text(fields.get('default'), 'default', OPTION_MAX), ask['options'])
        task_id = fields.get('task_id')
        if task_id is not None and (isinstance(task_id, bool) or not isinstance(task_id, int)):
            raise ValueError('task_id: expected a task number')
        project = fields.get('project')
        # The asker named the project (`tasks ask -p`), so it is where his yes is carried out; otherwise the project is
        # only the folder the session happens to be in (2026-11-22 review: a drifted folder is not other work).
        project_asked = bool(project and fields.get('project_asked'))
        if task_id is not None and self.tasks is not None:
            t = self.tasks.get(task_id)
            if not t:
                raise ValueError('task_id: no such task')
            # A question on a task is about that task's project; the asking session's folder only counts when
            # the asker named it (a Kha'Zix card filed from a session in the Beacon folder showed as Beacon).
            project = project if project_asked else t['project']
        ask.update(
            task_id=task_id, project=project, project_asked=project_asked,
            answer=text(fields.get('answer'), 'answer', TEXT_MAX),  # a question answered elsewhere, logged
            session_id=text(fields.get('session_id'), 'session_id', 200),
            source=text(fields.get('source'), 'source', 200),
            kind=kind_of(fields.get('kind')),
            context=text(fields.get('context'), 'context', TEXT_MAX) or '',
            asked_by=str(fields.get('asked_by') or '')[:80],
            see_none=text(fields.get('see_none'), 'see_none', 300),
            note=text(fields.get('note'), 'note', TEXT_MAX),
            created_at=created_at(fields.get('created_at')),
        )
        ask['via'] = answered_via(fields.get('via'), 'chat', hands) if ask['answer'] else None
        return ask

    def refuse_if_unfit(self, ask, lint, author):
        """`tasks ask` (strict): a card that would leave him guessing, or one he already answered in other
        words, is refused before he sees it, and the refusal goes to the check log."""
        asked_by = ask['asked_by'] or str(author)[:80]
        if lint:
            self.log_refusal(ask, '; '.join(lint), asked_by)
            raise ValueError('this card would leave him guessing: ' + '; '.join(lint))
        said = self.already_said(ask['task_id'], ask['session_id'])
        if said:  # the next ask from this session on this task passes: it has read them
            self.log_refusal(ask, SAID_WHY.format(ask['task_id']), asked_by)
            raise ValueError(said_text(ask['task_id'], said))

    def log_refusal(self, ask, why, asked_by):
        if self.cardrules is not None:
            self.cardrules.log('ask', 'refused', {'question': ask['question']}, why=why,
                               session_id=ask['session_id'], asked_by=asked_by)

    def question_row(self, ask, fields, lint, author):
        """The columns of the new questions row."""
        options, default, answer = ask['options'], ask['default'], ask['answer']
        # Layer 1 of the gate: the hub decides, by kind and words, whatever the agent said. In shadow
        # mode (the default, until he says "zapni") it only records what it would have decided.
        words = [ask['question'], ask['context'], default, *[o['label'] + ' ' + (o.get('detail') or '') for o in options]]
        gate_blocking, why = gate.question_blocks(ask['kind'], words, bool(fields.get('blocking')), default)
        # With no default the agent has nothing to go on with: it is waiting, whatever it said.
        blocking = gate_blocking if gate.rules.gate_mode() == 'enforce' else bool(fields.get('blocking')) or default is None
        now = time.time()
        cols = {
            'see': json.dumps(ask['see']),
            'steps': json.dumps(ask['steps']),
            'lint': json.dumps(lint),
            'see_none': ask['see_none'],
            'project': self.tasks.project(ask['project']) if self.tasks is not None else (ask['project'] or 'personal'),
            'question': ask['question'],
            'context': ask['context'],
            'options': json.dumps(options),
            'default_answer': default,
            'kind': ask['kind'],
            'blocking': int(blocking),
            'blocking_reason': why,
            'gate_blocking': int(gate_blocking),
            'asked_blocking': int(bool(fields.get('blocking'))),
            'status': 'answered' if answer else 'open',
            'asked_by': ask['asked_by'] or str(author)[:80],
            'session_id': ask['session_id'],
            'task_id': ask['task_id'],
            'project_asked': int(ask['project_asked']),
            'source': ask['source'],
            'created_at': ask['created_at'] or now,
            'updated_at': now,
        }
        if answer:
            cols.update(answer=answer, answer_note=ask['note'],
                        standing=int(bool(fields.get('standing'))), answered_via=ask['via'],
                        answered_at=now,
                        # his feedback (hub/feedback.py) is born answered and still waits for the dispatcher's run
                        applied_at=None if ask['kind'] == FEEDBACK_KIND else now)
        return cols

    def withdraw_ending_cards(self, session_id, qid):
        """The session filed its own decision: the deck card the hub made from its reply (hub/endings.py) goes."""
        with self.store.connect() as con:
            cards = [r['id'] for r in con.execute("SELECT id FROM questions WHERE session_id = ? AND status = 'open' "
                                                  "AND kind = 'ending' AND id != ?", (session_id, qid))]
        for c in cards:
            self.withdraw(c, 'the session filed its own decision')

    def already_said(self, task_id, session_id):
        """His own words on this task (answers that were not the agent's pick) this session has not been shown yet:
        newest first, at most SAID_MAX. Shown once per session and task, and again after each new answer of his."""
        if task_id is None or not session_id or self.cardrules is None:
            return []
        now = time.time()
        with self.store.connect() as con:
            shown = con.execute("SELECT MAX(ts) FROM card_checks WHERE session_id = ? AND kind = 'ask' AND verdict = 'refused' "
                                'AND why = ?', (session_id, SAID_WHY.format(task_id))).fetchone()[0]
            rows = con.execute("SELECT * FROM questions WHERE task_id = ? AND status = 'answered' AND answered_at > ? "
                               "AND kind != ? ORDER BY answered_at DESC LIMIT 40",
                               (task_id, max(shown or 0, now - SAID_DAYS * 86400), FEEDBACK_KIND)).fetchall()
        return [q for q in map(self.row, rows) if not q['matched_default'] and (q['answer'] or '').strip()][:SAID_MAX]

    def expire_stale(self, now=None):
        """Open cards older than STALE_DAYS leave the deck (withdrawn, a note on the task). Returns how many."""
        now = now or time.time()
        self.expired_at = now
        if not self.expiring.acquire(blocking=False):  # another request is on it
            return 0
        try:
            # Blocking cards and to-dos stay: an agent holds work on them, and a withdrawn one would stall it unseen.
            with self.store.connect() as con:
                rows = con.execute("SELECT id, task_id, question FROM questions WHERE status = 'open' AND created_at < ? "
                                   "AND blocking = 0 AND COALESCE(steps, '[]') IN ('', '[]')",
                                   (now - STALE_DAYS * 86400,)).fetchall()
            rows = [r for r in rows if self.withdraw(r['id'], STALE_NOTE)['answer_note'] == STALE_NOTE]
        finally:
            self.expiring.release()
        for r in rows:  # only the ones this call took off (an answer landing meanwhile wins)
            if r['task_id'] and self.tasks is not None and self.tasks.get(r['task_id']):
                self.tasks.comment(r['task_id'], f"🧹 Card ?{r['id']} „{r['question'][:120]}“ sat unanswered {STALE_DAYS} days and "
                                   'left his deck. Still needed? Ask it again, fresh, with what changed.', ANSWER_AUTHOR)
        return len(rows)

    def supersede(self, q):
        """He answered q: the same session's older open cards on that task, asked in an earlier turn, are behind
        the work and leave the deck. To-do cards (steps he ticks) and blocking ones (an agent holds work on them:
        a withdrawn one would stall it unseen) stay."""
        if not q.get('session_id') or not q.get('task_id'):
            return
        with self.store.connect() as con:
            ids = [r['id'] for r in con.execute(
                "SELECT id FROM questions WHERE session_id = ? AND task_id = ? AND status = 'open' AND id != ? "
                "AND created_at < ? AND blocking = 0 AND COALESCE(steps, '[]') IN ('', '[]')",
                (q['session_id'], q['task_id'], q['id'], q['created_at'] - SUPERSEDED_SEC))]
        for qid in ids:
            self.withdraw(qid, f"he answered a newer card of the same session (?{q['id']})", drop=False)
        if ids and self.tasks is not None:  # he decided on it: a hub-filed task closes done once his answer is taken
            self.drop_emptied(q['task_id'], 'done')

    @staticmethod
    def notify(fn, q):
        if fn:
            try:
                fn(q)
            except Exception:  # noqa: BLE001 - learning never breaks asking or answering
                log.exception('notify failed')

    def session_task(self, session_id):
        """The open task the session is linked to (the newest link), else None."""
        if not session_id:
            return None
        with self.store.connect() as con:
            r = con.execute("SELECT s.task_id FROM task_sessions s JOIN tasks t ON t.id = s.task_id "
                            "WHERE s.session_id = ? AND t.status IN ('todo', 'doing') "
                            "ORDER BY s.linked_at DESC, s.task_id DESC LIMIT 1", (session_id,)).fetchone()
        return r['task_id'] if r else None

    def file_task(self, question, context, project, session_id):
        """A question asked outside any task: a small task holds it, so the answer resumes something."""
        q = ' '.join(question.split())
        title = 'Decide: ' + (q if len(q) <= 62 else q[:61].rstrip() + '…')
        body = (f'## Goal\nHe decides: {q}\n\n## Done-test\n- His answer is carried out and noted here.\n\n'
                f'## Steps\n1. 👤 {OWNER_NAME}: answer it on http://beacon/decisions.\n2. 🤖 The answer dispatcher carries it out.\n\n'
                f'## Context\nFiled by the hub for a decision asked outside a task'
                + (f' (session {session_id})' if session_id else '') + '.'
                + (f'\n\n{context.strip()}' if isinstance(context, str) and context.strip() else ''))
        t = self.tasks.create({'title': title, 'body': body, 'project': project, 'tags': [DECISION_TAG]}, 'agent')
        if session_id:
            self.tasks.link(t['id'], session_id)
        return t['id']

    def note_answer(self, q):
        """His answer, also a note on its task once it is taken: the task's history says what he decided."""
        if self.tasks is None or not q.get('task_id') or q['answered_via'] == 'task':
            return  # an answer given on the task is already its note
        line = f"✅ He decided ?{q['id']} „{q['question'][:160]}“: {q['answer']}"
        if q.get('answer_note'):
            line += f"\n{q['answer_note']}"
        self.tasks.comment(q['task_id'], line, ANSWER_AUTHOR)
        if q.get('taken_by') != DISPATCHER:  # taken by its live chat: the filed task held only the question
            self.drop_emptied(q['task_id'], 'done')

    def answer(self, qid, fields, via='page', hands=True):
        """His answer: an option's label or his own words, a note, and whether it should become a
        standing rule. A task's question answered here is passed to the task agent, which applies it
        the way it applies a Telegram reply (applied_at stays empty until it has). `hands`: as in create()."""
        if not isinstance(fields, dict):
            raise ValueError('body: expected a JSON object')
        q = self.get(qid)
        if not q:
            return None
        if q['status'] != 'open':
            raise ValueError(f'status: this question is already {q["status"]}')
        answer = text(fields.get('answer'), 'answer', TEXT_MAX, required=True)
        via = answered_via(fields.get('via'), via, hands)
        now = time.time()
        # Taken at once only when he answered in the asking session's own chat (it carries on); every other
        # answer, his note on the task too, waits for the asker or the dispatcher to claim it (task 1776).
        applied = now if via == 'chat' else None
        with self.store.connect() as con:
            cur = con.execute('UPDATE questions SET status = ?, answer = ?, answer_note = ?, standing = ?, answered_via = ?, '
                        'answered_at = ?, applied_at = ?, updated_at = ? WHERE id = ? AND status = ?',
                        ('answered', answer, text(fields.get('note'), 'note', TEXT_MAX), int(bool(fields.get('standing'))),
                         via, now, applied, now, qid, 'open'))
        if not cur.rowcount:  # answered in the meantime, by another device or a relay
            raise ValueError('status: this question was just answered')
        q = self.get(qid)
        if applied:
            self.notify(self.note_answer, q)
        self.notify(self.supersede, q)
        self.notify(self.on_answered, q)
        return q

    def reopen(self, qid):
        """Undo an answer the asker hasn't taken yet."""
        q = self.get(qid)
        if not q:
            return None
        if q['status'] == 'open':
            return q
        if q['applied_at']:
            raise ValueError('status: the agent already took this answer')
        with self.store.connect() as con:
            con.execute('UPDATE questions SET status = ?, answer = NULL, answer_note = NULL, standing = 0, '
                        'answered_via = NULL, answered_at = NULL, handoff_at = NULL, handoff_reason = NULL, '
                        'later_until = NULL, updated_at = ? '
                        'WHERE id = ?', ('open', time.time(), qid))
        return self.get(qid)

    def later(self, qid, off=False):
        """His Later: the card leaves the deck, the badge and talk for LATER_SECONDS on every device,
        then comes back by itself. It stays open: the asker still waits, nobody is woken.
        `off` brings it back now."""
        with self.store.connect() as con:
            con.execute('UPDATE questions SET later_until = ?, updated_at = ? WHERE id = ? AND status = ?',
                        (None if off else time.time() + LATER_SECONDS, time.time(), qid, 'open'))
        return self.get(qid)

    def reword(self, qid, question, options, default, context):
        """An open card rewritten as a decision (hub/endings.py: a session's reply turned into question + buttons).
        Never over an answer that landed meanwhile. Returns the card, or None when it is no longer open."""
        question = text(question, 'question', QUESTION_MAX, required=True)
        options = check_options(options)
        default = text(default, 'default', OPTION_MAX)
        if default not in [o['label'] for o in options]:
            default = options[0]['label'] if options else None
        with self.store.connect() as con:
            cur = con.execute("UPDATE questions SET question = ?, options = ?, default_answer = ?, context = ?, updated_at = ? "
                              "WHERE id = ? AND status = 'open'",
                              (question, json.dumps(options, ensure_ascii=False), default,
                               text(context, 'context', TEXT_MAX) or '', time.time(), qid))
        return self.get(qid) if cur.rowcount else None

    def withdraw(self, qid, note=None, drop=True):
        """The asker no longer needs it (it found out, or the work moved on), or he dismissed it."""
        q = self.get(qid)
        if not q:
            return None
        if q['status'] == 'open':
            with self.store.connect() as con:  # never over an answer that landed meanwhile
                cur = con.execute('UPDATE questions SET status = ?, answer_note = ?, updated_at = ? WHERE id = ? AND status = ?',
                                  ('withdrawn', text(note, 'note', TEXT_MAX), time.time(), qid, 'open'))
            if cur.rowcount and drop and q['task_id'] and self.tasks is not None:
                self.drop_emptied(q['task_id'])
        return self.get(qid)

    def drop_emptied(self, task_id, status='dropped'):
        """A task the hub filed only to hold a question, with nothing left to decide or do: closed (dropped
        when withdrawn, done when his answer was taken by the asking chat). A run answer-dispatcher starts
        closes its own."""
        t = self.tasks.get(task_id)
        if not t or t['status'] != 'todo' or DECISION_TAG not in t['tags']:
            return
        with self.store.connect() as con:
            left = con.execute("SELECT count(*) FROM questions WHERE task_id = ? AND (status = 'open' OR "
                               "(status = 'answered' AND applied_at IS NULL))", (task_id,)).fetchone()[0]
        if not left:
            self.tasks.update(task_id, {'status': status})

    def applied(self, qid):
        """The asker took the answer (`tasks wait` got it, the task agent applied it)."""
        with self.store.connect() as con:
            cur = con.execute('UPDATE questions SET applied_at = ? WHERE id = ? AND status = ? AND applied_at IS NULL',
                              (time.time(), qid, 'answered'))
        q = self.get(qid)
        if cur.rowcount:
            self.notify(self.note_answer, q)
        return q

    def release(self, qid, by):
        """Give a claimed answer back, only by the one that claimed it: its run died before carrying it out
        (killed, rebooted), so the next tick runs it again. Returns (released, question)."""
        with self.store.connect() as con:
            cur = con.execute('UPDATE questions SET applied_at = NULL, taken_by = NULL WHERE id = ? AND status = ? '
                              'AND taken_by = ?', (qid, 'answered', str(by or '')[:80]))
        return bool(cur.rowcount), self.get(qid)

    def claim(self, qid, by):
        """Take an answer to carry it out, atomically: of a waiting asker and the dispatcher only one wins,
        so an answer is never carried out twice. Returns (won, question)."""
        with self.store.connect() as con:
            cur = con.execute('UPDATE questions SET applied_at = ?, taken_by = ? WHERE id = ? AND status = ? '
                              'AND applied_at IS NULL', (time.time(), str(by or '?')[:80], qid, 'answered'))
        q = self.get(qid)
        if cur.rowcount:  # taken: now it is his decision on the task (an undo before this leaves no note)
            self.notify(self.note_answer, q)
        return bool(cur.rowcount), q

    def handoff(self, qid, reason):
        """The asking session lets an answer go to a fresh session instead of carrying it out itself (other
        project, or too big and idle to resume well: ai-brain claude-code-session-size, 2026-11-22). The
        dispatcher then starts the run without waiting for that chat. Only an answer nobody has taken yet."""
        why = text(reason, 'reason', 300) or 'handed off'
        with self.store.connect() as con:
            con.execute('UPDATE questions SET handoff_at = ?, handoff_reason = ?, waited_at = NULL '
                        'WHERE id = ? AND status = ? AND applied_at IS NULL', (time.time(), why, qid, 'answered'))
        return self.get(qid)

    def waiting(self, qid):
        """`tasks wait` is polling for this question: the live asker gets the answer, not a fresh run."""
        with self.store.connect() as con:
            con.execute('UPDATE questions SET waited_at = ? WHERE id = ?', (time.time(), qid))
        return self.get(qid)

    def task_note(self, task_id, note):
        """A note on a task: an agent's `⏳` question becomes a question here (replacing an older
        open one on that task); his own note answers the open one."""
        author, body = note['author'], note['text']
        with self.store.connect() as con:
            open_ids = [r['id'] for r in con.execute(
                'SELECT id FROM questions WHERE task_id = ? AND status = ? ORDER BY id', (task_id, 'open'))]
        if author == OWNER_ID:
            for qid in open_ids:
                self.answer(qid, {'answer': body[:TEXT_MAX]}, via='task')
            return
        if not body.startswith('⏳'):
            return
        for qid in open_ids:
            self.withdraw(qid, 'asked again on the task')
        t = self.tasks.get(task_id)
        if t['status'] in ('done', 'dropped'):  # asked after the close (a run still going): nobody waits on it
            return
        asked = ASK_NOTE.sub('', body).strip() or body
        # The first line is the card's question, the lines under it its context (what he needs to answer it).
        line, _, rest = asked.partition('\n')
        more = asked if len(line) > QUESTION_MAX else rest.strip()
        self.create({
            'question': line if len(line) <= QUESTION_MAX else line[:QUESTION_MAX - 1].rstrip() + '…',
            'context': f'Task #{task_id}: {t["title"]}' + (f'\n\n{more}' if more else ''),
            'kind': 'task', 'task_id': task_id, 'asked_by': author, 'blocking': True,
            'session_id': (t['sessions'][-1]['session_id'] if t['sessions'] else None),
            'source': f'task-note:{note["id"]}',
        }, author)

    def withdraw_closed(self, task_id=None):
        """A task done or dropped (`task_id`, called once it is closed; or all of them, on start):
        nobody waits on its open questions any more. Answered ones stay in the log, and reopening
        the task doesn't bring the withdrawn ones back. Idempotent."""
        with self.store.connect() as con:
            ids = [r['id'] for r in con.execute(
                "SELECT q.id FROM questions q JOIN tasks t ON t.id = q.task_id "
                "WHERE q.status = 'open' AND t.status IN ('done', 'dropped')"
                + (' AND q.task_id = ?' if task_id is not None else ''), () if task_id is None else (task_id,))]
        for qid in ids:
            self.withdraw(qid, 'task closed')
        return len(ids)

    # ------------------------------------------------------------ the gate's approvals and log

    def approval(self, session, category, now=None):
        """Layer 2 asks: did he OK this category of action for this session? A question of this
        session (or of a task this session works on) he answered with his own hands in the last day,
        about this category (by its words or its kind), and not with a no. Returns that question."""
        if not session or category not in gate.CATEGORIES:
            return None
        now = now or time.time()
        with self.store.connect() as con:
            rows = [self.row(r) for r in con.execute(
                'SELECT * FROM questions WHERE status = ? AND answered_via IN (?, ?) AND answered_at >= ? AND kind IS NOT ? '
                'AND (session_id = ? OR task_id IN (SELECT task_id FROM task_sessions WHERE session_id = ?)) '
                'ORDER BY answered_at DESC', ('answered', *APPROVAL_VIA, now - APPROVAL_SECONDS, FEEDBACK_KIND, session, session))]
        for q in rows:
            about = gate.risk_words('\n'.join((q['question'], q['context'] or '', q['default_answer'] or '')))
            if category not in about and q['kind'] != category:
                continue
            if gate.NOT_YES.search(gate.fold(q['answer'])) or gate.fold(q['answer']).strip().startswith(('ne', 'no')):
                continue
            return q
        return None

    def log_gate(self, rec):
        if not isinstance(rec, dict):
            raise ValueError('body: expected a JSON object')
        cut = lambda k, n: (str(rec.get(k))[:n] if rec.get(k) is not None else None)
        with self.store.connect() as con:
            con.execute('INSERT INTO gate_log (ts, machine, session, tool, category, what, input, cwd, mode, approved, error, '
                        'received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (float(rec.get('ts') or time.time()), cut('machine', 64), cut('session', 200), cut('tool', 120),
                         cut('category', 20), cut('what', 200), cut('input', 600), cut('cwd', 300), cut('mode', 10),
                         cut('approved', 300), cut('error', 300) or cut('hub_error', 100), time.time()))
        return {'ok': True}

    def gate_report(self, since=None, limit=200):
        """The shadow log: what the gate flagged, per machine and category, and the latest lines."""
        since = since or time.time() - 7 * 86400
        with self.store.connect() as con:
            rows = [dict(r) for r in con.execute('SELECT * FROM gate_log WHERE ts >= ? ORDER BY ts DESC', (since,))]
        per = {}
        for r in rows:
            k = per.setdefault((r['machine'] or '?', r['category'] or 'error'), {'flagged': 0, 'approved': 0, 'unapproved': 0})
            k['flagged'] += 1
            k['approved' if r['approved'] else 'unapproved'] += 1
        return {'since': since, 'total': len(rows),
                'by': [{'machine': m, 'category': c, **v} for (m, c), v in sorted(per.items())],
                'latest': rows[:max(1, min(int(limit), 2000))]}

    # ------------------------------------------------------------ reads

    def get(self, qid):
        with self.store.connect() as con:
            r = con.execute('SELECT * FROM questions WHERE id = ?', (qid,)).fetchone()
        return self.row(r) if r else None

    def list(self, status='open', project=None, kind=None, q=None, since=None, task=None, pending=False, limit=500, session=None):
        if time.time() - self.expired_at > 3600:  # the deck asks every few seconds: stale cards go once an hour
            self.notify(lambda _: self.expire_stale(), None)  # never fails the deck's own read
        where, params = [], []
        if status in (None, '', 'open'):
            statuses = ('open',)
        elif status == 'all':
            statuses = STATUSES
        else:
            statuses = tuple(s for s in str(status).split(',') if s in STATUSES)
            if not statuses:
                raise ValueError('status: expected open, all or ' + ', '.join(STATUSES))
        where.append(f'status IN ({", ".join("?" * len(statuses))})')
        params += statuses
        if project:
            where.append('project = ?')
            params.append(self.tasks.project(project) if self.tasks is not None else project)
        if kind:
            where.append('kind = ?')
            params.append(kind_of(kind))
        if since:
            where.append('coalesce(answered_at, created_at) >= ?')
            params.append(float(since))
        if task is not None:
            where.append('task_id IS NOT NULL' if task is True else 'task_id = ?')
            params += [] if task is True else [int(task)]
        if session:  # what one session asked (its page in Usage links them)
            where.append('session_id = ?')
            params.append(session)
        if pending:  # answered, not yet taken by whoever asked
            where.append('applied_at IS NULL')
        order = ('blocking DESC, created_at DESC, id DESC' if 'open' in statuses  # what to answer first
                 else 'coalesce(answered_at, updated_at) DESC, id DESC')  # the log: latest decision first
        with self.store.connect() as con:
            rows = [self.row(r) for r in con.execute(f'SELECT * FROM questions WHERE {" AND ".join(where)} ORDER BY {order}', params)]
        if q:
            needle = fold(q)
            rows = [x for x in rows if needle in fold('\n'.join((x['question'], x['context'], x['answer'] or '', x['answer_note'] or '')))]
        return rows[:max(1, min(int(limit), 5000))]

    def awake(self, limit=500):
        """The open cards he sees now: those he put off with Later stay out until their later_until passes."""
        now = time.time()
        return [q for q in self.list('open', limit=limit) if not (q.get('later_until') or 0) > now]

    def kinds(self, since=None):
        """Per kind: how often he answered, how often he took the agent's default, how often he
        said make it a rule. A kind he always settles the agent's way is the next rule to write."""
        out = {}
        for x in self.list('answered', since=since, limit=5000):
            k = out.setdefault(x['kind'], {'kind': x['kind'], 'answered': 0, 'took_default': 0, 'standing': 0, 'last': 0})
            k['answered'] += 1
            k['took_default'] += bool(x['matched_default'])
            k['standing'] += x['standing']
            k['last'] = max(k['last'], x['answered_at'] or 0)
        return sorted(out.values(), key=lambda k: (-k['answered'], k['kind']))
