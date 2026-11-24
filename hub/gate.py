"""The approval gate's rules (task 1764), shared with the `tasks` client: client/tasks holds the one
copy (it must stay a single file: the Windows PC fetches just that file), loaded here as a module so a
question on the hub and an agent's tool call are judged by the same words."""
import importlib.machinery
import importlib.util
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'client', 'tasks')
_loader = importlib.machinery.SourceFileLoader('tasks_client', _PATH)
_spec = importlib.util.spec_from_loader('tasks_client', _loader)
rules = importlib.util.module_from_spec(_spec)
_loader.exec_module(rules)

question_blocks = rules.question_blocks
risk_words = rules.risk_words
risky_call = rules.risky_call
CATEGORIES = rules.CATEGORIES
NOT_YES = rules.NOT_YES
fold = rules.fold
