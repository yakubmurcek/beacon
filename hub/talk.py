"""Talk to Beacon: a spoken brief, flowing voice turns and spoken answers to decisions (Labs talk mode).

The same talk mode runs in any browser (phone, desk, an always-on wall screen), so the device does not matter:
- Listening is on the hub. The page records each spoken turn (its own voice detection, echo cancel on) and posts
  the clip here; OpenAI transcribes it. Browser speech recognition differs per browser and is missing on Linux
  Chromium, so it is not used.
- Speaking is on the hub too: TALK_TTS_URL (a self-hosted voice, bearer TALK_TTS_TOKEN) when set and answering,
  else OpenAI TTS. The voice answers 503 for a render under its quality bar, so a bad clip is never played or
  cached; a talk page opening warms it (TALK_TTS_WARM_URL). Its misses are counted and alarm. Keys never reach the page.
- The brief is built from live data with no model call, so it starts at once. It ends on a question.
- Acting: a decision answered by voice goes through the same path as a tap, after the page read it back and he
  said yes. Nothing sends, pays or submits.
- Every second heard and spoken is counted (talk_usage), capped per day, and shows on /ai as one job.
"""

import datetime
import hashlib
import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

log = logging.getLogger('beacon.talk')

OPENAI = 'https://api.openai.com/v1'
HEAR_MODEL = os.environ.get('TALK_HEAR_MODEL', 'gpt-4o-mini-transcribe')
SAY_MODEL = os.environ.get('TALK_SAY_MODEL', 'gpt-4o-mini-tts')
VOICE = os.environ.get('TALK_VOICE', 'marin')
STYLE = os.environ.get('TALK_VOICE_STYLE', 'Warm, calm and confident. A sharp chief of staff giving a short spoken '
                                            'update. Natural pace, no drama.')
TTS_URL = os.environ.get('TALK_TTS_URL', '')  # a self-hosted voice: POST {"text"} -> audio, 503 when below its bar
TTS_WARM_URL = os.environ.get('TALK_TTS_WARM_URL', '')  # POST: loads the model before the first line is needed
TTS_TIMEOUT = float(os.environ.get('TALK_TTS_TIMEOUT', '30'))  # a cold start takes ~25-40 s
# How long the own voice stays up after its last line (the host's idle window, 300 s, less a margin). Past it, and
# with a warm URL to wake it, a line never waits on a cold start: the quick voice says it while the own one wakes.
READY_WINDOW = float(os.environ.get('TALK_TTS_READY_SECONDS', '240'))
DAY_MINUTES = float(os.environ.get('TALK_DAY_MINUTES', '30'))  # heard + spoken, per day
# USD per minute (vendor price pages, 2026): transcribe $0.003, mini TTS ~$0.015. The own voice runs on a rented
# GPU (L40S $1.95/h, 2026-11-24) and renders a sentence in ~0.85x its length; a warm keeps it up ~5 min more.
GPU_PER_MIN = float(os.environ.get('TALK_TTS_GPU_PER_HOUR', '1.95')) / 60
PRICE = {'hear': 0.003, 'say': 0.015, 'wake': 0.003, 'say-own': round(GPU_PER_MIN * 0.85, 4)}
WARM_COST = GPU_PER_MIN * 5.5
MISS_ALARM = 3  # own-voice misses in a row before Telegram hears (once a day)
MAX_AUDIO = 4 * 1024 * 1024
WORDS_PER_SECOND = 2.6

SCHEMA = '''
CREATE TABLE IF NOT EXISTS talk_usage (
  at REAL NOT NULL,
  kind TEXT NOT NULL,          -- hear, wake, say, act, brief, session
  seconds REAL NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0,
  device TEXT
);
CREATE INDEX IF NOT EXISTS talk_usage_at ON talk_usage(at);
'''


class CapReached(Exception):
    pass


class VoiceError(Exception):
    pass


def vault(name):
    k = os.environ.get(name)
    if k:
        return k
    try:  # the box's vault (linux-setup `secrets`), read once
        k = subprocess.run(['secrets', 'get', name], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        k = ''
    if k:
        os.environ[name] = k
    return k


def openai_key():
    return vault('OPENAI_API_KEY')


def own_voice_headers():
    h = {'Content-Type': 'application/json'}
    if vault('TALK_TTS_TOKEN'):
        h['Authorization'] = f'Bearer {vault("TALK_TTS_TOKEN")}'
    return h


def http(req, timeout=30):
    for attempt in (1, 2):  # one retry: a name lookup that fails for a second should not cost him a turn
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), r.headers.get('Content-Type') or ''
        except urllib.error.HTTPError as e:
            raise VoiceError(f'{e.code}: {e.read()[:200].decode("utf-8", "replace")}') from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == 2:
                raise VoiceError(str(e)) from None
            time.sleep(0.5)


def say_count(n, one, many):
    words = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']
    return f'{words[n] if n < len(words) else n} {one if n == 1 else many}'


class Talk:
    def __init__(self, store, labs=None, questions=None, tasks=None, opener=None):
        self.store, self.labs, self.questions, self.tasks = store, labs, questions, tasks
        self.http = opener or http
        self.cache = {}  # text hash -> (audio, type): the filler line and repeats cost nothing
        self.lock = threading.Lock()
        self.warmed = 0.0  # last wake asked (at most one a minute)
        self.ready = 0.0  # last time the own voice answered or finished waking
        self.misses = 0  # own-voice misses in a row
        self.alarmed = ''  # the day the misses last reached Telegram
        self.send = None  # f(text) -> bool; the live hub passes telegram-alarm
        with store.connect() as con:
            con.executescript(SCHEMA)

    # ------------------------------------------------------------ usage and the cap

    def day_start(self):
        d = datetime.date.fromisoformat(self.store.today())
        return datetime.datetime.combine(d, datetime.time(), self.store.tz).timestamp()

    def log(self, kind, seconds=0.0, device=None, cost=None):
        cost = PRICE.get(kind, 0) * seconds / 60 if cost is None else cost
        with self.store.connect() as con:
            con.execute('INSERT INTO talk_usage (at, kind, seconds, cost, device) VALUES (?, ?, ?, ?, ?)',
                        (time.time(), kind, round(seconds, 1), cost, (device or '')[:20] or None))

    def minutes_today(self):
        with self.store.connect() as con:
            s = con.execute("SELECT COALESCE(SUM(seconds), 0) FROM talk_usage WHERE at >= ? AND kind IN ('hear', 'wake', 'say', 'say-own')",
                            (self.day_start(),)).fetchone()[0]
        return s / 60

    def check_cap(self):
        if self.minutes_today() >= DAY_MINUTES:
            raise CapReached(f'Talk has used its {DAY_MINUTES:g} minutes for today. Back tomorrow, or type.')

    def stats(self, days=7):
        """Per day: minutes heard and spoken, cost, talks, and what he did by voice (the Done-test counts these)."""
        start = self.day_start() - (days - 1) * 86400
        with self.store.connect() as con:
            rows = con.execute('SELECT at, kind, seconds, cost FROM talk_usage WHERE at >= ?', (start,)).fetchall()
        out = {}
        for r in rows:
            d = self.store.local_date(r['at'])
            x = out.setdefault(d, {'minutes': 0.0, 'cost': 0.0, 'talks': 0, 'acts': 0, 'briefs': 0})
            if r['kind'] in PRICE:
                x['minutes'] += r['seconds'] / 60
            x['cost'] += r['cost']
            x['talks'] += r['kind'] == 'session'
            x['acts'] += r['kind'] == 'act'
            x['briefs'] += r['kind'] == 'brief'
        return {'days': {d: {k: round(v, 3) if isinstance(v, float) else v for k, v in x.items()} for d, x in sorted(out.items())},
                'today_minutes': round(self.minutes_today(), 2), 'cap_minutes': DAY_MINUTES}

    def aijob(self, days):
        """One line for /ai (hub/aijobs.py `extra`): the voice's own cost per day, with the cap in its words."""
        start = datetime.datetime.combine(datetime.date.fromisoformat(days[0]), datetime.time(), self.store.tz).timestamp()
        with self.store.connect() as con:
            rows = con.execute('SELECT at, kind, seconds, cost FROM talk_usage WHERE at >= ?', (start,)).fetchall()
        if not rows:
            return None
        per = {d: 0.0 for d in days}
        talks = calls = 0
        mins = 0.0
        last = 0
        for r in rows:
            d = self.store.local_date(r['at'])
            if d in per:
                per[d] += r['cost']
            talks += r['kind'] == 'session'
            calls += r['kind'] in PRICE
            mins += r['seconds'] / 60 if r['kind'] in PRICE else 0
            last = max(last, r['at'])
        cost = sum(per.values())
        own = [r for r in rows if r['kind'] in ('say-own', 'warm')]
        missed = sum(r['kind'] == 'say-miss' for r in rows)
        models = [{'name': 'own voice (GPU)', 'calls': sum(r['kind'] == 'say-own' for r in rows), 'cost': round(sum(r['cost'] for r in own), 4)},
                  {'name': SAY_MODEL, 'calls': sum(r['kind'] == 'say' for r in rows), 'cost': round(sum(r['cost'] for r in rows if r['kind'] == 'say'), 4)},
                  {'name': HEAR_MODEL, 'calls': sum(r['kind'] in ('hear', 'wake') for r in rows),
                   'cost': round(sum(r['cost'] for r in rows if r['kind'] in ('hear', 'wake')), 4)}]
        return {'id': 'hub:talk', 'machine': 'hub', 'name': 'Talk to Beacon (voice)',
                'what': f'Hears and speaks in Labs talk mode: {mins:.0f} min this week, capped at {DAY_MINUTES:g} min a day'
                        + (f'; own voice missed {missed} lines (spoken by OpenAI instead)' if missed else ''),
                'kind': 'on demand', 'schedule': None, 'next_run': None, 'state': None, 'job_last_run': last,
                'linked': True, 'tools': ['OpenAI'] + (['own voice'] if own else []),
                'providers': ['OpenAI'] + (['GPU'] if own else []),
                'asks': {'models': [m['name'] for m in models if m['calls']], 'efforts': []},
                'runs': talks, 'runs_logged': talks, 'calls': calls, 'tokens': 0, 'cost': round(cost, 4),
                'per_run': round(cost / talks, 4) if talks else None, 'priced': True, 'floor': False, 'share': 0,
                'models': [m for m in models if m['calls']], 'efforts': [],
                'days': [round(per[d], 4) for d in days], 'last': last, 'measured': True, 'unmeasurable': False,
                'sample': None}

    # ------------------------------------------------------------ hearing

    def hear(self, audio, mime='audio/webm', seconds=0.0, kind='hear', device=None):
        if not audio or len(audio) > MAX_AUDIO:
            raise VoiceError('no audio, or too long')
        self.check_cap()
        key = openai_key()
        if not key:
            raise VoiceError('no OpenAI key on the hub')
        ext = {'audio/mp4': 'm4a', 'audio/aac': 'm4a', 'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/ogg': 'ogg',
               'audio/mpeg': 'mp3'}.get(mime.split(';')[0].strip(), 'webm')
        b = uuid.uuid4().hex
        parts = []
        for name, val in (('model', HEAR_MODEL), ('language', 'en'), ('response_format', 'json'),
                          ('prompt', 'Beacon, brief me. Hey Beacon. Yes, do it. Decisions, agents, tasks.')):
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode())
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="turn.{ext}"\r\n'
                     f'Content-Type: {mime.split(";")[0]}\r\n\r\n'.encode() + audio + b'\r\n')
        parts.append(f'--{b}--\r\n'.encode())
        req = urllib.request.Request(f'{OPENAI}/audio/transcriptions', data=b''.join(parts), method='POST',
                                     headers={'Authorization': f'Bearer {key}',
                                              'Content-Type': f'multipart/form-data; boundary={b}'})
        body, _ = self.http(req, 30)
        self.log(kind, max(1.0, float(seconds or 0)), device)
        try:
            return (json.loads(body).get('text') or '').strip()
        except ValueError:
            raise VoiceError('the transcript did not parse') from None

    # ------------------------------------------------------------ speaking

    def say(self, text, device=None):
        text = ' '.join(str(text or '').split())[:1200]
        if not text:
            raise VoiceError('nothing to say')
        h = hashlib.sha1(f'{VOICE}|{STYLE}|{TTS_URL}|{text}'.encode()).hexdigest()
        hit = self.cache.get(h)
        if hit:
            return hit
        self.check_cap()
        audio = self.own_voice(text) if TTS_URL and self.own_voice_up() else None
        kind = 'say-own' if audio else 'say'
        if audio is None:
            key = openai_key()
            if not key:
                raise VoiceError('no OpenAI key on the hub')
            req = urllib.request.Request(f'{OPENAI}/audio/speech', method='POST', data=json.dumps(
                {'model': SAY_MODEL, 'voice': VOICE, 'input': text, 'instructions': STYLE, 'response_format': 'mp3'}).encode(),
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
            audio = self.http(req, 40)
        data, ctype = audio
        self.log(kind, len(text.split()) / WORDS_PER_SECOND, device)
        if TTS_URL and kind == 'say':  # a fallback line is not kept: next time the own voice may answer
            return data, ctype or 'audio/mpeg'
        with self.lock:
            if len(self.cache) > 60:
                self.cache.clear()
            self.cache[h] = (data, ctype or 'audio/mpeg')
        return self.cache[h]

    def own_voice(self, text):
        """The self-hosted voice's clip, or None (asleep, too slow, or no render passed its bar): counted, alarmed."""
        try:
            audio = self.http(urllib.request.Request(TTS_URL, data=json.dumps({'text': text}).encode(), method='POST',
                                                     headers=own_voice_headers()), TTS_TIMEOUT)
        except VoiceError as e:
            self.log('say-miss', 0)
            self.misses += 1
            log.warning('own voice missed (%s in a row): %s', self.misses, e)
            today = self.store.today()
            if self.misses >= MISS_ALARM and self.alarmed != today and self.send:
                self.alarmed = today
                threading.Thread(target=self.send, daemon=True, args=(f'🔇 Beacon talk lost its own voice: {self.misses} lines in a row fell back to the '
                          f'OpenAI voice. Last error: {str(e)[:120]}',)).start()
            return None
        self.misses = 0
        self.warmed = self.ready = time.time()
        return audio

    def own_voice_up(self):
        """Ask the own voice only when it is awake (or cannot be woken apart): a cold start costs ~30 s of silence."""
        if not TTS_WARM_URL or time.time() - self.ready < READY_WINDOW:
            return True
        self.warm()
        return False

    def warm(self):
        """Wake the own voice before its first line (the talk page opened). At most once a minute; never blocks."""
        if not TTS_WARM_URL or time.time() - self.warmed < 60:
            return False
        self.warmed = time.time()

        def go():
            try:
                self.http(urllib.request.Request(TTS_WARM_URL, data=b'{}', method='POST', headers=own_voice_headers()),
                          90)
                self.ready = time.time()
                self.log('warm', 0, cost=WARM_COST)
            except VoiceError as e:
                log.warning('warming the own voice failed: %s', e)
        threading.Thread(target=go, daemon=True).start()
        return True

    # ------------------------------------------------------------ the brief

    def last_brief(self):
        with self.store.connect() as con:
            r = con.execute("SELECT MAX(at) FROM talk_usage WHERE kind = 'brief'").fetchone()[0]
        return r

    def brief(self, device=None):
        """What happened since the last brief, what waits for him, who is working; ends on 'What first?'."""
        now = time.time()
        since = self.last_brief() or now - 86400
        since = max(since, now - 3 * 86400)
        parts, cards = [], []

        done = []
        if self.tasks:
            done = [t for t in self.tasks.list('done', limit=200) if (t.get('closed_at') or t.get('updated_at') or 0) >= since]
        if done:
            names = ', '.join(t['title'] for t in done[:2])
            parts.append(f'Since last time, your agents finished {say_count(len(done), "task", "tasks")}'
                         + (f', including {names}.' if names else '.'))
            cards.append({'type': 'list', 'title': 'Done since last brief',
                          'items': [{'title': t['title'][:100], 'sub': self.labs.pname(t.get('project')) if self.labs else '',
                                     'tone': 'good'} for t in done[:5]]})
        else:
            parts.append('Nothing new finished since last time.')

        decisions = []
        if self.questions:
            qs = self.questions.awake(limit=100)
            qs.sort(key=lambda q: (not q.get('blocking'), -(q.get('created_at') or 0)))
            decisions = qs[:3]
        if decisions:
            order = ['First', 'Second', 'Third']
            n_all = len(self.questions.awake())
            parts.append(f'{say_count(n_all, "decision waits", "decisions wait").capitalize()} for you.')
            for i, q in enumerate(decisions):
                parts.append(f'{order[i]}: {q["question"].rstrip("?")}?')
            cards.append({'type': 'list', 'title': 'Waiting for you',
                          'items': [{'title': q['question'][:110], 'sub': ' · '.join(
                              o.get('label') if isinstance(o, dict) else str(o) for o in (q.get('options') or [])[:3])[:110],
                              'tone': 'warn' if q.get('blocking') else ''} for q in decisions]})
        else:
            parts.append('No decisions wait for you.')

        if self.labs:
            live = self.labs.source('sessions_live') or {}
            working = live.get('working') or 0
            if working:
                parts.append(f'{say_count(working, "agent is", "agents are").capitalize()} working right now.')
                cards.append({'type': 'list', 'title': 'Working now', 'source': 'sessions_live', 'limit': 5})

        parts.append('What first?')
        text = ' '.join(parts)
        self.log('brief', 0, device)
        cards = [{'type': 'say', 'text': text}] + cards
        return {'say': text, 'cards': self.labs.resolve(cards) if self.labs else cards,
                'decisions': [{'id': q['id'], 'question': q['question'],
                               'options': [o.get('label') if isinstance(o, dict) else str(o) for o in (q.get('options') or [])]}
                              for q in decisions]}

    # ------------------------------------------------------------ acting

    def act(self, body, device=None):
        """Answer a decision he confirmed by voice. Read back and confirmed on the page before this is called."""
        if not self.questions:
            raise VoiceError('decisions are off')
        if body.get('do') != 'answer':
            raise VoiceError('only answering a decision is possible by voice')
        qid = int(body.get('decision') or 0)
        q = self.questions.get(qid) if qid else None
        if q and q.get('blocking'):  # a send, pay or risky call waits for his tap on Decisions, never a heard "yes"
            raise VoiceError('that one needs a tap on Decisions')
        answer = ' '.join(str(body.get('answer') or '').split())[:500]
        if not qid or not answer:
            raise VoiceError('which decision, and what answer?')
        q = self.questions.answer(qid, {'answer': answer, 'note': 'Said by voice in Labs talk mode, read back and confirmed.'},
                                  via='page')
        if not q:
            raise VoiceError('that decision is gone')
        self.log('act', 0, device)
        return {'ok': True, 'id': qid, 'question': q['question'], 'answer': answer}
