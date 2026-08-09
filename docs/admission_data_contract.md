# Kazakhstan Admissions Data Contract

## 1. Purpose and scope

This contract defines how the project represents Kazakhstan higher-education
admission thresholds and the evidence supporting them. It covers legal admission
minimums, university-specific admission minimums, historical grant competition
results, educational-program identities, applicant categories, admission routes,
and source evidence.

The contract does not predict or guarantee admission. A threshold establishes
eligibility only for its stated context. A historical grant cutoff describes a
completed competition and must not be presented as a guaranteed result for a
future competition.

Every producer and consumer of admissions data must follow this contract,
including Django models, importers, web harvesting, extraction prompts,
validation services, serializers, API responses, user-interface labels, and
tests.

## 2. Score types

The generic phrases "profession score" and "passing score" are prohibited in
stored data and internal APIs because they do not identify what a score means.
Every canonical score must have exactly one of the following types.

### 2.1 Legal admission minimum

A legal admission minimum is the government-established minimum required for an
applicant to participate in admission or a grant competition under a specified
set of conditions.

It represents eligibility. It is not a historical grant-winning score, a
university promise, or proof that an applicant will receive a grant. Its required
context includes the applicable year, program group or regulated field,
admission route, applicant background, funding applicability, and source.

API and interface label: **Legal admission minimum**.

### 2.2 University admission minimum

A university admission minimum is a threshold established by a specific
university for admission to one of its programs or program groups. It may be
higher than the legal minimum.

It represents eligibility at that university for its stated year and context. It
is not necessarily the score of the lowest successful grant recipient. A
university admission minimum must always identify the university, applicable
program or program group, year, route, applicant background, funding
applicability, and source.

API and interface label: **University admission minimum**.

### 2.3 Historical grant cutoff

A historical grant cutoff is the lowest verified score recorded for a successful
grant applicant in a completed competition within one precisely identified
category.

It describes a historical outcome rather than a legal eligibility rule. It must
identify the competition year, university when the competition is
university-specific, program group or program, admission route, applicant
background, quota category, instruction language when applicable, and source.
It must be displayed with its year and must never be described as a guarantee
for a future competition.

API and interface label: **Historical grant cutoff — {year}**.

## 3. Identity hierarchy

Admissions data uses three separate identity levels.

### 3.1 Profession

A profession is a job or occupation, such as software developer or mathematics
teacher. A profession can be related to several educational programs. It does
not directly own a universal UNT threshold.

### 3.2 Educational-program group

An educational-program group is an official admissions grouping used for such
purposes as profile subjects, grant allocation, and competition. Official
threshold information is commonly published at this level. The group has a
current official code and names that may differ between Kazakh, Russian, and
English.

### 3.3 University program

A university program is a particular course of study offered by a particular
university. It belongs to an educational-program group where that relationship
is established by an authoritative source. A university program may also have
its own institutional code.

The following identities are not interchangeable:

> Occupation code != educational-program-group code != university program code

Legacy specialty codes such as `5B...` must be stored as typed legacy aliases
with their applicable classification version or period. They must not be
silently treated as current educational-program-group codes. A mapping from a
legacy code to a current identity requires an authoritative mapping source; the
system must not infer it from similar names.

## 4. Required dimensions of a threshold claim

An absent dimension means **unknown**, not a convenient default. Values such as
"general competition," "standard route," and "all languages" may be recorded
only when supported by the source or established by an explicit, documented
rule. Candidate claims with unresolved mandatory context must not become
canonical.

| Dimension | Requirement and reason | Absence policy |
| --- | --- | --- |
| `year` | The admission or completed competition year to which the score applies. It establishes temporal relevance. | Mandatory for canonical data. It is not inferred from retrieval time. |
| `score_type` | Identifies whether the value is a legal minimum, university minimum, or historical grant cutoff. | Mandatory; an untyped number is not a usable threshold. |
| `score` | The total UNT score stated by the claim. It must preserve the source value without averaging or estimation. | Mandatory for a threshold claim. Unknown is represented by no claim, never by zero. |
| `program_group` | Identifies the official educational-program group or other explicitly regulated grouping to which the claim applies. | Mandatory unless the source establishes a broader national rule; the broader scope must then be represented explicitly. |
| `university` | Identifies the institution whose rule or competition outcome is represented. | Mandatory for university minimums and university-specific grant cutoffs; absent only for genuinely national or non-institutional claims. |
| `admission_route` | Distinguishes standard, shortened related-program, creative, and other explicitly named routes. | Mandatory. Unknown routes require review because they can change the meaning of the score. |
| `funding_type` | States whether the claim applies to grant-funded study, paid study, or explicitly both. | Mandatory; applicability to both must be supported rather than assumed. |
| `applicant_background` | Identifies school graduates, TVET or postsecondary graduates, prior higher-education graduates, or another stated category. | Mandatory because thresholds can differ by previous education. |
| `quota_category` | Identifies general competition or a specific quota category. | Mandatory for historical grant cutoffs. It may be explicitly not applicable for rules that do not distinguish quotas. |
| `instruction_language` | Identifies Kazakh, Russian, English, or explicitly language-independent applicability. | Mandatory when results are separated by language; otherwise the source must support language-independent scope. |
| `source_url` | The exact page, file, or document supporting the claim. | Mandatory. A search-result URL is insufficient when the evidence is in an attached document. |
| `evidence` | A short excerpt, table row, or structured location that supports this exact value and context. | Mandatory for harvested and imported candidate claims; canonical promotion requires reviewable evidence. |
| `source_publication_date` | The publication or effective date of the supporting material when available. | May be unknown when the source provides no date, but this reduces freshness certainty and must be visible. |
| `retrieved_at` | The timestamp at which the system obtained the source. It supports audit and refresh operations. | Mandatory for automated ingestion. It never substitutes for `year` or publication date. |

Additional provenance should preserve the source title, publisher, document page
or table location, original language, and a stable content fingerprint when
available. These values make later verification and change detection possible.

## 5. Source-of-truth ownership

`apps.careers` owns the canonical admissions data consumed by the application.
Only verified program identities and verified threshold claims in this domain
may drive career recommendations, university listings, or student-facing score
comparisons.

`web_harvester` is an ingestion and staging subsystem. It owns retrieval
attempts, candidate claims, raw source references, extracted evidence, and
harvest diagnostics. A harvested value is not canonical merely because
extraction succeeded or its URL belongs to an official domain.

Spreadsheet and other bulk imports are additional ingestion paths. They must
produce evidence-bearing candidate claims or pass the same validation and
verification boundary before updating canonical careers data.

The API reads canonical data from `apps.careers`; it does not choose between
independent score databases at request time. `web_harvester.Profession` and
`careers.GrantThreshold` remain legacy structures until a later controlled,
reversible migration. They must not be deleted or reinterpreted in place before
their data has been classified and backfilled.

## 6. Invariants

1. No score becomes canonical without a score type.
2. No score becomes canonical without an applicable year.
3. No score becomes canonical without an exact source and reviewable evidence.
4. A shortened-related-program score cannot be used for a standard
   school-graduate route.
5. A legal admission minimum cannot be presented as a historical grant cutoff.
6. A historical grant cutoff cannot be presented as a guarantee or future
   threshold.
7. Missing scores are represented as unknown, never as zero.
8. Retrieving a source today does not make the source data current.
9. An official domain increases source authority but does not establish that a
   claim is relevant to the requested program, route, applicant, or year.
10. Conflicting credible claims are preserved for reconciliation; they are not
    averaged and an LLM must not silently choose between them.
11. A source must support the specific score and its context, not merely mention
    the profession, program, or university.
12. Updating subjects, aliases, or university offerings must not erase an
    existing verified threshold.
13. Candidate claims and canonical claims have distinct lifecycle states; an
    extraction success is not verification.
14. The original source value and context are immutable audit information. A
    correction creates a traceable superseding decision rather than rewriting
    what the source said.
15. Recommendations must compare a student only with claims matching the
    selected program identity and applicable admission context.

## 7. Acceptance scenarios

### 7.1 Standard technical admission

A government source states a standard legal admission minimum of 50, while a
university's completed grant competition has a historical cutoff of 105.

The system stores two separate claims. The first is a legal admission minimum
with national scope. The second is a historical grant cutoff with its
university, year, competition category, and other required dimensions. Neither
value overwrites the other.

### 7.2 Shortened technical program

A source states a threshold of 25 for a TVET graduate entering a related
shortened program.

The value is valid only with the shortened-related-program route and TVET or
postsecondary applicant background. It is excluded when answering a standard
school-graduate query.

### 7.3 Different universities

Two universities publish different historical cutoffs for the same
educational-program group.

Both claims are preserved with their respective universities and contexts. The
system creates neither an average nor a universal profession score.

### 7.4 Missing current-year result

Only an older historical cutoff is available.

The API may return it with its actual year and a stale-data indication. It must
not label the value as current, silently copy it into a newer year, or omit its
age.

### 7.5 Conflicting sources

Two credible sources state different values for apparently identical context.

Both candidate claims and their evidence are retained. The record is flagged
for deterministic reconciliation or manual review. Neither an average nor an
LLM-selected winner becomes canonical without an auditable rule.

### 7.6 Partial harvesting

A retrieval attempt finds profile subjects and universities but no score.

Those facts may progress through their own validation paths, while the score
remains unknown. Score-specific retrieval continues. Updating the partial facts
does not erase an existing verified threshold.

### 7.7 One source contains several admission categories

An official document lists 50 for a standard route and 25 for a related
shortened route.

The extractor produces two candidate claims with separate evidence and
contexts. It must not select the smaller value, the larger value, or one generic
score for the program.

### 7.8 A profession maps to several program groups

A profession can be reached through more than one educational-program group.

The mapping is stored separately with its own authority and provenance. A user
query may return several educational routes, but their thresholds remain
attached to their actual groups and are not collapsed into one profession
score.

## 8. Vocabulary

| Term | Meaning |
| --- | --- |
| **Legal admission minimum** | A government-established eligibility threshold for stated conditions. |
| **University admission minimum** | An institution-established eligibility threshold for a stated program and year. |
| **Historical grant cutoff** | The lowest verified successful score in a specified completed grant competition. |
| **Educational-program group** | An official admissions grouping used for program classification and competition. |
| **Candidate claim** | An evidence-bearing value produced by an ingestion path but not yet accepted as canonical. |
| **Verified claim** | A candidate claim that passed the required identity, context, evidence, and reconciliation checks. |
| **Admission route** | The route under which the applicant enters, such as standard or shortened related program. |
| **Applicant background** | The applicant's relevant prior education category. |
| **Canonical data** | Verified admissions data owned by `apps.careers` and safe for application use. |
| **Stale data** | A valid historical claim that is older than the freshness policy for the requested use. |

Internal models, APIs, logs, and interfaces must not use "profession score,"
"passing score," or "current score" without a precise type and year. Confidence
must describe the verification state of a claim and its evidence; it must not be
derived solely from domain membership.

## 9. Implementation gate

Future model, ingestion, validation, and API work must satisfy the following
questions for every canonical threshold:

1. What type of score is it?
2. Which program or program group owns the claim?
3. Which applicant background can use it?
4. Which admission route and funding type does it cover?
5. Is it eligibility or a completed competition outcome?
6. Which year applies?
7. Which exact source and evidence support it?
8. Has it passed the canonical verification boundary owned by `apps.careers`?

If any mandatory answer is unknown, the value remains a candidate claim or an
explicitly missing fact. It must not drive student-facing recommendations.
