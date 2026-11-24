"""Feedback from any page (task 1913): he draws on a screenshot of the page, says what to change, and an agent starts.

The page's feedback button (web/feedback.js) posts the marked-up screenshot, his note and what the page knows:
its address, size and theme, the elements under his strokes, his last taps and the page's recent errors.
This files a task in the page's project with all of it, and a `feedback` card already answered with his note:
the answer dispatcher (client/answer-dispatcher) picks that card up like any answer and starts a run on the
task, so feedback needs no queue of its own. The card is born answered: nothing pings his phone, and it never
counts as an approval or teaches a card rule.
"""
import base64
import binascii
import urllib.parse

from questions import PICTURE_MAX, PICTURE_TYPES, text
from settings import OWNER_ID, OWNER_NAME

KIND = 'feedback'
TAG = 'feedback'
NOTE_MAX = 4000
LIST_MAX = 30  # marks, taps and errors: the latest this many
DEFAULT_PROJECT = 'beacon'
# Passed-through apps (hub/apps.py) live in their own repos: feedback on /budget/... is budget's work.
APP_PROJECTS = {'/budget': 'budget', '/lighthouse': 'lighthouse'}


def project_for(path):
    for prefix, project in APP_PROJECTS.items():
        if path == prefix or path.startswith(prefix + '/'):
            return project
    return DEFAULT_PROJECT


def short(v, limit):
    return ' '.join(str(v or '').split())[:limit]


def lines(items, fmt):
    if not isinstance(items, list):
        return []
    return [fmt(x) for x in items[-LIST_MAX:] if isinstance(x, dict)]


def picture(v):
    """(bytes, ext) of a data: URL or bare base64 picture, else (None, None)."""
    if not v:
        return None, None
    if not isinstance(v, str):
        raise ValueError('picture: expected a data URL')
    ext = 'jpg'
    if v.startswith('data:'):
        head, _, v = v.partition(',')
        ext = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}.get(head[5:].split(';')[0], '')
        if not ext:
            raise ValueError('picture: png, jpg or webp')
    try:
        data = base64.b64decode(v, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('picture: not base64')
    if not data or len(data) > PICTURE_MAX:
        raise ValueError('picture: at most 8 MB')
    assert ext in PICTURE_TYPES
    return data, ext


class Feedback:
    def __init__(self, tasks, questions):
        self.tasks = tasks
        self.questions = questions

    def file(self, body, author):
        """{task, question} for one piece of feedback; ValueError when there is nothing to act on."""
        note = text(body.get('note'), 'note', NOTE_MAX) or ''
        data, ext = picture(body.get('picture'))
        if not note.strip() and data is None:
            raise ValueError('note: say what to change, or mark it on the picture')
        page = body.get('page') if isinstance(body.get('page'), dict) else {}
        url = short(page.get('url'), 500)
        path = urllib.parse.urlsplit(url).path or '/'
        title = short(page.get('title'), 80) or path
        project = project_for(path)
        pic = self.questions.save_picture(data, ext) if data is not None else None

        marks = lines(body.get('marks'), lambda m: f"- {short(m.get('what'), 200)}" + (
            f" „{short(m.get('text'), 160)}“" if m.get('text') else ''))
        taps = lines(body.get('taps'), lambda t: f"- {short(t.get('at'), 8)} {short(t.get('page'), 80)}: {short(t.get('what'), 160)}")
        errors = lines(body.get('errors'), lambda e: f"- {short(e.get('at'), 8)} {short(e.get('what'), 300)}")
        said = short(note, 70) or 'the marked part'
        context = [f"- Page: {title} ({url})" if url else f'- Page: {title}',
                   f"- Screen: {short(page.get('viewport'), 40)}, theme {short(page.get('theme'), 20) or 'light'}, "
                   f"{short(page.get('device'), 120)}"]
        if pic:
            context.append(f'- Marked-up screenshot: {pic} (fetch it with the tasks token, then look at it)')
        body_text = '\n'.join([
            '## Goal', f'{OWNER_NAME} flagged this on the {title} page: „{note.strip() or "see the marks on the screenshot"}“.',
            'Change it so the page does what he means; the marks on the screenshot show where.', '',
            '## Done-test',
            f'- The change is live on {url or title}.',
            '- A screenshot of the same spot at the same size shows it changed, and you looked at it.',
            '- Tests and lint pass.', '',
            '## Steps',
            '1. 🤖 Look at the marked-up screenshot and the elements under his marks; find the code behind them.',
            '2. 🤖 Make the change in a worktree, test it, screenshot it at 390 px and desktop.',
            '3. 🤖 Merge and deploy it the way this project ships, then check it live.', '',
            '## Context', *context,
            *(['', 'Under his marks:', *marks] if marks else []),
            *(['', 'His last taps, oldest first:', *taps] if taps else []),
            *(['', 'Recent errors on the page:', *errors] if errors else []),
        ])
        task = self.tasks.create({'title': f'{title}: {said}', 'body': body_text, 'project': project,
                                  'tags': [TAG], 'priority': 2}, author)
        fields = {'question': f'Feedback on {title}', 'kind': KIND, 'task_id': task['id'], 'project': project,
                  'project_asked': True, 'answer': note.strip() or 'Fix what I marked on the screenshot.',
                  'via': 'page' if author == OWNER_ID else 'cli',  # his hand only when it is his login
                  'asked_by': author, 'context': '\n'.join(context)}
        if data is not None:
            fields['see'] = [{'name': f'feedback.{ext}', 'data': base64.b64encode(data).decode()}]
        try:
            q = self.questions.create(fields, author)
        except Exception:
            self.tasks.update(task['id'], {'status': 'dropped'})  # no task without the card that starts its run
            raise
        return {'task': task, 'question': q}
