# Blueprint authoring prompt

Paste everything below the line into ChatGPT, then paste your lesson material after it.
It encodes every rule the MAIQE engine actually enforces, including the four that
silently produce broken-but-passing blueprints if you don't know them.

---

You are authoring generative question **blueprints** for a deterministic question
engine (MAIQE). I will give you lesson material. You will return runnable blueprint
files — not prose, not sample questions.

Everything you produce must run unmodified. A blueprint that "looks right" but
violates one of the rules below fails silently: it generates questions that are
subtly wrong, or that break bank deduplication, or whose wrong answers are secretly
the right answer. Treat the rules as a compiler, not as style advice.

## 0. What you are producing

For each topic, **two files that share a stem**:

- `<topic>.json` — the blueprint
- `<topic>.j2` — the Jinja2 template

Hard requirements:
- The JSON's `"topic"` field **must equal the filename stem**.
- `"constraints_template"` **must equal** `"<stem>.j2"`.
- The stem must be unique across the whole project (there are two blueprint
  directories and they share one namespace).

## 1. FIRST decide: does this need new Python?

This is the most important decision you will make, so make it explicitly and tell me
the answer before you write anything.

The engine can compute an answer in five ways. **Four need no Python from anyone.**
Prefer them. Only the fifth costs engineering time.

| `answer.type` | What it computes | Requirements |
|---|---|---|
| `roots` | Two integer roots of `a·x² + b·x + c = 0` | `spec` must contain `a`, `b`, `c` and the two root parameters. `answer.values` names them, e.g. `["r1","r2"]`. **Integer roots only** — no surds, no fractions. |
| `progression` | Arithmetic-progression term and sum | `answer.nth_term` and `answer.sum_n` are expressions; `spec` needs `a1`, `d`, `n`. |
| `integral_definite` | A definite integral | `answer.expression`, `var`, `from`, `to`. |
| `static_choice` | **Anything you can write as text** | `answer.correct` is a **Jinja2 string rendered against the spec**. Each distractor's `transform` carries **literal replacement text**, applied with no evaluation. The canonical scalar form is a string; imported Qadam files may wrap it as `{"correct": "<Jinja text>"}`. No other wrapper keys are valid. |
| *(a new type)* | Exact symbolic math | Requires a new Python module. **Do not invent one silently.** |

**`static_choice` is the universal escape hatch and it needs no Python.** If the
answer is a formula, a set, a classification, a word, an equation, an interval — and
you can produce it with Jinja from the rolled parameters — use `static_choice`. Most
material can be expressed this way.

Choose a **new answer type only** when the answer requires exact symbolic arithmetic
that Jinja cannot do honestly: surds needing simplification, fractions needing
reduction to lowest terms, root sets whose *size varies by case*, or answers that
depend on filtering (e.g. "only the real roots"). In that case:

1. Say so explicitly at the top of your reply.
2. Still write both files, with `answer.type` set to your proposed name.
3. Add a section `## Python solver spec` describing, in precise prose:
   the exact answer for every branch, every distractor's arithmetic, and the
   worked-solution steps. A developer will implement it.

Never fake exact math in Jinja by rounding, by assuming a discriminant is a perfect
square, or by printing a decimal. Wrong is worse than unimplemented.

## 2. The JSON schema

```json
{
  "topic": "<must equal the filename stem>",
  "display_name": "<human title, in the material's language>",
  "curriculum_ref": "<grade + chapter>",
  "constraints_template": "<stem>.j2",
  "_comment": "<what this blueprint generates and how it is built>",
  "tag": { "name": "<label>", "slug": "<kebab-case>" },
  "default_difficulty": 2,

  "parameters": {
    "<name>": {
      "type": "integer" | "string",
      "options": [ ... ],
      "desc": "<what it controls>"
    }
  },

  "difficulty_overrides": {
    "1": { "<name>": { "options": [ ... ] } },
    "2": { "...": "..." },
    "3": { "...": "..." }
  },

  "derived":     { "<name>": "<expression>" },
  "constraints": [ "<expression>", "..." ],
  "integer_clean": true,

  "answer": { "type": "<one of the five>", "...": "..." },

  "distractors": [
    { "id": "<snake_case>", "desc": "<the student belief that produces it>",
      "transform": "<literal or Jinja replacement text>" }
  ],

  "narrative_settings": [ "<3 real-world framings the story-writer may use>" ]
}
```

Notes:
- **Do not** put a `difficulty` parameter in `parameters`. Difficulty is passed in
  from outside and selects a `difficulty_overrides` block.
- Every parameter uses an **`options` list**. The engine picks with `random.choice`.
- `difficulty_overrides["N"]` patches parameter definitions for that band only.
- For a scalar `static_choice` answer, prefer a string `transform`. The engine also
  accepts the imported form `{"correct": "<text>"}` and unwraps it strictly. For a
  dictionary answer, `transform` remains a dictionary containing only the fields the
  misconception changes.

## 3. The expression dialect — read this twice

`derived` values and `constraints` entries are Python expressions evaluated with
**no builtins**. These are the sharp edges. Three of them fail *silently*.

| Rule | Why it matters |
|---|---|
| **`//` is a COMMENT delimiter.** Everything after it is stripped. | `(2*b) // a` does not error — it silently evaluates `(2*b)`. **Never use floor division.** Reformulate with multiplication: instead of `x < -b - 2*b//a`, multiply through and write `a*x + b < 0`. |
| **`^` is XOR, not a power.** | Not remapped, on purpose. Write `**`. `p^2` silently computes a bitwise xor. |
| **`->` is implication.** `A -> B` means "if A then B". | Right-associative, binds loosest. Use it freely; it reads far better than hand-negated `or`. |
| Available names: rolled parameters, earlier `derived` values, and exactly `abs`, `gcd`, `min`, `max`. | No other function calls. No attribute access, no imports, no comprehensions with function calls. |
| **`derived` keys must not start with `_`.** | Every value in `derived` is evaluated. A prose note there is a crash. Put notes in a separate top-level key such as `_derived_notes`. |

Order of operations per roll:
1. Every parameter is rolled from its (possibly overridden) `options`.
2. `derived` is evaluated **in declaration order**; each result enters scope, so a
   later expression can use an earlier one.
3. **`derived` may overwrite a parameter of the same name.** This is the intended
   canonicalisation mechanism — see rule 4.1.
4. Every `constraints` expression must be `True`, otherwise the whole roll is
   discarded and repeated (max 1000 attempts, then it raises).

Because constraints run *after* derived, **constraints may reference derived values.**
Use that: it is far easier to constrain `t_1 != t_2` than to constrain the six raw
parameters that produce them.

## 4. The four rules that cause silent failures

### 4.1 Canonicalise every unused parameter (the identity trap)

The engine hashes the **entire spec** to deduplicate the question bank. A parameter
that a branch does not use, but still rolls, produces different hashes for what is
the same question — and deduplication silently stops working.

So: if your blueprint has variants (a `structure` / `task_type` / `query_type`
parameter selecting disjoint parameter groups), **every parameter outside the active
group must be pinned in `derived`**:

```json
"derived": {
  "quad_p": "quad_p if structure == 'repeated_product' else 0",
  "pair_center": "pair_center if structure == 'paired' else 0"
}
```

`difficulty_overrides` is **not** sufficient for this: a single difficulty band
usually holds more than one variant, and overrides cannot reach inside a band.

Also canonicalise **symmetry**. If swapping two parameters yields the identical
question, force an order (`root_1 < root_2`, `p < r`). Otherwise the same question is
generated under two hashes.

### 4.2 Build backwards, from the answer

Do not roll coefficients and hope the answer comes out clean. **Roll the answer, then
compute the coefficients.**

Rolling `a`, `b`, `c` and requiring `b² − 4ac` to be a perfect square is rejection
sampling on a rare event — slow, and it biases the sample. Instead roll the integer
roots `r1`, `r2` and derive `b = -a·(r1+r2)`, `c = a·r1·r2`. Every question is then
clean **by construction**, with no constraint needed.

This applies everywhere: roll the roots, the factors, the intended value — then build
the presented problem from them.

### 4.3 Rejection sampling is the wrong tool for pinning

A constraint requiring two independently rolled parameters to coincide
(`p == r and q == s`) will fire on a tiny fraction of rolls — in practice ~1%. If you
want two things equal, **make them equal in `derived`**, don't ask the sampler to
stumble onto it:

```json
"r": "p if form == 'repeated_root' else r"
```

### 4.4 A distractor must be determinate, and must never equal the correct answer

Two separate failures, both silent:

**Not determinate.** Some student errors lead to a *dead end*, not to a wrong number
("expanded the brackets and got stuck", "chose a substitution that doesn't reduce").
There is no answer such a student would write down. **Do not invent one.** Drop the
distractor, and record it under a top-level `"_dropped_distractors"` key with a
`why_dropped` explanation, so nobody re-adds it later.

**Collapses onto the correct answer.** This is the dangerous one. Check algebraically
whether your distractor's arithmetic can coincide with the correct answer, and add a
constraint forbidding that case. Real examples from this codebase:

- "forgot to divide by P" gives the *right* answer whenever `|P| = 1` → add `abs(P) != 1`
- "computed P − x₁ instead of P/x₁" coincides exactly when `S = P`, i.e. `b + c = 0` → add `b + c != 0`
- "kept the inadmissible negative t" is correct whenever `|t₁| = |t₂|` → forbid it

A distractor that collapses is deduplicated away, so the slate still looks valid and
every generic test still passes. The item has simply stopped testing anything.

### 4.5 The option slate

The publisher requires **exactly 4 options, all distinct, exactly 1 correct**. So the
blueprint needs enough distractors that at least 3 always produce distinct text on
every roll. List **variant-specific distractors first** — slots are filled in order
and stop at 4, so a generic sign-slip listed first will crowd out the specific error
the question exists to catch.

### 4.6 Weight by measurement, not intuition

Constraints reject unevenly, so equal `options` do **not** give an equal mix. Weight
by **repeating entries** in the `options` list:

```json
"options": ["easy_form", "easy_form", "easy_form", "hard_form"]
```

State in a sibling `"_weighting"` key what distribution you were correcting and why.
If you cannot measure, say so and propose the weighting as a guess to be verified.

## 5. The `.j2` template

Structure — the marker line is mandatory and must appear **verbatim**:

```
PROBLEM TO POSE (turn ONLY this into the word problem; keep every number exactly as written):
- <the equation / givens, built from spec values>
- <what to ask the student for>

=== INTERNAL (guidance for you only — never show, restate, paraphrase, number, or hint any of this to the student) ===
- <method reminders, the substitution to use, the classic errors>
- Render all math as LaTeX inside $...$.
- Do NOT state or hint at the answer: <name the specific things that must not appear>.
```

Rules:
- Everything **above** the marker is shown to the student. A pure-Python reviewer
  checks number fidelity and answer leakage against this half — so it must contain
  every number the problem needs and **no other numbers**.
- **Read values from the spec; never recompute math the answer engine also computes.**
  If both the template and the solver calculate the coefficients, they can disagree,
  and you will ship a question whose statement doesn't match its answer key. Compute
  it once in `derived`, print it here.
- Use Jinja macros for repeated formatting (sign handling, eliding coefficients of 1).
- The instructions in the INTERNAL half are addressed to a writer LLM; write them in
  English. `display_name`, `desc` fields and any student-visible text follow the
  material's language.

## 6. Before you answer, verify each of these

1. `topic` == filename stem == `constraints_template` minus `.j2`.
2. No `//` anywhere in `derived` or `constraints`. No `^` used as a power.
3. No `derived` key starts with `_`; every `derived` value is a valid expression.
4. For every variant: list its parameters, and confirm **every other** parameter is
   pinned in `derived`.
5. Every symmetry (swappable pairs) is broken by an ordering constraint.
6. Problems are built backwards from rolled answers; no constraint asks the sampler
   to hit a rare coincidence.
7. For each distractor: is it determinate? Can it equal the correct answer for some
   parameter values? If yes — constrain that case away and say which constraint does it.
8. At least 3 distractors always yield distinct text; variant-specific ones listed first.
9. The template's student-facing half contains exactly the needed numbers, and reads
   every value from the spec.

## 7. Output format

1. **Answer-type decision** — which of the five, and why (one short paragraph).
2. `<topic>.json` in a fenced block.
3. `<topic>.j2` in a fenced block.
4. **Worked check** — two concrete rolls: the parameter values, the equation the
   template prints, and the correct answer computed by hand, so I can verify the math
   without running anything.
5. If you proposed a new answer type: `## Python solver spec`.
6. **Open questions** — anything in the material that was ambiguous, and what you
   assumed. Do not silently guess about mathematics.

Now here is the material:
