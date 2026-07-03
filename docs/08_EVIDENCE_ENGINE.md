# Evidence Engine

## Status

MVP implemented.

---

## Purpose

The Evidence Engine explains which observed metrics supported or weakened a
scan result.

It is an explanation layer, not a decision layer.

---

## MVP Inputs

`collect_evidence(data)` currently reads:

- `price_change_1m`
- `volume_spike_ratio`
- `oi_change_1m`

---

## MVP Rules

Price evidence is positive when `price_change_1m > 0`.

Volume evidence is positive when `volume_spike_ratio > 2`.

Open interest evidence is positive when `oi_change_1m > 0`.

All other cases produce negative evidence.

---

## Output

Each evidence item contains:

- `name`
- `value`
- `positive`

Scanner output may format evidence compactly:

`Evidence: + Price increasing; + Volume above average; - OI not increasing`

---

## Boundaries

Evidence does not:

- classify market state;
- calculate confidence;
- create hypotheses;
- assign probabilities;
- make decisions;
- generate alerts;
- imply trading action.

Later versions can attach richer evidence provenance and weights, but this MVP
stays deterministic and lightweight.
