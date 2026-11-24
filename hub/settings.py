"""Deployment settings: who owns this hub, which machines it watches, where its neighbour apps and data live.

Read once from a JSON file, the first that exists of:
  $BEACON_CONFIG                     an explicit path (tests point it at config.example.json)
  ~/.config/beacon/config.json       the machine's own copy, shared by every checkout and worktree on it
Each top-level section of that file replaces the same section of config.example.json (committed, generic),
so a deployment lists only what differs and a fresh clone runs on the example alone.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, 'config.example.json')
LOCAL = os.path.expanduser('~/.config/beacon/config.json')


def path():
    """The settings file in use, or None when only the example applies."""
    explicit = os.environ.get('BEACON_CONFIG')
    if explicit:
        return explicit
    return LOCAL if os.path.isfile(LOCAL) else None


def load(file=None):
    with open(EXAMPLE, encoding='utf-8') as f:
        out = json.load(f)
    file = file or path()
    if file and os.path.abspath(file) != os.path.abspath(EXAMPLE):
        with open(file, encoding='utf-8') as f:
            out.update(json.load(f))
    out.pop('_about', None)
    return out


CONFIG = load()
OWNER = CONFIG['owner']
OWNER_ID = OWNER['id']          # the author name his own task notes and answers carry
OWNER_NAME = OWNER['name']      # how pages and prompts call him
HUB = CONFIG['hub']
MACHINES = CONFIG['machines']
APPS = CONFIG['apps']
PROJECT_NAMES = CONFIG['project_names']


def p(key):
    """A path from the `paths` section, with ~ expanded."""
    return os.path.expanduser(CONFIG['paths'][key])


def browser():
    """The part of the settings pages need, served as /config.js before any page script."""
    return {
        'owner': {'id': OWNER_ID, 'name': OWNER_NAME, 'names': OWNER.get('names') or [OWNER_NAME]},
        'hub': {'machine': HUB['machine'], 'https': HUB.get('https', ''), 'agent_home': HUB['agent_home']},
        'machines': list(MACHINES),
        'machine_icons': {k: v['icon'] for k, v in MACHINES.items() if v.get('icon')},
        'project_names': PROJECT_NAMES,
        'site_names': CONFIG.get('site_names', {}),
    }
