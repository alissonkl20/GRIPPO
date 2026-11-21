"""GitPermissionManager — the single authority on whether a plan may run.

An :class:`Authorization` is an HMAC-signed, single-use, short-lived grant bound to
(plan digest, repository, branch, HEAD, working-tree fingerprint, extra bindings).
It can only be minted from an explicit :data:`Decision.CONFIRM`; silence, timeouts
and arbitrary strings never count as consent. Any change to the plan or to the
repository state invalidates it.
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from grippo.engine.operations import Risk
from grippo.engine.plan import PreparedPlan
from grippo.errors import AuthorizationError, StateChangedError


class Decision(Enum):
    CONFIRM = "confirm"
    CANCEL = "cancel"


AUTONOMY_LEVELS = {
    0: "análise somente",
    1: "consultas e recomendações",
    2: "operações mutáveis com confirmação",
    3: "autonomia ampliada dentro da política (risco alto sempre confirma)",
}


@dataclass(frozen=True)
class Requirement:
    allowed: bool
    needs_confirmation: bool
    risk: Risk
    reason: str | None = None


@dataclass(frozen=True)
class Authorization:
    binding: str
    risk: Risk
    issued_at: float
    expires_at: float
    nonce: str
    signature: str


class GitPermissionManager:
    def __init__(
        self,
        *,
        autonomy_level: int = 2,
        ttl_sec: float = 300.0,
        auto_approve: list[str] | None = None,
        secret: bytes | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if autonomy_level not in AUTONOMY_LEVELS:
            raise ValueError("autonomy_level inválido")
        self.autonomy_level = autonomy_level
        self.ttl_sec = ttl_sec
        self.auto_approve = frozenset(auto_approve or ())
        self._secret = secret or os.urandom(32)
        self._time = clock
        self._outstanding: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ policy
    def requirement(self, ops: list[tuple[str, Risk, bool]]) -> Requirement:
        """``ops`` = [(name, risk, mutating)] for every step of a plan."""
        risk = max((r for _, r, _ in ops), default=Risk.LOW)
        mutating = any(m for _, _, m in ops)
        if not mutating:
            return Requirement(True, False, risk)
        if self.autonomy_level < 2:
            return Requirement(
                False,
                True,
                risk,
                f"nível de autonomia {self.autonomy_level} "
                f"({AUTONOMY_LEVELS[self.autonomy_level]}) não executa operações mutáveis",
            )
        if risk is Risk.HIGH:
            return Requirement(True, True, risk)
        needs = any(
            mutating_op
            and r >= Risk.MODERATE
            and not (self.autonomy_level >= 3 and name in self.auto_approve)
            for name, r, mutating_op in ops
        )
        return Requirement(True, needs, risk)

    # ------------------------------------------------------------------ grants
    def _sign(self, binding: str, risk: Risk, issued: float, expires: float, nonce: str) -> str:
        message = f"{binding}|{int(risk)}|{issued:.6f}|{expires:.6f}|{nonce}".encode()
        return hmac.new(self._secret, message, "sha256").hexdigest()

    def grant(self, prepared: PreparedPlan, decision: Decision) -> Authorization | None:
        if decision is not Decision.CONFIRM:  # identity check: no strings, no truthiness
            return None
        if not prepared.allowed:
            raise AuthorizationError(prepared.denial_reason or "operação não permitida")
        if prepared.errors:
            raise AuthorizationError(
                "o plano tem erros de validação: " + "; ".join(prepared.errors)
            )
        issued = self._time()
        expires = issued + self.ttl_sec
        nonce = secrets.token_hex(16)
        binding = prepared.binding()
        with self._lock:
            self._outstanding[nonce] = expires
            while len(self._outstanding) > 256:
                self._outstanding.popitem(last=False)
        return Authorization(
            binding,
            prepared.risk,
            issued,
            expires,
            nonce,
            self._sign(binding, prepared.risk, issued, expires, nonce),
        )

    def revoke_all(self) -> None:
        with self._lock:
            self._outstanding.clear()

    def check(
        self, prepared: PreparedPlan, current_binding: str, authorization: Authorization | None
    ) -> None:
        """Raise unless ``prepared`` may run now. Consumes the authorization."""
        if not prepared.allowed:
            raise AuthorizationError(prepared.denial_reason or "operação não permitida")
        if not prepared.needs_confirmation:
            return
        if authorization is None:
            raise AuthorizationError(
                f"operação de risco {prepared.risk.label} exige confirmação explícita"
            )
        expected = self._sign(
            authorization.binding,
            authorization.risk,
            authorization.issued_at,
            authorization.expires_at,
            authorization.nonce,
        )
        if not hmac.compare_digest(expected, authorization.signature):
            raise AuthorizationError("autorização inválida (assinatura)")
        with self._lock:
            expires = self._outstanding.pop(authorization.nonce, None)
        if expires is None:
            raise AuthorizationError("autorização já utilizada ou revogada")
        if self._time() > authorization.expires_at:
            raise AuthorizationError("autorização expirada; confirme novamente")
        if authorization.risk < prepared.risk:
            raise AuthorizationError("autorização não cobre o nível de risco da operação")
        if not hmac.compare_digest(authorization.binding, prepared.binding()):
            raise AuthorizationError("a autorização foi concedida para outra operação")
        if not hmac.compare_digest(authorization.binding, current_binding):
            raise StateChangedError(
                "o repositório mudou depois da confirmação; revise e confirme novamente"
            )
