# The θ (theta) mastery model — explained

A plain-language + worked-numbers guide to the per-topic student model that powers
the roadmap and the Chapter Ladder. Every number in this file is computed directly
from `apps/roadmap/mastery.py`.

Related code: `apps/roadmap/mastery.py` (the math), `apps/roadmap/models.py`
(`StudentTopicMastery`), `apps/roadmap/ladder.py` (the first caller).

---

## 1. What θ is

θ ("theta") is **one number per `(student, topic)` that estimates the student's
ability on that topic**. It is stored on `StudentTopicMastery.theta` and starts at
`0.0` for a brand-new topic.

θ lives on the **logit scale**, not a 0–100% scale. Both ability and question
difficulty sit on this one scale, so they can be compared by subtraction:

- **θ = 0** → average
- **θ > 0** → stronger; **θ < 0** → weaker
- Difficulty anchors (`DIFFICULTY_LOGITS`): `easy(1) = −1.0`, `medium(2) = 0.0`,
  `hard(3) = +1.0`.

Because both are on the same scale, the model asks *"given this θ and this
difficulty, how likely was a correct answer?"* with the **sigmoid** (logistic)
function, which maps any number into a 0–1 probability:

```
P(correct) = sigmoid(θ − difficulty_logit)      sigmoid(x) = 1 / (1 + e^(−x))
```

If ability equals difficulty (θ − d = 0), sigmoid(0) = 0.5 — a coin flip. This is
Item Response Theory (the Rasch model), simplified.

---

## 2. The update rule (Elo-lite)

Every answered question nudges θ (`mastery.py`):

```python
p_pred = sigmoid(theta - difficulty_to_logit(difficulty))   # what we expected
theta += learning_rate(n_observations) * (outcome - p_pred) # nudge by the surprise
n_observations += 1
```

- `outcome` = 1 (correct) or 0 (wrong).
- `(outcome − p_pred)` is the **prediction error / surprise**.
- `learning_rate(n) = 0.8 / (1 + n/5)` is the **step size K**, which shrinks as
  evidence accumulates (K = 0.8 at n=0, ≈0.67 at n=1, halves by n≈5).

So the move per answer is:

```
move = K · (outcome − p_pred)
```

θ moves in proportion to **how surprising the result was**, scaled by **how much we
still trust the estimate**.

---

## 3. Single-answer moves (fresh student: θ = 0, n = 0, K = 0.8)

`p_pred` at θ=0 depends only on difficulty:

- easy: sigmoid(0 − (−1.0)) = sigmoid(+1.0) = **0.731**
- medium: sigmoid(0 − 0.0) = sigmoid(0) = **0.500**
- hard: sigmoid(0 − (+1.0)) = sigmoid(−1.0) = **0.269**

Applying `move = 0.8 · (outcome − p_pred)`:

| Difficulty | p_pred | got it **right** | got it **wrong** |
|---|---|---|---|
| easy (d=1) | 0.731 | **+0.215** | **−0.585** |
| medium (d=2) | 0.500 | **+0.400** | **−0.400** |
| hard (d=3) | 0.269 | **+0.585** | **−0.215** |

**Intuition — surprising results move θ; expected results barely do.**
- Hard + right = **+0.585** (big: we predicted 27%, they passed).
- Easy + right = **+0.215** (small: already expected).
- Easy + wrong = **−0.585** (big: they missed something they should ace).
- Hard + wrong = **−0.215** (small: expected to miss anyway).
- Medium is the **symmetric** rung (±0.400) because p_pred = 0.5 exactly — which is
  why the ladder starts at medium (`LADDER_START_RUNG = 2`): a fresh student's first
  answer is equally informative whether right or wrong.

---

## 4. Reading θ back out

θ is internal. Two conversions make it usable:

- **Verdict label** (`verdict_for_theta`): θ ≥ **1.0** → `mastered`; θ ≥ **0.0** →
  `solid`; below → `gap`.
- **`p_mastery` = sigmoid(θ)** — a 0–1 probability for the UI (θ=0 → 0.50,
  θ=1 → 0.73, θ=−1 → 0.27).

`n_observations` rides alongside θ as its **confidence** — the same θ means far more
at n=15 than at n=1, which is exactly why the learning rate reads n.

---

## 5. Three full worked examples

All start from a fresh student (θ = 0, n = 0) and follow the ladder's rung logic.
Numbers are exact from `mastery.py`.

### 5a. MASTERED — clears hard (medium ✓ → hard ✓ → hard ✓ confirm)

| Step | K(n) | p_pred | θ before → after | verdict |
|---|---|---|---|---|
| medium correct (d=2) | 0.800 | 0.500 | 0.000 → **+0.400** | solid |
| hard correct (d=3) | 0.667 | 0.354 | 0.400 → **+0.830** | solid |
| hard correct (confirm, d=3) | 0.571 | 0.458 | 0.830 → **+1.140** | **mastered** |

**Final θ = +1.140 → `mastered`, p_mastery = 0.758.**
Worked middle row: gap = 0.400 − 1.0 = −0.6 → p_pred = sigmoid(−0.6) = 0.354;
surprise = 1 − 0.354 = 0.646; move = 0.667 × 0.646 = +0.430; θ = 0.400 + 0.430 = 0.830.
The third answer is the ladder's asymmetric confirm (a skip-granting correct answer is
re-checked). Note θ does **not** jump to 100% on three correct answers — it stays
conservative.

### 5b. SOLID — clears medium, misses hard (medium ✓ → hard ✗)

| Step | K(n) | p_pred | θ before → after | verdict |
|---|---|---|---|---|
| medium correct (d=2) | 0.800 | 0.500 | 0.000 → **+0.400** | solid |
| hard wrong (d=3) | 0.667 | 0.354 | 0.400 → **+0.164** | **solid** |

**Final θ = +0.164 → `solid`, p_mastery = 0.541.**
Worked second row: gap = 0.400 − 1.0 = −0.6 → p_pred = 0.354 (we expected them to miss
the hard one); surprise = 0 − 0.354 = −0.354; move = 0.667 × (−0.354) = −0.236;
θ = 0.400 − 0.236 = 0.164. A *deciding wrong* answer is accepted on a single attempt
(no confirm), so the ladder resolves here: cleared medium, missed hard → `solid`.

### 5c. GAP — misses medium, misses easy (medium ✗ → easy ✗)

| Step | K(n) | p_pred | θ before → after | verdict |
|---|---|---|---|---|
| medium wrong (d=2, start) | 0.800 | 0.500 | 0.000 → **−0.400** | gap |
| easy wrong (d=1, stepped down) | 0.667 | 0.646 | −0.400 → **−0.830** | **gap** |

**Final θ = −0.830 → `gap`, p_mastery = 0.304.**
Worked second row: gap = −0.400 − (−1.0) = +0.6 → p_pred = sigmoid(0.6) = 0.646
(even a weakened student is expected to pass an easy question ~65%); surprise =
0 − 0.646 = −0.646; move = 0.667 × (−0.646) = −0.430; θ = −0.400 − 0.430 = −0.830.
Missing the *easy* rung dropped θ more (−0.430) than missing the medium (−0.400),
because easy was the answer they were most expected to get — the most damning miss.

### The full spread

| End state | Path | Final θ | p_mastery | Branch |
|---|---|---|---|---|
| **gap** | medium ✗ → easy ✗ | **−0.830** | 0.30 | this topic's lessons |
| **solid** | medium ✓ → hard ✗ | **+0.164** | 0.54 | known, no remediation |
| **mastered** | medium ✓ → hard ✓ → confirm ✓ | **+1.140** | 0.76 | hard problems |

---

## 6. Why it's built this way (the two safety properties)

1. **Difficulty weighting** (Section 3): a hard-correct teaches a lot, an easy-correct
   almost nothing; an easy-wrong is damning, a hard-wrong is mild. θ tracks *evidence*,
   not raw score.
2. **Confidence decay** (`K(n)`): the same event moves θ ~3× less at n=10 than at n=0.
   Early answers set the estimate fast; once well-probed, a single lucky/careless MCQ
   cannot flip a settled verdict. This is the core defense against single-question
   noise, backed up by the ladder's asymmetric confirm for skip-granting answers.
