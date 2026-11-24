"""Which background jobs call a language model, and which job a running model process belongs to.

Two halves, both for the hub's AI jobs page (http://beacon/ai):

- sniff(words): reads the files a job's command runs (and the scripts those name) for the marks of a
  model call: a headless agent CLI (`claude -p`, `opencode run`, `codex exec`, `hermes`), or a provider's
  API host (OpenRouter, DeepSeek, Anthropic's Messages API, MiniMax, ...). Returns what it found:
  {'tools': [...], 'providers': [...], 'models': [...], 'efforts': [...]}, or None for a job that calls none.
  A hint, not the truth: the hub measures the real model, effort and cost from the runs themselves.
- runs(procs): every running agent CLI process that a job started, as {'job', 'agent', 'session', 'pid',
  'started'}. Linux reads the systemd unit from /proc/<pid>/cgroup, macOS the launchd label launchd puts in
  the process's environment (XPC_SERVICE_NAME). Sent every collector run (10 s), so the hub can tie the
  session's logged calls (model, effort, tokens) to the job that ran it.
"""
import json
import os
import re
import subprocess
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hub'))
import settings  # noqa: E402  (hub/settings.py: the deployment's names)

HOME = os.path.expanduser('~')
MAX_READ = 512 * 1024
MAX_FILES = 12

# An agent CLI run headless from a script, written in shell or as a Python/JS argument list.
TOOL_MARKS = (
    ('Claude Code', re.compile(r"""(?<!`)\bclaude\b[^\n|;&`]{0,120}?\s(?:-p|--print)\b"""
                               r"""|["']claude["']\s*,\s*(?:\*\*?\w+\s*,\s*)?["'](?:-p|--print)["']"""
                               r"""|\[\s*(?i:\w*claude\w*)\s*,\s*["'](?:-p|--print)["']"""
                               r"""|["'](?:-p|--print)["']\s*,[^\n]{0,80}\bclaude\b"""
                               r"""|(?:from|import)\s+claude_agent_sdk|(?:from\s+|require\()["']@anthropic-ai/claude-agent-sdk""")),
    ('OpenCode', re.compile(r"""\bopencode\s+run\b|["']opencode["']\s*,\s*["']run["']""")),
    ('Codex', re.compile(r"""\bcodex\s+exec\b|["']codex["']\s*,\s*["']exec["']""")),
    ('Hermes', re.compile(r"""\bhermes\s+(?:chat|gateway|run)\b|hermes_cli|["']hermes["']\s*,\s*["'](?:chat|run)["']""")),
)
# A provider's model API called directly (no agent CLI in between).
PROVIDER_MARKS = (
    ('OpenRouter', re.compile(r'openrouter\.ai/api')),
    ('DeepSeek', re.compile(r'api\.deepseek\.com')),
    ('Anthropic API', re.compile(r'api\.anthropic\.com/v1/messages|anthropic\.Anthropic\(|from anthropic import')),
    ('OpenAI', re.compile(r'api\.openai\.com/v1/(?:chat|responses|completions)|from openai import|openai\.OpenAI\(')),
    ('MiniMax', re.compile(r'api\.minimaxi?\.(?:io|com|chat)')),
    ('Gemini', re.compile(r'generativelanguage\.googleapis\.com|google\.generativeai|from google import genai')),
    ('Grok', re.compile(r'api\.x\.ai/v1')),
    ('Groq', re.compile(r'api\.groq\.com')),
    ('Mistral', re.compile(r'api\.mistral\.ai')),
    ('Ollama (local)', re.compile(r'(?:localhost|127\.0\.0\.1):11434|\bollama\s+run\b')),
)
MODEL_RE = re.compile(
    r"""--model[ =]["']?([A-Za-z0-9][\w.:/-]{1,60})"""
    r"""|\bmodel["']?\s*[:=]\s*["']([A-Za-z0-9][\w.:/-]{1,60})["']"""
    r"""|["']--model["']\s*,\s*["']([\w.:/-]{2,60})["']"""
    r"""|(?i:\bmodel\w*)\s*[:=][^\n]{0,80}?["'](opus|sonnet|haiku|fable)["']""")
MODEL_ID_RE = re.compile(r"""\b(claude-(?:opus|sonnet|haiku|fable)-[\d][\w.-]*|deepseek-(?:chat|reasoner|v[\d.]+\w*)"""
                         r"""|MiniMax-M[\d.]+\w*|gpt-[\d][\w.-]*|gemini-[\d][\w.-]*|grok-[\d][\w.-]*)\b""")
EFFORT_RE = re.compile(r"""--effort[ =]["']?(low|medium|high|xhigh|max)\b"""
                       r"""|["']--effort["']\s*,\s*["'](low|medium|high|xhigh|max)["']"""
                       r"""|\beffort["']?\s*[:=]\s*["'](low|medium|high|xhigh|max)["']""")
# The opening words of a prompt a script sends: a sentence starting right after a quote or a heredoc.
PROMPT_START_RE = re.compile(r'''(?:"""|\'\'\'|["'`]|<<-?\s*["']?\w+["']?\s*\n)\s*([A-Z][^\n"'`]{15,})''')
ALIASES = {'opus', 'sonnet', 'haiku', 'fable', 'opusplan', 'default', 'best'}
PATH_RE = re.compile(r"""(?:(?<=[\s"'=(:])|^)((?:~|\$HOME|\$\{HOME\}|/)[\w./@+-]+|[A-Za-z]:[\\/][\w.\\/@+ -]*?\.(?:py|ps1|sh|js|mjs|ts|bat|cmd))""")
# Comment lines say what a script is about ("pauses the claude -p jobs"), not what it runs.
COMMENT_RE = re.compile(r'^\s*(?:#|//|--|;|\*|/\*)')
# Directories whose files are programs of his, worth reading; never interpreters or system libraries.
SKIP_DIRS = ('/usr/bin/', '/bin/', '/usr/sbin/', '/sbin/', '/usr/lib/', '/lib/', '/opt/homebrew/bin/',
             '/opt/homebrew/Cellar/', '/System/', '/Library/', '/proc/', '/dev/', '/etc/', '/tmp/', '/var/')


def expand(p):
    p = p.replace('${HOME}', HOME).replace('$HOME', HOME)
    return os.path.expanduser(p).rstrip('.,;:)"\'')


def readable(p):
    if any(p.startswith(d) for d in SKIP_DIRS) and not p.startswith('/usr/local/'):
        return False
    try:
        return os.path.isfile(p) and os.path.getsize(p) > 0
    except OSError:
        return False


def read_text(path, cache):
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    hit = cache.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with open(path, 'rb') as f:
            raw = f.read(MAX_READ)
    except OSError:
        return None
    text = None if b'\0' in raw[:4096] else raw.decode('utf-8', 'replace')  # a binary is no script
    cache[path] = (mt, text)
    return text


def code_of(text):
    return '\n'.join(x for x in text.splitlines() if not COMMENT_RE.match(x))


def files_of(words, cache):
    """The scripts a command runs, then the scripts those name (one level), his own files only."""
    first = []
    for w in words:
        for m in PATH_RE.finditer(' ' + str(w)):
            p = expand(m.group(1))
            if readable(p) and p not in first:
                first.append(p)
    out = list(first)
    for p in first:
        text = read_text(p, cache)
        if not text:
            continue
        for m in PATH_RE.finditer(code_of(text)):
            q = expand(m.group(1))
            if len(out) >= MAX_FILES:
                break
            if q not in out and readable(q):
                out.append(q)
    return out[:MAX_FILES]


def scan(text):
    """What one script's code says about model calls."""
    code = code_of(text)
    tools = [name for name, rx in TOOL_MARKS if rx.search(code)]
    providers = [name for name, rx in PROVIDER_MARKS if rx.search(code)]
    models = []
    for m in MODEL_RE.finditer(code):
        v = next(g for g in m.groups() if g)
        if v.lower() in ALIASES or MODEL_ID_RE.fullmatch(v) or '/' in v:
            models.append(v)
    models += MODEL_ID_RE.findall(code)
    efforts = [next(g for g in m.groups() if g) for m in EFFORT_RE.finditer(code)]
    starts = [k for k in (prompt_start(m.group(1)) for m in PROMPT_START_RE.finditer(code)) if k]
    return tools, providers, models, efforts, starts


def prompt_start(text):
    """A prompt's first three words, placeholders and numbers out: the hub matches past runs on it."""
    t = re.sub(r'\{[^}]*\}|\$\{?\w+\}?|%\(?\w*\)?[sd]|[#?]?\d[\d.:-]*', ' ', text.lower())
    words = re.findall(r"[a-z][\w']*", t)
    return ' '.join(words[:3]) if len(words) >= 4 else None


def uniq(xs):
    seen, out = set(), []
    for x in xs:
        if x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def hermes_model():
    """Hermes keeps its model in ~/.hermes/config.yaml (model: default/provider), not in a script."""
    try:
        with open(os.path.join(HOME, '.hermes', 'config.yaml'), encoding='utf-8') as f:
            head = f.read(4096)
    except OSError:
        return [], []
    m = re.search(r'^model:\s*\n((?:[ \t]+.*\n)+)', head, re.M)
    block = m.group(1) if m else ''
    model = re.search(r'^\s+default:\s*["\']?([\w.:/-]+)', block, re.M)
    prov = re.search(r'^\s+provider:\s*["\']?([\w.-]+)', block, re.M)
    names = {'anthropic': 'Anthropic API', 'openrouter': 'OpenRouter', 'deepseek': 'DeepSeek', 'minimax': 'MiniMax',
             'openai': 'OpenAI'}
    return ([model.group(1)] if model else []), ([names.get(prov.group(1).lower(), prov.group(1))] if prov else [])


# The job's own command starts an agent session with a prompt (`claude.exe --remote-control=… "Read BRIEF.md …"`):
# no -p, but nobody typed that first prompt. The prompt is a quoted argument of 4+ words after the claude program
# that is no flag's value (`--name "Alex main box"`, `--append-system-prompt "…"` are not prompts).
CLAUDE_EXE_RE = re.compile(r'''(?:^|[\\/\s"'])claude(?:\.exe)?["']?(?=\s)''')
VALUE_FLAGS = {'-n', '--name', '--remote-control', '--append-system-prompt', '--system-prompt', '--model', '--effort',
               '--permission-mode', '--add-dir', '--allowedTools', '--allowed-tools', '--disallowedTools',
               '--disallowed-tools', '--mcp-config', '--settings', '--session-id', '-r', '--resume', '--agents',
               '--agent', '--output-format', '--input-format', '--fallback-model', '--max-turns', '--worktree', '-w'}
QUOTED_RE = re.compile(r'''(\S*)\s+["']([^"'\n]+)["']''')


def launch_prompts(inline):
    m = CLAUDE_EXE_RE.search(inline)
    if not m:
        return []
    out = []
    for q in QUOTED_RE.finditer(inline, m.end()):
        before, text = q.group(1), q.group(2)
        if before in VALUE_FLAGS or len(text.split()) < 4:
            continue
        k = prompt_start(text)
        if k:
            out.append(k)
    return out


def sniff(words, cache=None):
    """{'tools', 'providers', 'models', 'efforts'} when the command's scripts call a model, else None.
    words: the command as a list of words (argv, or one shell string)."""
    cache = {} if cache is None else cache
    words = [w for w in (words or []) if isinstance(w, str)]
    tools, providers, models, efforts, starts = [], [], [], [], []
    inline = ' '.join(words)
    launches = launch_prompts(inline)
    if launches:
        tools.append('Claude Code')
        starts += launches
    for text in [inline] + [read_text(p, cache) for p in files_of(words, cache)]:
        if not text:
            continue
        t, p, m, e, st = scan(text)
        tools += t
        providers += p
        starts += st
        if t or p or tools or providers:  # a model name counts only next to a call
            models += m
            efforts += e
    if 'Hermes' in tools:
        m, prov = hermes_model()
        models += m
        providers += prov
    if not tools and not providers:
        return None
    return {'tools': uniq(tools), 'providers': uniq(providers), 'models': uniq(models)[:8], 'efforts': uniq(efforts),
            'prompts': uniq(starts)[:30], 'launches': uniq(launches)}


# ---------------------------------------------------------------- runs: which job a model process belongs to

# Matched on the program itself (its first word), so a wrapper like `timeout 2h claude -p` is not the agent.
AGENT_EXE = (
    ('Claude Code', re.compile(r'^claude(?:\.exe)?$'), re.compile(r'@anthropic-ai[/\\]claude-code')),
    ('OpenCode', re.compile(r'^opencode(?:\.exe)?$'), None),
    ('Codex', re.compile(r'^codex(?:\.exe)?$'), None),
)


def agent_of(args):
    words = (args or '').split()
    if not words:
        return None
    exe = re.split(r'[/\\]', words[0])[-1]
    for name, rx, node in AGENT_EXE:
        if rx.match(exe) or (node and exe.startswith('node') and len(words) > 1 and node.search(words[1])):
            return name
    return None


UNIT_RE = re.compile(r'/system\.slice/(?:[^/]+\.slice/)*([^/]+)\.service$', re.M)
SESSION_ARG = re.compile(r'--(?:session-id|resume)[ =]([0-9a-f]{8}-[0-9a-f-]{27})')


def unit_of(pid):
    """The systemd system service a process runs under, or None (a login session, a user unit)."""
    try:
        with open(f'/proc/{pid}/cgroup') as f:
            m = UNIT_RE.search(f.read())
    except OSError:
        return None
    return m.group(1) if m else None


def launchd_label(pid):
    """The launchd job that started a process: launchd sets XPC_SERVICE_NAME in its environment."""
    try:
        out = subprocess.run(['ps', 'eww', '-o', 'command=', '-p', str(pid)], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'\bXPC_SERVICE_NAME=(\S+)', out)
    v = m.group(1) if m else None
    if not v or v == '0' or v.startswith(('application.', 'com.apple.')):
        return None  # started from a terminal or an app, not by a launchd job of his
    return v


def job_of(pid):
    if sys.platform.startswith('linux'):
        u = unit_of(pid)
        return 'systemd:' + u if u else None
    if sys.platform == 'darwin':
        label = launchd_label(pid)
        return 'launchd:' + label if label else None
    return None  # Windows: a scheduled task's process carries no mark of it


def registry(sessions_dir):
    """{pid: session id} from Claude Code's ~/.claude/sessions/<pid>.json, one-shot runs included."""
    out = {}
    try:
        names = os.listdir(sessions_dir)
    except OSError:
        return out
    for n in names:
        if not n.endswith('.json'):
            continue
        try:
            with open(os.path.join(sessions_dir, n), encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and isinstance(d.get('pid'), int) and d.get('sessionId'):
            out[d['pid']] = d['sessionId']
    return out


# The hub itself and the collector are never "a job running a model", even when they shell out to one.
_LAUNCHD = settings.OWNER['launchd_prefix']
NOT_JOBS = ('systemd:agent-usage-collect', 'launchd:' + _LAUNCHD + 'agent-usage-collect')
# Remote Control servers host his own sessions, driven from the phone: his work, not a job of its own.
HUMAN_UNITS = re.compile(r'^systemd:claude-remote-control|^launchd:' + re.escape(_LAUNCHD) + 'claude-remote')


def runs(procs, sessions_dir=None):
    """Agent CLI processes a background job started: [{'job', 'agent', 'session', 'pid', 'started'}]."""
    if sys.platform == 'win32' or not procs:
        return []
    reg = registry(sessions_dir or os.path.join(HOME, '.claude', 'sessions'))
    by_pid = {p['pid']: p for p in procs}
    out, seen = [], set()
    for p in procs:
        agent = agent_of(p.get('args'))
        if not agent:
            continue
        parent = by_pid.get(p.get('ppid'))
        if parent and agent_of(parent.get('args')):
            continue  # a launcher's child (node under the claude shim): the outer one counts
        job = job_of(p['pid'])
        if not job or job in NOT_JOBS or HUMAN_UNITS.search(job):
            continue
        sid = reg.get(p['pid'])
        if not sid:
            m = SESSION_ARG.search(p.get('args') or '')
            sid = m.group(1) if m else None
        key = (job, sid or p['pid'])
        if key in seen:
            continue
        seen.add(key)
        mm = re.search(r'--model[ =]([\w.:/-]+)', p.get('args') or '')
        ee = re.search(r'--effort[ =](low|medium|high|xhigh|max)\b', p.get('args') or '')
        out.append({'job': job, 'agent': agent, 'session': f'{agent}:{sid}' if sid else None,
                    'pid': p['pid'], 'started': p.get('started'),
                    'model': mm.group(1) if mm else None, 'effort': ee.group(1) if ee else None})
    return out


if __name__ == '__main__':  # python3 collector/llm.py [command words…]: what a command's scripts call
    if len(sys.argv) > 1:
        print(json.dumps(sniff(sys.argv[1:]), indent=1))
    else:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import live
        print(json.dumps(runs(live.list_processes()), indent=1))
