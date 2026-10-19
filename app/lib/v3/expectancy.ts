export interface ExpectancyInput {
  winProbability: number;
  expectedWin: number;
  expectedLoss: number;
  spreadCost: number;
  slippageCost: number;
  commissionCost: number;
}

export interface ExpectancyResult {
  grossExpectedValue: number;
  totalCosts: number;
  netExpectedValue: number;
}

function requireFinite(name: string, value: number): void {
  if (!Number.isFinite(value)) throw new Error(`${name} must be finite`);
}

export function calculateExpectancy(input: ExpectancyInput): ExpectancyResult {
  Object.entries(input).forEach(([name, value]) => requireFinite(name, value));
  if (input.winProbability < 0 || input.winProbability > 1) throw new Error("winProbability must be between 0 and 1");
  if (input.expectedWin <= 0 || input.expectedLoss <= 0) throw new Error("expectedWin and expectedLoss must be positive");
  if (input.spreadCost < 0 || input.slippageCost < 0 || input.commissionCost < 0) throw new Error("costs cannot be negative");
  const grossExpectedValue = input.winProbability * input.expectedWin - (1 - input.winProbability) * input.expectedLoss;
  const totalCosts = input.spreadCost + input.slippageCost + input.commissionCost;
  return { grossExpectedValue, totalCosts, netExpectedValue: grossExpectedValue - totalCosts };
}
