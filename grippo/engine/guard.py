"""Anti-hallucination guard: destructive operations must be *grounded* in what the user said.

The LLM (or an external AI client talking to GRIPPO over MCP) only ever *proposes* a plan.
For operations that rewrite history or destroy data, every target they name — a branch,
a commit, a tag, a path — must appear in the user's own request, and "forcing" flags need
explicit wording. Otherwise the plan is refused *before* it reaches the confirmation
screen, so a model that invents ``branch_delete feature/old`` never gets to ask.

This is a second line of defence: the permission manager, the human confirmation and the
automatic backup still apply afterwards.
"""

from __future__ import annotations

import re

from grippo.engine.plan import Plan, Step
from grippo.text import fold

# op -> argument names that identify *what is being affected*.
TARGET_ARGS: dict[str, tuple[str, ...]] = {
    "rebase": ("upstream", "onto"),
    "revert": ("commit",),
    "reset": ("target",),
    "branch_delete": ("name",),
    "push_delete": ("branch",),
    "tag_delete": ("name",),
    "tag_delete_remote": ("tag",),
    "cherry_pick": ("commits",),
    "clean": ("paths",),
    "restore": ("paths",),
    "rm": ("paths",),
}

# op -> {condition: words (accent-folded) of which at least one must be in the request}.
# Condition: "arg" (truthy), "arg=value", or "*" (the operation itself).
FORCE_WORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "reset": {"mode=hard": ("hard", "descart", "forc", "destru", "perder", "perda")},
    "branch_delete": {"force": ("forc", "force", "nao mesclad", "sem merge", "mesmo assim")},
    "push": {"force": ("forc", "force"), "force_with_lease": ("forc", "force", "lease")},
    "clean": {
        "*": ("limp", "clean", "apag", "remov", "delet", "exclu", "nao rastread", "untracked"),
        "ignored": ("ignorad", "gitignore", "-x", "tudo", "all", "build", ".env"),
    },
    "stash_drop": {"*": ("stash",)},
}

# Revisions that can be implied by phrases like "o último commit".
_IMPLICIT = re.compile(r"^(HEAD|@)([~^]\d*)*$")
_IMPLICIT_WORDS = ("ultimo", "ultima", "anterior", "atual", "head", "last", "previous", "current")
_COUNT = re.compile(r"HEAD~(\d+)$")
_MAX_IMPLICIT_BACK = 5
_SHA_TOKEN = re.compile(r"[0-9a-f]{7,40}")
_WHOLE_TREE_OPS = frozenset({"restore", "clean", "rm"})
_REMOTE_NAMES = frozenset({"origin", "upstream"})


def _values(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]


def _candidates(needle: str) -> set[str]:
    """Spellings that ground ``needle``: ``origin/main`` is also grounded by ``main``."""
    remote, _, rest = needle.partition("/")
    return {needle, needle.rstrip("/")} | ({rest} if rest and remote in _REMOTE_NAMES else set())


def _mentioned(value: str, request: str, sha_tokens: list[str]) -> bool:
    needle = fold(value).strip()
    if not needle:
        return True
    for candidate in _candidates(needle):
        if re.search(rf"(?<![\w./-]){re.escape(candidate)}(?![\w-])", request):
            return True
        if _SHA_TOKEN.fullmatch(candidate) and any(
            candidate.startswith(token) or token.startswith(candidate) for token in sha_tokens
        ):  # a sha typed shorter or longer than the argument
            return True
    return False


def _implicit_ok(value: str, request: str) -> bool:
    if not _IMPLICIT.fullmatch(value):
        return False
    back = _COUNT.fullmatch(value)
    if back and int(back.group(1)) > _MAX_IMPLICIT_BACK:
        return False
    return value in request or any(word in request for word in _IMPLICIT_WORDS)


def _target_problems(step: Step, request: str, sha_tokens: list[str]) -> list[str]:
    problems = []
    for arg in TARGET_ARGS.get(step.op, ()):
        for value in _values(step.args.get(arg, [])):
            if value == "." and step.op in _WHOLE_TREE_OPS:
                problems.append(
                    f"{step.op} em '.' (tudo) exige que o pedido diga isso explicitamente"
                )
            elif not (_mentioned(value, request, sha_tokens) or _implicit_ok(value, request)):
                problems.append(
                    f"{step.op}: '{value}' ({arg}) não aparece no seu pedido — "
                    "não vou assumir um alvo que você não citou"
                )
    return problems


def _force_problems(step: Step, request: str) -> list[str]:
    problems = []
    for spec, words in FORCE_WORDS.get(step.op, {}).items():
        key, _, wanted = spec.partition("=")
        actual = step.args.get(key)
        active = key == "*" or (str(actual) == wanted if wanted else bool(actual))
        if active and not any(word in request for word in words):
            what = (
                "esta operação" if key == "*" else f"a opção destrutiva {spec if wanted else key}"
            )
            problems.append(f"{step.op}: o pedido não autoriza explicitamente {what}")
    return problems


def _stash_problems(step: Step, request: str) -> list[str]:
    """Dropping a stash other than the newest one requires naming its number."""
    index = step.args.get("index")
    if step.op != "stash_drop" or not index or re.search(rf"(?<!\d){index}(?!\d)", request):
        return []
    return [f"stash_drop: o stash número {index} não aparece no seu pedido"]


def grounding_problems(plan: Plan, user_request: str) -> list[str]:
    """Human-readable reasons the plan is *not* backed by ``user_request`` (empty = OK)."""
    request = fold(user_request)
    sha_tokens = _SHA_TOKEN.findall(request)
    return [
        problem
        for step in plan.steps
        for problem in (
            *_target_problems(step, request, sha_tokens),
            *_force_problems(step, request),
            *_stash_problems(step, request),
        )
    ]


def grounding_error(plan: Plan, user_request: str) -> str | None:
    """All grounding problems as one sentence, or ``None`` when the plan is grounded."""
    problems = grounding_problems(plan, user_request)
    return "; ".join(problems) if problems else None
