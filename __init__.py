"""Hermes plugin for Ponytail."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

DEFAULT_MODE = "full"
RUNTIME_MODES = {"off", "lite", "full", "ultra"}
CONFIG_MODES = RUNTIME_MODES | {"review"}
SKILL_COMMANDS = {
    "ponytail-review": "Review the current diff or provided target for over-engineering.",
    "ponytail-audit": "Audit the repo for over-engineering and deletion opportunities.",
    "ponytail-debt": "List every deliberate `ponytail:` shortcut and its upgrade path.",
    "ponytail-gain": "Show the measured-impact scoreboard (less code, less cost, more speed).",
    "ponytail-help": "Show the Ponytail command reference.",
}

ROOT = Path(__file__).resolve().parent
SKILLS_DIR = ROOT / "skills"
PONYTAIL_SKILL = SKILLS_DIR / "ponytail" / "SKILL.md"
REVIEW_SKILL = SKILLS_DIR / "ponytail-review" / "SKILL.md"

_current_mode = None
_rendered_skills = None

HERMES_SCOPE = """## Hermes scope

Skill-tool loads require explicit invocation or an allowed dependency of the selected owner. Do not select another main workflow, weaken requested coverage, or authorize writes, installs, agents, publication or runtime changes. Acknowledgement is not permission. Preserve the caller's scope and accepted decisions.

The plugin owns automatic style injection and off/level state; metadata does not enable another always-on route. A helper invoked for review/help reports only; it does not change mode or apply fixes. Use Hermes-native tools and current host instructions, not another agent application.
"""

HERMES_UPDATE = """## Update

Help is read-only. Report the installed state with `hermes plugins list` if requested; do not update, enable, disable or edit configuration merely to display this card.

An explicitly approved plugin update uses `hermes plugins update ponytail`. After an approved update, restart the relevant Hermes session and verify commands plus off/level behavior. A local worktree change is not applied automatically. Do not enable an automatic updater here.

"""


HERMES_DEBT_SCAN = """## Scan

Enumerate only the owning project's source files; exclude `.git`, dependencies,
`node_modules`, vendor, build/dist/target output, generated files and documentation.
Use the available file-search tool with explicit path/glob scope for `ponytail:`.
A regex hit is only a candidate, never an automatic ledger row.

Read each candidate in language context and confirm it belongs to a real source
comment, not a quoted string, test fixture, Markdown/code example or documentation.
If lexical context is ambiguous, leave it unconfirmed; do not guess a count.
Deduplicate confirmed comments by exact path:line, then compute totals from that
collection in code. Report unreadable/unsupported files as coverage gaps.

"""


def _hermes_skill_text(path: Path) -> str:
    """Keep shared upstream bodies intact; adapt only the Hermes read surface."""
    text = path.read_text(encoding="utf-8")
    frontmatter = re.match(r"^---\n[\s\S]*?\n---\n", text)
    if not frontmatter:
        raise ValueError(f"Missing skill frontmatter: {path}")
    body = text[frontmatter.end():]
    if path.parent.name == "ponytail-debt":
        if re.findall(r"(?m)^## (Scan|Output)\n", body) != ["Scan", "Output"]:
            raise ValueError("Ponytail debt structure changed; review Hermes adaptation before loading")
        body = re.sub(r"(?m)^## Scan\n[\s\S]*?(?=^## Output\n)", HERMES_DEBT_SCAN, body, count=1)
    if path.parent.name == "ponytail-help":
        body, hosts = re.subn(
            r"(?m)^Codex uses[\s\S]*?(?=^## Deactivate)",
            "Hermes uses the registered slash commands above. Do not install or switch to another agent application for this workflow.\n\n",
            body,
        )
        body, updates = re.subn(r"(?m)^## Update\n[\s\S]*?(?=^## More)", HERMES_UPDATE, body)
        if (hosts, updates) != (1, 1):
            raise ValueError("Ponytail help structure changed; review Hermes adaptation before loading")
    return frontmatter[0] + "\n" + HERMES_SCOPE + "\n" + body.lstrip("\n")


def _normalize_runtime_mode(mode: str | None) -> str | None:
    if not isinstance(mode, str):
        return None
    mode = mode.strip().lower()
    return mode if mode in RUNTIME_MODES else None


def _normalize_config_mode(mode: str | None) -> str | None:
    if not isinstance(mode, str):
        return None
    mode = mode.strip().lower()
    return mode if mode in CONFIG_MODES else None


def _config_dir() -> Path:
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "ponytail"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "ponytail"
    return Path.home() / ".config" / "ponytail"


def _default_mode() -> str:
    env_mode = _normalize_config_mode(os.environ.get("PONYTAIL_DEFAULT_MODE"))
    if env_mode:
        return env_mode
    try:
        data = json.loads((_config_dir() / "config.json").read_text(encoding="utf-8"))
        file_mode = _normalize_config_mode(data.get("defaultMode"))
        if file_mode:
            return file_mode
    except Exception:
        pass
    return DEFAULT_MODE


def _strip_frontmatter(text: str) -> str:
    return re.sub(r"^---[\s\S]*?---\s*", "", text or "", count=1)


def _filter_skill_body_for_mode(body: str, mode: str) -> str:
    effective = _normalize_runtime_mode(mode) or DEFAULT_MODE
    lines = []
    for line in _strip_frontmatter(body).splitlines():
        table_label = re.match(r"^\|\s*\*\*(.+?)\*\*\s*\|", line)
        if table_label:
            label_mode = _normalize_runtime_mode(table_label.group(1))
            if label_mode and label_mode != effective:
                continue

        example_label = re.match(r"^-\s*([^:]+):\s*", line)
        if example_label:
            label_mode = _normalize_runtime_mode(example_label.group(1))
            if label_mode and label_mode != effective:
                continue

        lines.append(line)
    return "\n".join(lines)


def _fallback_instructions(mode: str) -> str:
    return (
        f"PONYTAIL MODE ACTIVE — level: {mode}\n\n"
        "You are a lazy senior developer. Lazy means efficient, not careless. "
        "The best code is the code never written.\n\n"
        "Before any code, stop at the first rung that holds: YAGNI, stdlib, "
        "native platform, installed dependency, one line, then minimum code. "
        "No unrequested abstractions, avoidable dependencies, boilerplate, or "
        "speculative scaffolding. Deletion over addition. Boring over clever. "
        "Do not simplify away trust-boundary validation, data-loss handling, "
        "security, accessibility, explicitly requested behavior, or one small "
        "runnable check for non-trivial logic."
    )


def build_injected_context(mode: str | None = None) -> str:
    """Return the mode-filtered Ponytail context injected before LLM turns."""
    configured = _normalize_config_mode(mode) or _default_mode()
    if configured == "off":
        return ""
    if configured == "review":
        try:
            body = _hermes_skill_text(REVIEW_SKILL)
            return f"PONYTAIL MODE ACTIVE — level: review\n\n{_strip_frontmatter(body)}"
        except OSError:
            return "PONYTAIL MODE ACTIVE — level: review. Review diffs for unnecessary complexity."

    effective = _normalize_runtime_mode(configured) or DEFAULT_MODE
    try:
        body = _hermes_skill_text(PONYTAIL_SKILL)
        return f"PONYTAIL MODE ACTIVE — level: {effective}\n\n{_filter_skill_body_for_mode(body, effective)}"
    except OSError:
        return _fallback_instructions(effective)


def _pre_llm_call(session_id: str = "", **_: Any) -> dict[str, str] | None:
    mode = _current_mode or _default_mode()
    context = build_injected_context(mode)
    return {"context": context} if context else None


def _skill_prompt(command: str, args: str = "") -> str:
    tail = args.strip()
    target = f"\n\nUser arguments: {tail}" if tail else ""
    return (
        f"/{command}\n"
        f"Load and follow the Hermes plugin skill `ponytail:{command}`. "
        f"{SKILL_COMMANDS[command]}{target}"
    )


def _slash_access_denied(event: Any, gateway: Any, command: str) -> bool:
    if gateway is None or event is None:
        return False
    checker = getattr(gateway, "_check_slash_access", None)
    source = getattr(event, "source", None)
    if checker is None or source is None:
        return False
    try:
        return checker(source, command) is not None
    except Exception:
        return True


def rewrite_gateway_command(event: Any = None, gateway: Any = None, **_: Any) -> dict[str, str] | None:
    """Rewrite authorized gateway /ponytail-* commands into normal agent prompts."""
    text = str(getattr(event, "text", "") or "").strip()
    if not text.startswith("/"):
        return None
    head, _, rest = text[1:].partition(" ")
    command = head.replace("_", "-").lower()
    if command not in SKILL_COMMANDS:
        return None
    if _slash_access_denied(event, gateway, command):
        return None
    return {"action": "rewrite", "text": _skill_prompt(command, rest)}


def _handle_mode_command(raw_args: str) -> str:
    global _current_mode
    arg = (raw_args or "").strip().lower()
    if not arg:
        mode = _current_mode or _default_mode()
        return f"Ponytail mode: {mode}. Use `/ponytail lite|full|ultra|off`."
    mode = _normalize_runtime_mode(arg)
    if not mode:
        return "Usage: /ponytail [lite|full|ultra|off]"
    _current_mode = mode
    return f"Ponytail mode set to {mode}."


def _make_skill_command_handler(ctx: Any, command: str) -> Callable[[str], str]:
    def handler(raw_args: str) -> str:
        prompt = _skill_prompt(command, raw_args or "")
        injected = False
        try:
            injected = bool(ctx.inject_message(prompt))
        except Exception:
            injected = False
        if injected:
            return f"Queued `{command}` for the agent."
        return prompt

    return handler


def register(ctx: Any) -> None:
    """Register Ponytail hooks, skills, and slash commands with Hermes."""
    global _rendered_skills
    # Native skill registration takes paths, not rendered content. These files
    # are disposable process-local output, never a second maintained library.
    if _rendered_skills is None:
        _rendered_skills = tempfile.TemporaryDirectory(prefix="ponytail-hermes-")
    for child in sorted(SKILLS_DIR.iterdir() if SKILLS_DIR.exists() else []):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            rendered = Path(_rendered_skills.name) / child.name / "SKILL.md"
            rendered.parent.mkdir(parents=True, exist_ok=True)
            rendered.write_text(_hermes_skill_text(skill_md), encoding="utf-8")
            ctx.register_skill(child.name, rendered)

    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("pre_gateway_dispatch", rewrite_gateway_command)

    ctx.register_command(
        "ponytail",
        _handle_mode_command,
        description="Set Ponytail lazy senior dev mode: lite, full, ultra, or off.",
        args_hint="[lite|full|ultra|off]",
    )
    for command, description in SKILL_COMMANDS.items():
        ctx.register_command(
            command,
            _make_skill_command_handler(ctx, command),
            description=description,
            args_hint="[target or notes]",
        )
