# UBT Question Generator

This project generates Kazakhstan UBT mathematics questions using
deterministic Python.

## Core architecture

- Do NOT use an LLM to generate mathematics questions.
- Python owns all mathematical generation and validation.
- JSON files contain machine-readable generation configuration.
- Jinja2 files only render already-generated mathematical data.
- Jinja templates must not contain prompt-like instructions.
- Difficulty must primarily come from different generation modes and
  reasoning structures, not merely larger numbers.

## Difficulty

Difficulty 1:
- direct computation
- immediately identifiable method
- usually one mathematical operation/method

Difficulty 2:
- transformation, reverse reasoning, or method selection
- typically two conceptual steps

Difficulty 3:
- combines multiple conditions/concepts
- typically three or more conceptual steps
- do not create difficulty simply by increasing coefficients

## Question requirements

- Exactly five answer choices.
- Exactly one correct answer unless a topic explicitly specifies otherwise.
- Choices must be unique.
- Distractors should correspond to deterministic mathematical mistakes.
- Every generated question must be reproducible and independently validated.