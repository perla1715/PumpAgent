# ACP-003 --- Market Archetypes

**Status:** Working hypothesis (approved for discussion)

## Background

PampAgent should not memorize isolated trading setups. Real market
situations differ in their indicators, but often belong to the same
underlying market behavior.

## Core Idea

PampAgent learns **market archetypes**, not fixed setups.

An archetype is a recurring market behavior that can appear in many
different forms.

## Knowledge Levels

### Level 1 --- Archetype

Example:

-   Pump → Exhaustion → Breakdown → Short Continuation

### Level 2 --- Features

Features strengthen or weaken confidence, but none is mandatory.

Examples:

-   Vertical Expansion
-   Volume Climax
-   EMA Compression
-   EMA Crossover
-   CVD Divergence
-   Long Liquidations
-   OI Decrease
-   Funding Extreme
-   Loss of Structure
-   Failed Recovery
-   Lower High
-   Breakdown Candle
-   Weak Bounce
-   Trend Continuation

### Level 3 --- Real Cases

Every completed trade is a concrete realization of an archetype.

Over time PampAgent accumulates hundreds of cases and statistically
evaluates which features are truly predictive.

## Decision Logic

Instead of asking:

> Does this exact setup exist?

PampAgent asks:

> How similar is the current market state to a known archetype?

## Guiding Principle

-   PampAgent does not memorize setups.
-   PampAgent accumulates statistically validated market archetypes.
-   A setup is only one manifestation of an archetype.

## Working Hypothesis

At the beginning, humans define the initial archetypes.

The agent accumulates evidence, evaluates statistics, and in the future
may propose new archetypes for Human Review.

This keeps the learning process:

-   controlled;
-   reproducible;
-   explainable;
-   safe.
