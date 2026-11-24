#!/usr/bin/env python3
"""Fill a fresh hub with made-up tasks and decisions, so its pages show something on a first run.

  TASKS_TOKEN=demo python3 hub/server.py &          # an empty hub on http://127.0.0.1:4829
  TASKS_TOKEN=demo python3 scripts/demo.py           # then open http://127.0.0.1:4829/tasks and /decisions
  ... scripts/demo.py --many --long                  # also 30 more of each, and the longest text agents write

Everything goes in through the public API, the way agents write; nothing here is real.
"""
import json
import os
import sys
import urllib.request

HUB = os.environ.get('HUB', 'http://127.0.0.1:4829').rstrip('/')
TOKEN = os.environ.get('TASKS_TOKEN', '')


def call(path, body):
    req = urllib.request.Request(HUB + path, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TOKEN})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def body(goal, done, steps):
    return (f'## Goal\n{goal}\n\n## Done-test\n' + '\n'.join(f'- {d}' for d in done)
            + '\n\n## Steps\n' + '\n'.join(f'{i}. {s}' for i, s in enumerate(steps, 1)))


TASKS = [
    ('100 players try the new level this week', 'snow-game', 'doing', 1,
     body('More players stay past the first level.', ['Roblox analytics shows 100 first-time players on level 2.'],
          ['🤖 Ship the level behind a flag.', '🤖 Post three short clips.', '👤 Alex: pick the clip that goes first.'])),
    ('First TestFlight build reaches 5 testers', 'habit-app', 'todo', 2,
     body('Real people use the app before the store launch.', ['5 testers installed the build.'],
          ['🤖 Build and upload.', '👤 Alex: send the invite to 5 friends.'])),
    ('The second couple\'s site is live', 'weddings', 'todo', 2,
     body('A second paying couple.', ['Their site opens on their own domain.'], ['🤖 Copy the template.', '🤖 Deploy.'])),
    ('The booking page takes a first lesson', 'coaching', 'todo', 3,
     body('The first paid lesson booked without a message.', ['One booking arrives through the page.'], ['🤖 Add the calendar.'])),
    ('The Machines page loads in under a second', 'beacon', 'done', 3,
     body('The hub stays fast.', ['The page paints in under 1 s on a phone.'], ['🤖 Cache the slow read.'])),
]

QUESTIONS = [
    ('Which clip goes out first?', ['The snowball fight', 'The speed run', 'The new map'], 'snow-game', 0),
    ('Price it at $2.99 a month or $19.99 a year?', ['$19.99 a year', '$2.99 a month'], 'habit-app', 1),
    ('Send the offer to the second couple today?', ['Yes, send it', 'Not yet'], 'weddings', 2),
]


# What real agents write at the edges: an unbroken word wider than a phone, a title that runs on, a path.
LONG = [  # (title, up to the hub's 70 characters; question, no limit)
    ('SubscriptionCheckoutConfirmationFlowControllerV2 ships to 5 users',
     'Rename the checkout flow to SubscriptionCheckoutConfirmationFlowControllerV2 everywhere'),
    ('Landing page for the second couple loads under one second on phones',
     'The landing page for the second couple loads in under a second on a slow phone, with the gallery, the '
     'map, the RSVP form and the countdown all visible above the fold and nothing jumping while fonts load'),
    ('NotificationPreferencesSettingsPanelComponent works in all 3 apps',
     'Fix /Users/someone/Projects/very-long-project-name/src/components/settings/NotificationPreferences.tsx'),
]
PROJECTS = ['snow-game', 'habit-app', 'weddings', 'coaching', 'beacon', 'a-very-long-project-name-here']


def extra(many, long):
    """More tasks and decisions for the review's 'many' and 'long text' states."""
    tasks, questions = [], []
    if long:
        for i, (title, text) in enumerate(LONG):
            tasks.append((title, PROJECTS[i], 'todo', 1, body(text, [text], [f'🤖 {text}'])))
            questions.append((text + '?', [title, 'No'], PROJECTS[i], 0))
    if many:
        for i in range(30):
            project = PROJECTS[i % len(PROJECTS)]
            tasks.append((f'Made-up task {i + 1} for {project}', project, ('todo', 'doing', 'done')[i % 3], 1 + i % 3,
                          body('Fill the page.', ['Shows.'], ['🤖 Nothing.'])))
            questions.append((f'Made-up decision {i + 1}: ship it?', ['Yes', 'No', 'Later'], project, 0))
    return tasks, questions


def main():
    many, long = '--many' in sys.argv, '--long' in sys.argv
    if not TOKEN:
        sys.exit('Set TASKS_TOKEN to the token the hub was started with.')
    more_tasks, more_questions = extra(many, long)
    ids = []
    for title, project, status, priority, text in TASKS + more_tasks:
        t = call('/api/tasks', {'title': title, 'project': project, 'priority': priority, 'body': text})
        if status != 'todo':
            call(f"/api/tasks/{t['id']}", {'status': status})
        ids.append(t['id'])
    call(f'/api/tasks/{ids[0]}/comments', {'text': '👉 Level 2 is behind the flag; clips are drafted.', 'author': 'agent'})
    for question, options, project, task in QUESTIONS + more_questions:
        call('/api/questions', {'question': question, 'options': options, 'project': project, 'task_id': ids[task],
                                'kind': 'decision'})
    print(f'{len(TASKS + more_tasks)} tasks and {len(QUESTIONS + more_questions)} decisions on {HUB}')


if __name__ == '__main__':
    main()
