// app/lib/risk-engine.ts
// Deterministic position sizing. This module NEVER rounds up: it always
// rounds sizing down to the broker's lot step so realized risk can only be
// less than or equal to the planned risk, never more.

import type {
  BrokerSpec,
  RiskInput,
  RiskResult,
  RiskWarning,
} from "./types.ts";
import { RiskValidationError } from "./types.ts";

const MAX_RISK_PERCENT = 10;
const WARN_RISK_PERCENT = 2;
const WARN_MIN_REWARD_R = 1.5;
const WARN_MARGIN_OF_EQUITY = 0.5;

function isFiniteNumber(x: unknown): x is number {
  return typeof x === "number" && Number.isFinite(x);
}

function roundDownToStep(units: number, step: number): number {
  if (step <= 0) return units;
  return Math.floor(units / step) * step;
}

export function validateRiskInput(input: RiskInput): void {
  const { accountBalance, riskPercent, entry, stop, leverage } = input;

  for (const [name, value] of Object.entries({
    accountBalance,
    riskPercent,
    entry,
    stop,
    leverage,
  })) {
    if (!isFiniteNumber(value)) {
      throw new RiskValidationError(`${name} must be a finite number`);
    }
  }
  if (accountBalance <= 0) {
    throw new RiskValidationError("accountBalance must be positive");
  }
  if (entry <= 0 || stop <= 0) {
    throw new RiskValidationError("entry and stop must be positive prices");
  }
  if (leverage <= 0) {
    throw new RiskValidationError("leverage must be positive");
  }
  if (entry === stop) {
    throw new RiskValidationError("entry and stop cannot be identical");
  }
  if (riskPercent <= 0) {
    throw new RiskValidationError("riskPercent must be positive");
  }
  if (riskPercent > MAX_RISK_PERCENT) {
    throw new RiskValidationError(
      `riskPercent above ${MAX_RISK_PERCENT}% is rejected outright`
    );
  }
  if (input.target !== undefined && !isFiniteNumber(input.target)) {
    throw new RiskValidationError("target must be a finite number if provided");
  }
}

function lossPerUnitModelOnly(entry: number, stop: number): number {
  return Math.abs(entry - stop);
}

function lossPerUnitWithBrokerSpec(
  entry: number,
  stop: number,
  spec: BrokerSpec
): number {
  const priceDistance = Math.abs(entry - stop);
  const ticks = priceDistance / spec.tickSize;
  return ticks * spec.tickValue;
}

function requiredMarginFor(
  entry: number,
  units: number,
  spec: BrokerSpec | undefined,
  leverage: number
): number | null {
  if (!spec) return null;
  const notional = entry * units * spec.contractSize;
  if (spec.marginRate !== undefined) {
    return notional * spec.marginRate;
  }
  return notional / leverage;
}

export function computeRisk(input: RiskInput): RiskResult {
  validateRiskInput(input);
  const { accountBalance, riskPercent, entry, stop, target, leverage, brokerSpec } =
    input;

  const maxLossAmount = accountBalance * (riskPercent / 100);

  const lossPerUnit = brokerSpec
    ? lossPerUnitWithBrokerSpec(entry, stop, brokerSpec)
    : lossPerUnitModelOnly(entry, stop);

  if (lossPerUnit <= 0) {
    throw new RiskValidationError(
      "computed loss per unit is non-positive; check tick size/value"
    );
  }

  let positionUnits = maxLossAmount / lossPerUnit;

  const warnings: RiskWarning[] = [];

  if (brokerSpec) {
    positionUnits = roundDownToStep(positionUnits, brokerSpec.lotStep);
    positionUnits = Math.min(positionUnits, brokerSpec.maxVolume);
    if (positionUnits < brokerSpec.minVolume) {
      warnings.push({
        code: "PROVIDER_UNIT_CONSTRAINT",
        message: `Sized position (${positionUnits}) is below the broker minimum volume (${brokerSpec.minVolume}); order would be rejected at the venue.`,
      });
    }
  } else {
    warnings.push({
      code: "MISSING_BROKER_SPEC",
      message:
        "No broker specification supplied; sizing uses model-only assumptions and is not broker-verified.",
    });
  }

  // Reward/risk is a dimensionless price-distance ratio. `lossPerUnit` may
  // be monetary when broker tick value is available, so mixing it with a
  // raw target price distance would understate R by several orders.
  const rewardToRiskRatio =
    target !== undefined ? Math.abs(target - entry) / Math.abs(entry - stop) : null;

  const requiredMargin = requiredMarginFor(
    entry,
    positionUnits,
    brokerSpec,
    leverage
  );

  if (riskPercent > WARN_RISK_PERCENT) {
    warnings.push({
      code: "RISK_ABOVE_2_PERCENT",
      message: `Risking ${riskPercent}% of the account on a single position exceeds the ${WARN_RISK_PERCENT}% guideline.`,
    });
  }
  if (rewardToRiskRatio !== null && rewardToRiskRatio < WARN_MIN_REWARD_R) {
    warnings.push({
      code: "REWARD_BELOW_1_5R",
      message: `Reward-to-risk (${rewardToRiskRatio.toFixed(
        2
      )}R) is below the ${WARN_MIN_REWARD_R}R guideline.`,
    });
  }
  if (
    requiredMargin !== null &&
    requiredMargin > accountBalance * WARN_MARGIN_OF_EQUITY
  ) {
    warnings.push({
      code: "MARGIN_ABOVE_HALF_EQUITY",
      message: `Required margin (${requiredMargin.toFixed(
        2
      )}) exceeds ${WARN_MARGIN_OF_EQUITY * 100}% of account equity.`,
    });
  }

  return {
    maxLossAmount,
    positionUnits,
    lossPerUnit,
    rewardToRiskRatio,
    requiredMargin,
    sizingSource: brokerSpec ? "BROKER_VERIFIED" : "MODEL_ONLY",
    warnings,
  };
}
