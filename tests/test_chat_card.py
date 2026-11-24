"""A question asked with buttons in the chat becomes a card with no filler line of its own (task 1992)."""
import importlib.machinery
import importlib.util
import os
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ['BEACON_CONFIG'] = os.path.join(ROOT, 'config.example.json')


def load_cli():
    loader = importlib.machinery.SourceFileLoader('tasks_cli_chat', os.path.join(ROOT, 'client', 'tasks'))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader('tasks_cli_chat', loader))
    loader.exec_module(module)
    return module


class ChatCard(unittest.TestCase):
    def test_card_carries_only_the_question_and_buttons(self):
        cli = load_cli()
        sent = []
        ev = {'session_id': 'abc', 'cwd': ROOT, 'tool_use_id': 't1', 'tool_input': {'questions': [
            {'question': 'Ship it?', 'header': 'ship', 'options': [{'label': 'Yes', 'description': 'now'}]}]}}
        with mock.patch.object(cli, 'hooks_off', return_value=False), \
                mock.patch.object(cli, 'open_question_for_box', return_value=None), \
                mock.patch.object(cli, 'quick', side_effect=lambda path, body=None, **k: sent.append((path, body))):
            cli.hook_ask_shown(ev)
        self.assertEqual(len(sent), 1)
        body = sent[0][1]
        self.assertEqual(body['question'], 'Ship it?')
        self.assertFalse(body.get('context'), 'no "asked in the chat" filler on the card')


if __name__ == '__main__':
    unittest.main()
