# Deterministic UBT Question Generator — Architecture and Logic

## Purpose

This project generates Kazakhstan UBT mathematics questions **without using an LLM to construct the mathematics**.

The generator is deterministic and configuration-driven:

```text
JSON configuration
        ↓
Python generator
        ↓
Difficulty selects allowed question structure
        ↓
Parameters are generated
        ↓
Constraints are validated
        ↓
Derived values are calculated
        ↓
Correct answer is computed
        ↓
Distractors are computed
        ↓
Symbolic uniqueness is validated
        ↓
Choices are shuffled
        ↓
Jinja renders the final question
```

The main design principle is:

> **Python owns mathematics. JSON defines generation rules. Jinja only renders presentation.**

## 1. Role of the JSON Configuration

The topic JSON is not a prompt. It acts as a declarative configuration for the deterministic Python engine.

It defines:
- which parameters exist;
- their allowed ranges or choices;
- which question structures are available;
- which structures belong to each difficulty level;
- mathematical constraints;
- derived values;
- correct-answer expressions;
- deterministic distractor expressions;
- validation requirements.

Example:

```json
{
  "k": {
    "type": "int",
    "min": 2,
    "max": 10
  },
  "m": {
    "type": "choice",
    "values": [2, 3, 5, 6, 7, 10, 11, 13]
  }
}
```

Python reads this and generates valid values such as:

```python
k = 4
m = 3
```

No natural-language reasoning is required.

## 2. Question Modes

A topic may contain several mathematically different question structures.

For example, the `roots_expressions` topic contains modes such as:

```text
extract_square_factor
insert_factor_under_root
rationalize_simple
combine_like_radicals
rational_exponent_exact
nth_root_extract
rationalize_conjugate
mixed_radical_fraction
```

Each mode declares:
- which parameters it uses;
- any mode-specific constraints;
- derived values;
- how the question should be represented;
- which answer formula applies;
- which distractor formulas apply.

Example:

```json
"extract_square_factor": {
  "uses": ["k", "m"],
  "derived": {
    "radicand": "k**2 * m"
  },
  "question": {
    "instruction": "Simplify",
    "latex": "\\sqrt{{{radicand}}}"
  }
}
```

If:

```python
k = 4
m = 3
```

then Python calculates:

```python
radicand = 4**2 * 3
          = 48
```

and the rendered question becomes:

```text
Simplify:

√48
```

## 3. Difficulty Logic

Difficulty should not be created only by making numbers larger.

For example, `√48` and `√158472` use essentially the same reasoning, even though the second one has larger arithmetic.

The preferred design is:

> **Difficulty changes the mathematical structure of the problem.**

Example:

```json
"difficulty_overrides": {
  "1": {
    "mode": {
      "values": [
        "extract_square_factor",
        "insert_factor_under_root",
        "rationalize_simple"
      ]
    }
  },
  "2": {
    "mode": {
      "values": [
        "combine_like_radicals",
        "rational_exponent_exact",
        "nth_root_extract"
      ]
    }
  },
  "3": {
    "mode": {
      "values": [
        "rationalize_conjugate",
        "mixed_radical_fraction"
      ]
    }
  }
}
```

### Difficulty 1

Direct application of one known rule.

Typical reasoning:

```text
recognize rule
→ apply rule
→ answer
```

### Difficulty 2

Requires recognition plus transformation.

Example:

```text
√50 + √18
```

The student must recognize:

```text
√50 = 5√2
√18 = 3√2
```

and then combine:

```text
5√2 + 3√2 = 8√2
```

Typical reasoning:

```text
identify structure
→ transform components
→ combine result
```

### Difficulty 3

Requires multiple connected transformations.

Example:

```text
3 / (√7 + 2)
```

The student must:

```text
recognize conjugate
→ multiply numerator and denominator
→ use difference of squares
→ simplify
```

The complexity comes from the reasoning structure, not merely larger numbers.

## 4. Parameter Generation

The Python engine generates only the parameters required by the selected mode.

Example mode:

```json
"combine_like_radicals": {
  "uses": ["k", "k2", "m"]
}
```

Suppose Python generates:

```python
k = 5
k2 = 3
m = 2
```

Then:

```json
"derived": {
  "r1": "k**2 * m",
  "r2": "k2**2 * m"
}
```

produces:

```python
r1 = 50
r2 = 18
```

The question becomes:

```text
√50 + √18
```

## 5. Constraints

Constraints prevent invalid or undesirable parameter combinations.

Example:

```json
"constraints": [
  "k != k2"
]
```

Conceptually:

```python
for attempt in range(MAX_ATTEMPTS):
    params = generate_parameters()

    if all(evaluate_constraint(c, params) for c in constraints):
        break
```

If:

```python
k = 4
k2 = 4
```

then `k != k2` fails, so Python regenerates the parameters.

Constraints are **machine-readable mathematical conditions**, not LLM instructions.

## 6. Derived Values

Derived values are calculated from generated parameters instead of being generated independently.

Example:

```json
"derived": {
  "radicand": "k**2 * m"
}
```

With:

```python
k = 4
m = 3
```

Python calculates:

```python
radicand = 48
```

This prevents mathematically inconsistent combinations.

## 7. Correct Answer Generation

The correct answer is calculated from a symbolic expression associated with the selected mode.

Example:

```json
"answer": {
  "type": "symbolic_by_mode",
  "expression": {
    "extract_square_factor": "k*sqrt(m)",
    "combine_like_radicals": "(k+k2)*sqrt(m)",
    "rationalize_simple": "p*sqrt(m)/m"
  }
}
```

For:

```python
k = 4
m = 3
```

the expression:

```text
k*sqrt(m)
```

evaluates to:

```text
4*sqrt(3)
```

which is rendered as `4√3`.

The intended implementation should use a symbolic mathematics library such as SymPy.

## 8. Distractor Generation

Wrong answers should not be random numbers. They should represent realistic mathematical mistakes.

Example:

```json
[
  {
    "id": "square_not_rooted",
    "transform": "k**2*sqrt(m)"
  },
  {
    "id": "factor_left_inside",
    "transform": "sqrt(k*m)"
  },
  {
    "id": "drop_radical",
    "transform": "k*m"
  }
]
```

With:

```python
k = 4
m = 3
```

these become:

```text
16√3
√12
12
```

Each distractor corresponds to a predictable student error.

## 9. Why Symbolic Uniqueness Validation Is Required

String comparison is not enough for mathematics.

For example:

```text
2√12
```

and:

```text
4√3
```

are mathematically equivalent.

Therefore this is not sufficient:

```python
choice1 != choice2
```

Instead, use symbolic equivalence:

```python
from sympy import simplify

def equivalent(a, b):
    return simplify(a - b) == 0
```

Then:

```python
equivalent(2*sqrt(12), 4*sqrt(3))
```

returns `True`.

## 10. Building Five Unique Choices

The required result is:

```text
1 correct answer
+
4 valid distractors
=
5 answer choices
```

Recommended design:

- define 5–7 possible distractors per mode;
- evaluate all of them;
- remove any distractor equivalent to the correct answer;
- remove duplicate distractors;
- remove undefined values;
- select four valid unique distractors;
- combine them with the correct answer;
- shuffle the five choices.

Conceptually:

```python
valid_distractors = []

for distractor in distractor_pool:
    candidate = evaluate(distractor["transform"])

    if equivalent(candidate, correct_answer):
        continue

    if any(equivalent(candidate, existing) for existing in valid_distractors):
        continue

    if not expression_is_valid(candidate):
        continue

    valid_distractors.append(candidate)
```

Then:

```python
if len(valid_distractors) < 4:
    regenerate_question()

choices = [
    correct_answer,
    *random.sample(valid_distractors, 4)
]

rng.shuffle(choices)
```

This provides a strong uniqueness guarantee.

## 11. Validation Configuration

The JSON can expose requirements such as:

```json
"validation": {
  "choice_count": 5,
  "single_correct_answer": true,
  "unique_choices": true,
  "symbolic_equivalence": true,
  "exact_answers_only": true
}
```

These fields do not perform validation by themselves. They tell Python which checks are mandatory.

## 12. Jinja's Role

Jinja should contain no mathematical generation logic.

Example:

```jinja2
{{ question.instruction }}

\[
{{ question.latex }}
\]

{% for choice in choices -%}
{{ ["A", "B", "C", "D", "E"][loop.index0] }}) \( {{ choice.latex }} \)
{% endfor %}
```

Python should already provide fully prepared data:

```python
{
    "question": {
        "instruction": "Simplify",
        "latex": "\\sqrt{48}+\\sqrt{12}"
    },
    "choices": [
        {"latex": "6\\sqrt{3}"},
        {"latex": "8\\sqrt{3}"},
        {"latex": "4\\sqrt{3}"},
        {"latex": "12"},
        {"latex": "2\\sqrt{3}"}
    ]
}
```

Jinja only displays it.

It does **not**:
- choose difficulty;
- generate numbers;
- calculate answers;
- create distractors;
- validate uniqueness;
- understand mathematical meaning.

## 13. Recommended Generation Algorithm

```python
def generate_question(topic_config, difficulty, seed=None):

    rng = Random(seed)

    schema = apply_difficulty_overrides(
        topic_config["parameters"],
        topic_config["difficulty_overrides"][str(difficulty)]
    )

    mode = generate_parameter(
        schema["mode"],
        rng
    )

    mode_config = topic_config["modes"][mode]

    for attempt in range(MAX_ATTEMPTS):

        params = generate_parameters(
            mode_config["uses"],
            schema,
            rng
        )

        if not constraints_pass(
            topic_config.get("constraints", []),
            params
        ):
            continue

        if not constraints_pass(
            mode_config.get("constraints", []),
            params
        ):
            continue

        derived = evaluate_derived(
            mode_config.get("derived", {}),
            params
        )

        context = {
            **params,
            **derived
        }

        answer_expr = (
            topic_config["answer"]
            ["expression"][mode]
        )

        correct = symbolic_eval(
            answer_expr,
            context
        )

        distractors = evaluate_distractors(
            topic_config["distractors"][mode],
            context
        )

        valid_distractors = filter_unique_distractors(
            correct,
            distractors
        )

        if len(valid_distractors) < 4:
            continue

        selected = rng.sample(
            valid_distractors,
            4
        )

        choices = [
            correct,
            *selected
        ]

        rng.shuffle(choices)

        question = build_question(
            mode_config["question"],
            context
        )

        return {
            "question": question,
            "choices": render_choices(choices),
            "answer": correct,
            "mode": mode,
            "difficulty": difficulty
        }

    raise GenerationError(
        "Could not produce a valid question"
    )
```

## 14. Deterministic Reproducibility

The generator should accept a random seed.

Example:

```python
generate_question(
    topic="roots_expressions",
    difficulty=2,
    seed=12345
)
```

The same seed should always reproduce the same:

```text
mode
parameters
derived values
question
correct answer
distractors
choice ordering
```

Benefits:
- reproducible tests;
- easier bug investigation;
- identical questions across environments;
- ability to regenerate a question from stored metadata;
- deterministic unit tests.

## 15. Recommended Stored Question Metadata

Store enough metadata to reproduce a question later.

Example:

```json
{
  "topic": "roots_expressions",
  "difficulty": 2,
  "mode": "combine_like_radicals",
  "seed": 12345,

  "parameters": {
    "k": 4,
    "k2": 2,
    "m": 3
  },

  "derived": {
    "r1": 48,
    "r2": 12
  }
}
```

This makes generated questions auditable.

## 16. Separation of Responsibilities

| Component | Responsibility |
|---|---|
| JSON | Defines the mathematical generation space |
| Difficulty configuration | Controls which mathematical structures can appear |
| Python generator | Generates parameters and coordinates the pipeline |
| SymPy | Computes, simplifies, and checks mathematical equivalence |
| Constraints | Prevent invalid parameter combinations |
| Derived expressions | Build mathematically consistent values |
| Answer expressions | Deterministically calculate the correct answer |
| Distractor expressions | Encode realistic student mistakes |
| Validator | Guarantees correctness and uniqueness |
| Jinja | Presentation only |
| Random seed | Provides reproducibility |

## 17. Important Design Rule

> If a field affects mathematical generation, it must be machine-readable and executable by Python.

Natural-language fields are fine for:
- display names;
- descriptions;
- curriculum metadata;
- developer comments.

Natural-language instructions should **not** control generation.

Weak:

```json
{
  "difficulty": 3,
  "description": "Make the problem require multiple reasoning steps"
}
```

Better:

```json
{
  "difficulty_overrides": {
    "3": {
      "mode": {
        "values": [
          "rationalize_conjugate",
          "mixed_radical_fraction"
        ]
      }
    }
  }
}
```

Python can act on the second version directly.

## 18. Known Improvement Required in Topic 01

The current Topic 01 design defines only three distractor expressions for several modes while requiring:

```json
"choice_count": 5
```

A five-choice UBT question requires:

```text
1 correct answer
+
4 unique incorrect answers
```

Recommended correction:

> Define at least 5–7 deterministic distractors per mode and let Python select four after symbolic validation.

This prevents failures caused by:
- a distractor matching the correct answer;
- two distractors simplifying to the same value;
- an undefined distractor;
- equivalent answer forms.

## 19. Overall Design Goal

The system should behave like a deterministic mathematical compiler:

```text
Curriculum definition
        ↓
Topic JSON
        ↓
Difficulty
        ↓
Mathematical variant
        ↓
Parameters
        ↓
Constraints
        ↓
Symbolic calculations
        ↓
Correct answer
        ↓
Error-based distractors
        ↓
Symbolic validation
        ↓
Five unique choices
        ↓
Jinja rendering
        ↓
Final UBT question
```

The generator does not depend on an LLM to understand or invent the problem.

Every meaningful mathematical decision is encoded explicitly and can be tested, reproduced, reviewed, and validated.
