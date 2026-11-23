"""The deterministic router against the intent corpus (no model, no Git repository).

Every case the router answers must be correct. Cases it does not cover are left to the
model (tests/llm_real measures those); they are listed so the gap stays visible.
"""

import pytest

from grippo.agent.router import route
from tests.llm_real.corpus import CASES, check_case


def _route(text):
    result = route(text, None)
    if result is None:
        return None
    return result.action, [(op, dict(args)) for op, args in result.steps]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["text"][:50])
def test_router_is_correct_when_it_answers(case):
    routed = _route(case["text"])
    if routed is None:
        pytest.skip("fora do roteador determinístico: decide o modelo")
    action, steps = routed
    problems = check_case(case, action, steps)
    assert not problems, problems


def test_router_covers_the_reference_git_requests():
    """The requests a user makes every day must not depend on the model."""
    must_cover = [
        "faça commit de todas alterações e push",
        "faça merge da develop na main",
        "tem alguma solicitação de pull request em develop?",
        "reverta o último commit",
        "aborte o merge",
        "faça rebase na develop",
        "mostre o diff",
    ]
    missing = [text for text in must_cover if _route(text) is None]
    assert not missing, missing
