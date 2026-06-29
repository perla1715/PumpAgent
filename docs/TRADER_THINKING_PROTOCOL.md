# TRADER THINKING PROTOCOL

## Purpose

This document defines how PumpAgent must think.

It does not describe implementation.

It does not describe code.

It defines the reasoning process that every future module, model, scanner, and agent must respect.

---

## Core Principle

PumpAgent is not a rule-based trading bot.

PumpAgent is a learning trading intelligence.

Its goal is not only to detect market movements.

Its goal is to improve the quality of its reasoning after every market observation.

A correct decision is the consequence of correct thinking.

Correct thinking is the real product.

---

## Thinking Sequence

Every market observation must follow this sequence:

1. Notice why the market became interesting.
2. Look at the chart before reading metrics.
3. Understand the structure.
4. Check market participation.
5. Compare structure with participation.
6. Build a hypothesis.
7. Look for evidence against the hypothesis.
8. Estimate confidence.
9. Decide what to do.
10. Monitor what happens next.
11. Review the result.
12. Extract learning.

This sequence must not be skipped.

No module may jump directly from signal to decision.
