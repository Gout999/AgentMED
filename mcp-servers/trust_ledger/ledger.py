"""信任账本服务（spec §6 / §3.7 / §9.8；T8）。

口径：
- 一次动作 = 一个样本（动作内多条探针只算 1 个 trial）。
- 原始整数计数（epoch 内 successes/trials），不存比例。
- 双侧 Wilson 95% 下界 > 0.9 且白名单 R1 才可晋升；R2 永远逐次审批。
- MVP：3/3 → 下界≈0.4385 < 0.9 → 记账但拒绝晋升（PromotionRejected 入审计）。
- SUSPENDED 冷却 24h；冷却结束计数清零开新 epoch，且必须人工确认才 reinstate（D-001 Q8，不自动恢复）。

状态机（§3.7 迁移表子集，纯函数式实现）：
  MANUAL --R1白名单-->> ELIGIBLE --LB>0.9-->> AWAITING_CONFIRMATION
  任意态 --验证失败-->> SUSPENDED（冷却+epoch+1） --人工 reinstate-->> ELIGIBLE（新 epoch）
  任意态 --对账异常-->> BLOCKED_UNKNOWN（仅人工解锁）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.audit import AuditService
from common.config import Settings, get_settings
from common.errors import STATE_CONFLICT, VALIDATION_FAILED, McpError
from common.ids import new_entry_id
from common.tables import TrustLedger
from trust_ledger.wilson import PROMOTION_THRESHOLD, evaluate

RISK_CLASSES = ("R0_READ", "R1_REVERSIBLE_WRITE", "R2_HIGH_IMPACT")
AUTONOMY_STATES = ("MANUAL", "ELIGIBLE", "AWAITING_CONFIRMATION", "AUTO_ENABLED", "SUSPENDED", "BLOCKED_UNKNOWN")

# 默认白名单（R1 可逆写可晋升；示例动作，控制面可按需传入）
DEFAULT_R1_WHITELIST = {
    "case.triage",
    "workorder.draft.prompt",
    "notification.reply_origin",
}


@dataclass
class LedgerState:
    risk_class: str
    action_type: str
    autonomy_state: str
    epoch: int
    trials: int
    successes: int
    lower: float
    upper: float
    suspended_until: Optional[datetime] = None
    pending_promotion_ref: Optional[str] = None


class TrustLedgerService:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        r1_whitelist: set[str] | None = None,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.r1_whitelist = set(r1_whitelist or DEFAULT_R1_WHITELIST)
        self.audit = AuditService(session, self.settings)

    # ---------- 查询 ----------

    def _current_row(self, risk_class: str, action_type: str) -> Optional[TrustLedger]:
        return self.session.scalar(
            select(TrustLedger)
            .where(TrustLedger.risk_class == risk_class, TrustLedger.action_type == action_type)
            .order_by(TrustLedger.epoch.desc())
            .limit(1)
        )

    def _ensure_row(self, risk_class: str, action_type: str) -> TrustLedger:
        row = self._current_row(risk_class, action_type)
        if row is None:
            row = TrustLedger(
                risk_class=risk_class,
                action_type=action_type,
                epoch=1,
                successes=0,
                trials=0,
                autonomy_state="MANUAL",
            )
            self.session.add(row)
            self.session.flush()
        return row

    def get_state(self, risk_class: str, action_type: str) -> dict[str, Any]:
        self._validate_key(risk_class, action_type)
        row = self._current_row(risk_class, action_type)
        if row is None:
            return {
                "trust_key": f"{action_type}:{risk_class}",
                "risk_class": risk_class,
                "action_type": action_type,
                "autonomy_state": "MANUAL",
                "epoch": 1,
                "trials": 0,
                "successes": 0,
                "LB": 0.0,
                "UB": 1.0,
                "suspended_until": None,
            }
        interval, _ = evaluate(row.successes, row.trials, threshold=self.settings.promotion_threshold)
        return {
            "trust_key": f"{action_type}:{risk_class}",
            "risk_class": row.risk_class,
            "action_type": row.action_type,
            "autonomy_state": row.autonomy_state,
            "epoch": row.epoch,
            "trials": row.trials,
            "successes": row.successes,
            "LB": round(interval.lower, 6),
            "UB": round(interval.upper, 6),
            "suspended_until": row.suspended_until.isoformat() if row.suspended_until else None,
            "pending_promotion_ref": row.pending_promotion_ref,
        }

    # ---------- 记账：一次动作 = 一个样本 ----------

    def record_outcome(
        self,
        *,
        risk_class: str,
        action_type: str,
        success: bool,
        action_ref: str,
        causation_id: str = "",
        detail: str = "",
    ) -> dict[str, Any]:
        self._validate_key(risk_class, action_type)
        row = self._ensure_row(risk_class, action_type)

        if row.autonomy_state == "SUSPENDED":
            raise McpError(STATE_CONFLICT, f"{action_type} suspended during cooloff; reinstate required")
        if row.autonomy_state == "BLOCKED_UNKNOWN":
            raise McpError(STATE_CONFLICT, f"{action_type} blocked unknown; manual unlock required")

        now = datetime.now(timezone.utc)
        whitelisted = risk_class == "R1_REVERSIBLE_WRITE" and action_type in self.r1_whitelist

        # MANUAL → ELIGIBLE：仅白名单 R1 可开始记账（§3.7）
        if row.autonomy_state == "MANUAL" and whitelisted:
            row.autonomy_state = "ELIGIBLE"

        # 应用本条样本（记录在"旧 epoch"，即本条动作所属 epoch）
        row.trials += 1
        if success:
            row.successes += 1
        failed = not success

        entry: dict[str, Any]
        if failed:
            # 验证失败 → SUSPENDED + 冷却 + epoch+1（计数清零重攒）
            old_epoch = row.epoch
            row.autonomy_state = "SUSPENDED"
            row.suspended_until = now + timedelta(hours=self.settings.cooloff_hours)
            interval, _ = evaluate(row.successes, row.trials, threshold=self.settings.promotion_threshold)
            entry = self._build_entry(
                row, success, action_ref, causation_id, detail,
                decision="not_evaluable",
                evidence_table_ref=None,
            )
            # 新 epoch 行
            new_row = TrustLedger(
                risk_class=risk_class,
                action_type=action_type,
                epoch=old_epoch + 1,
                successes=0,
                trials=0,
                autonomy_state="SUSPENDED",
                suspended_until=row.suspended_until,
            )
            self.session.add(new_row)
            self.audit.record(
                actor="controller:trust-ledger",
                action="trust.suspended",
                target=f"{action_type}:{risk_class}",
                params={"epoch": old_epoch, "reason": detail or "verification_failed", "action_ref": action_ref},
                result="denied",
            )
            self.session.flush()
            return entry

        # 成功路径：重算 Wilson 并评估晋升
        interval, eligible = evaluate(
            row.successes, row.trials, threshold=self.settings.promotion_threshold
        )
        decision = self._promotion_decision(risk_class, action_type, row, eligible)
        self._apply_promotion_decision(row, decision, interval)
        entry = self._build_entry(
            row, success, action_ref, causation_id, detail,
            decision=decision,
            evidence_table_ref=row.pending_promotion_ref,
        )
        self.session.flush()
        return entry

    # ---------- 晋升评估（纯函数） ----------

    def evaluate_promotion(self, risk_class: str, action_type: str) -> dict[str, Any]:
        """纯函数：白名单 + R1 + LB>0.9。MVP 口径下输出拒绝判定（含数字）。"""
        self._validate_key(risk_class, action_type)
        row = self._current_row(risk_class, action_type)
        if row is None:
            return {
                "eligible": False,
                "decision": "not_evaluable",
                "reason": "no evidence yet",
                "trials": 0,
                "successes": 0,
                "lower": 0.0,
            }
        interval, eligible = evaluate(row.successes, row.trials, threshold=self.settings.promotion_threshold)
        decision, reason = self._eligibility_reason(risk_class, action_type, row, interval, eligible)
        return {
            "eligible": eligible and decision == "proposed",
            "decision": decision,
            "reason": reason,
            "trials": row.trials,
            "successes": row.successes,
            "lower": round(interval.lower, 6),
            "upper": round(interval.upper, 6),
        }

    # ---------- 提请晋升（飞书带证据表） ----------

    def request_promotion(
        self, risk_class: str, action_type: str, *, evidence_table_ref: str, requester: str = "controller:trust-ledger"
    ) -> dict[str, Any]:
        """达标后生成证据表经飞书提请；人确认才生效（§3.7）。R2 永远逐次审批。"""
        self._validate_key(risk_class, action_type)
        row = self._current_row(risk_class, action_type)
        if row is None:
            raise McpError(VALIDATION_FAILED, "no evidence recorded yet")

        if row.autonomy_state == "AWAITING_CONFIRMATION":
            # 已提请：幂等返回现有提请（人确认/拒绝前不再重复生成）
            return {
                "eligible": True,
                "autonomy_state": "AWAITING_CONFIRMATION",
                "evidence_table_ref": row.pending_promotion_ref or evidence_table_ref,
                "trials": row.trials,
                "successes": row.successes,
                "lower": round(evaluate(row.successes, row.trials)[0].lower, 6),
            }

        if row.autonomy_state not in ("ELIGIBLE",):
            raise McpError(STATE_CONFLICT, f"cannot request promotion from state {row.autonomy_state}")

        interval, eligible = evaluate(row.successes, row.trials, threshold=self.settings.promotion_threshold)
        decision, reason = self._eligibility_reason(risk_class, action_type, row, interval, eligible)
        if decision != "proposed":
            # 不达标：拒绝提请（含数字），供审计/反馈
            self.audit.record(
                actor=requester,
                action="trust.promotion_rejected",
                target=f"{action_type}:{risk_class}",
                params={"reason": reason, "successes": row.successes, "trials": row.trials},
                result="denied",
            )
            raise McpError(STATE_CONFLICT, reason)

        row.autonomy_state = "AWAITING_CONFIRMATION"
        row.pending_promotion_ref = evidence_table_ref
        self.audit.record(
            actor=requester,
            action="trust.promotion_proposed",
            target=f"{action_type}:{risk_class}",
            params={
                "successes": row.successes,
                "trials": row.trials,
                "lower": round(interval.lower, 6),
                "evidence_table_ref": evidence_table_ref,
            },
            result="success",
        )
        self.session.flush()
        return {
            "eligible": True,
            "autonomy_state": "AWAITING_CONFIRMATION",
            "evidence_table_ref": evidence_table_ref,
            "trials": row.trials,
            "successes": row.successes,
            "lower": round(interval.lower, 6),
        }

    def confirm_promotion(self, risk_class: str, action_type: str, *, confirmed_by: str) -> dict[str, Any]:
        """人工确认 → AUTO_ENABLED（仅白名单 R1；R2 永远逐次审批）。"""
        self._validate_key(risk_class, action_type)
        row = self._current_row(risk_class, action_type)
        if row is None or row.autonomy_state != "AWAITING_CONFIRMATION":
            raise McpError(STATE_CONFLICT, "no pending promotion to confirm")
        if risk_class != "R1_REVERSIBLE_WRITE" or action_type not in self.r1_whitelist:
            raise McpError(STATE_CONFLICT, "R2 or non-whitelisted action can never be AUTO_ENABLED")
        row.autonomy_state = "AUTO_ENABLED"
        self.audit.record(
            actor=confirmed_by,
            action="trust.promotion_confirmed",
            target=f"{action_type}:{risk_class}",
            params={"epoch": row.epoch},
            result="success",
        )
        self.session.flush()
        return {"autonomy_state": "AUTO_ENABLED", "risk_class": risk_class, "action_type": action_type}

    def reject_promotion(self, risk_class: str, action_type: str, *, rejected_by: str, reason: str) -> dict[str, Any]:
        """人工拒绝提请 → 回 ELIGIBLE，继续攒证据。"""
        self._validate_key(risk_class, action_type)
        row = self._current_row(risk_class, action_type)
        if row is None or row.autonomy_state != "AWAITING_CONFIRMATION":
            raise McpError(STATE_CONFLICT, "no pending promotion to reject")
        row.autonomy_state = "ELIGIBLE"
        row.pending_promotion_ref = None
        self.audit.record(
            actor=rejected_by,
            action="trust.promotion_rejected_by_human",
            target=f"{action_type}:{risk_class}",
            params={"reason": reason},
            result="denied",
        )
        self.session.flush()
        return {"autonomy_state": "ELIGIBLE"}

    # ---------- SUSPENDED：冷却 + 人工 reinstate（D-001 Q8） ----------

    def suspend(self, risk_class: str, action_type: str, *, reason: str = "") -> dict[str, Any]:
        """显式 suspend：验证失败 → SUSPENDED + 冷却 + epoch+1。"""
        self._validate_key(risk_class, action_type)
        row = self._ensure_row(risk_class, action_type)
        now = datetime.now(timezone.utc)
        old_epoch = row.epoch
        row.autonomy_state = "SUSPENDED"
        row.suspended_until = now + timedelta(hours=self.settings.cooloff_hours)
        new_row = TrustLedger(
            risk_class=risk_class,
            action_type=action_type,
            epoch=old_epoch + 1,
            successes=0,
            trials=0,
            autonomy_state="SUSPENDED",
            suspended_until=row.suspended_until,
        )
        self.session.add(new_row)
        self.audit.record(
            actor="controller:trust-ledger",
            action="trust.suspended",
            target=f"{action_type}:{risk_class}",
            params={"reason": reason, "epoch": old_epoch},
            result="denied",
        )
        self.session.flush()
        return {
            "autonomy_state": "SUSPENDED",
            "epoch": old_epoch + 1,
            "suspended_until": row.suspended_until.isoformat(),
        }

    def reinstate(self, risk_class: str, action_type: str, *, confirmed_by: str) -> dict[str, Any]:
        """人工 reinstate：冷却期满才可；计数清零开新 epoch（Q8，不自动恢复）。"""
        self._validate_key(risk_class, action_type)
        row = self._current_row(risk_class, action_type)
        if row is None or row.autonomy_state != "SUSPENDED":
            raise McpError(STATE_CONFLICT, "not suspended; nothing to reinstate")
        now = datetime.now(timezone.utc)
        until = row.suspended_until
        if until is not None:
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if now < until:
                raise McpError(
                    STATE_CONFLICT,
                    f"cooloff not elapsed; suspend until {until.isoformat()}",
                )
        row.autonomy_state = "ELIGIBLE"
        row.suspended_until = None
        # 当前 epoch 即新 epoch（计数已在 suspend 时清零）；reinstate 后继续攒
        self.audit.record(
            actor=confirmed_by,
            action="trust.reinstated",
            target=f"{action_type}:{risk_class}",
            params={"epoch": row.epoch, "confirmed_by": confirmed_by},
            result="success",
        )
        self.session.flush()
        return {"autonomy_state": "ELIGIBLE", "epoch": row.epoch}

    # ---------- 内部 ----------

    def _promotion_decision(
        self, risk_class: str, action_type: str, row: TrustLedger, eligible: bool
    ) -> str:
        if risk_class == "R2_HIGH_IMPACT":
            return "not_evaluable"
        if risk_class != "R1_REVERSIBLE_WRITE" or action_type not in self.r1_whitelist:
            return "not_evaluable"
        if not eligible:
            return "denied"
        return "proposed"

    def _apply_promotion_decision(
        self, row: TrustLedger, decision: str, interval: Any
    ) -> None:
        if decision == "denied":
            reason = f"{row.successes}/{row.trials} LB={interval.lower:.4f}<{self.settings.promotion_threshold}"
            self.audit.record(
                actor="controller:trust-ledger",
                action="trust.promotion_rejected",
                target=f"{row.action_type}:{row.risk_class}",
                params={
                    "reason": reason,
                    "successes": row.successes,
                    "trials": row.trials,
                    "lower": round(interval.lower, 6),
                },
                result="denied",
            )
        elif decision == "proposed":
            # 达标：自动生成证据表引用（提请由控制面经飞书 dispatch）
            ref = f"trust://evidence/{row.action_type}:{row.risk_class}/epoch/{row.epoch}"
            row.autonomy_state = "AWAITING_CONFIRMATION"
            row.pending_promotion_ref = ref
            self.audit.record(
                actor="controller:trust-ledger",
                action="trust.promotion_proposed",
                target=f"{row.action_type}:{row.risk_class}",
                params={
                    "successes": row.successes,
                    "trials": row.trials,
                    "lower": round(interval.lower, 6),
                    "evidence_table_ref": ref,
                },
                result="success",
            )

    def _build_entry(
        self,
        row: TrustLedger,
        success: bool,
        action_ref: str,
        causation_id: str,
        detail: str,
        *,
        decision: str,
        evidence_table_ref: Optional[str],
    ) -> dict[str, Any]:
        interval, _ = evaluate(row.successes, row.trials, threshold=self.settings.promotion_threshold)
        return {
            "entry_id": new_entry_id(),
            "trust_key": f"{row.action_type}:{row.risk_class}",
            "risk_class": row.risk_class,
            "action_type": row.action_type,
            "outcome": {
                "status": "success" if success else "failure",
                "action_ref": action_ref,
                "detail": detail,
            },
            "sample_rule": "one_action_one_sample",
            "evidence_epoch": row.epoch,
            "epoch_successes": row.successes,
            "epoch_trials": row.trials,
            "wilson": {
                "confidence": 0.95,
                "side": "two-sided",
                "z": 1.96,
                "lower": round(interval.lower, 6),
                "upper": round(interval.upper, 6),
            },
            "autonomy_state_before": "SUSPENDED" if row.autonomy_state == "SUSPENDED" else row.autonomy_state,
            "autonomy_state_after": row.autonomy_state,
            "promotion": {
                "eligible": decision == "proposed",
                "threshold": self.settings.promotion_threshold,
                "decision": decision,
                "evidence_table_ref": evidence_table_ref,
            },
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "causation_id": causation_id or "",
        }

    @staticmethod
    def _eligibility_reason(
        risk_class: str, action_type: str, row: TrustLedger, interval: Any, eligible: bool
    ) -> tuple[str, str]:
        if risk_class == "R2_HIGH_IMPACT":
            return "not_evaluable", "R2_HIGH_IMPACT 永远逐次审批（T8 硬约束）"
        if risk_class != "R1_REVERSIBLE_WRITE" or action_type not in DEFAULT_R1_WHITELIST:
            return "not_evaluable", f"{action_type} 不在白名单内，不得晋升"
        if not eligible:
            return "denied", f"{row.successes}/{row.trials} LB={interval.lower:.4f}<{PROMOTION_THRESHOLD}"
        return "proposed", "达到统计条件，提请人工确认"

    @staticmethod
    def _validate_key(risk_class: str, action_type: str) -> None:
        if risk_class not in RISK_CLASSES:
            raise McpError(VALIDATION_FAILED, f"risk_class must be one of {RISK_CLASSES}")
        if not action_type or not action_type.replace("_", "").replace(".", "").isalnum():
            raise McpError(VALIDATION_FAILED, f"invalid action_type: {action_type!r}")
