# Risk Engine

## Position sizing

The deterministic risk engine accepts account balance, risk percentage, entry, stop, target, instrument metadata, leverage, and an optional broker specification.

```text
maximum loss = account balance × risk percentage
position units = maximum loss ÷ loss per unit at stop
```

Without broker data, Atlas uses documented analytical assumptions and labels the result `MODEL ONLY`. With MT5, it uses the broker's tick size/value, contract size, minimum/maximum volume, lot step, and account leverage. With OANDA it applies provider pip location, unit precision, limits, and margin rate. Both paths round down so sizing cannot exceed planned risk and label the specification source as broker verified.

## Validation and limits

The engine rejects non-finite or non-positive balance/prices/leverage, identical entry and stop, and risk above 10%. It warns about risk above 2%, reward below 1.5R, margin above half of equity, missing broker specifications, and provider unit constraints.

Broker verification does not make a plan executable. Spreads, slippage, currency conversion, tiered margin, commissions, swap, market hours, and order acceptance can still change the result. Confirm the final ticket in the execution venue.
