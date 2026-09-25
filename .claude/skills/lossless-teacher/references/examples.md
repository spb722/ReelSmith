# Examples Guide

Use this reference when a worked example would make an explanation easier to understand.

The purpose of these examples is to demonstrate the expected teaching style.

Do not copy them mechanically.

Adapt the same structure to the user's actual question.

---

# Example 1 — Simple Technical Explanation

## Question

What is KV cache?

## Good explanation pattern

Start with the problem.

An LLM generates text one token at a time.

Suppose the prompt is:

```text
The capital of France is
```

The model processes those tokens and then generates:

```text
Paris
```

To generate the next token, the model still needs information from the earlier tokens.

Without KV cache, the model would repeatedly calculate attention information for the old tokens.

Conceptually:

```text
Token generation step 1

The
capital
of
France
is

↓ calculate attention information

Generate:
Paris
```

Then the next generation step would otherwise need to revisit much of that previous work.

KV cache avoids repeating that work.

The model stores the previously calculated:

* Keys
* Values

from the attention layers.

That stored information is called the:

**KV cache**

The journey becomes:

```text
Prompt
↓
calculate Keys and Values
↓
store them in KV cache
↓
generate next token
↓
calculate K/V only for the new token
↓
reuse old cached K/V
↓
generate another token
```

So the simple mental model is:

```text
Without cache:
do old work again

With cache:
remember old work and reuse it
```

The important technical detail is that the model is not caching the final answer.

It is caching intermediate attention tensors representing the Keys and Values for previous tokens.

This saves compute during autoregressive inference.

The trade-off is memory.

As the sequence becomes longer:

```text
more tokens
↓
more cached K/V tensors
↓
more GPU memory usage
```

So KV cache usually improves generation efficiency while increasing memory consumption.

---

# Why This Example Is Good

It follows this order:

```text
Problem
↓
Simple idea
↓
Journey
↓
Technical terminology
↓
Actual mechanism
↓
Trade-off
```

It does not begin with a dense definition such as:

```text
KV cache stores projected key and value tensors for all transformer layers.
```

That statement may be technically correct, but it gives a new learner no mental model.

---

# Example 2 — Troubleshooting

## Problem

The user expected Claude Account 3 to be used, but Claude opens as Account 1.

## Good explanation pattern

### What we're trying to do

We want Claude to start using the configuration and credentials belonging to Account 3.

### What you're seeing

Claude starts successfully, but the active account is Account 1.

That is the visible symptom.

### How it should normally work

Suppose the machine has:

```text
~/.claude
→ Account 1

~/.claude-account2
→ Account 2

~/.claude-account3
→ Account 3
```

When Account 3 is selected, the intended journey is:

```text
T3
↓
sets CLAUDE_CONFIG_DIR
↓
~/.claude-account3
↓
Claude reads that directory
↓
Account 3 credentials
↓
Account 3 becomes active
```

### What may actually be happening

If Claude starts without Account 3's config path, the journey may instead be:

```text
Claude starts
↓
no alternate config directory supplied
↓
Claude uses default config
↓
~/.claude
↓
Account 1 credentials
↓
Account 1 becomes active
```

### Where the paths differ

Expected:

```text
~/.claude-account3
```

Actual:

```text
~/.claude
```

That is the important divergence point.

### Likely root cause

If we have not verified the environment yet, do not state this as certain.

Say:

The most likely cause is that `CLAUDE_CONFIG_DIR` is not pointing to Account 3 when Claude starts.

### How to verify the hypothesis

Run:

```bash
echo "$CLAUDE_CONFIG_DIR"
```

This only reads the current value.

It does not change anything.

If the output is:

```text
/Users/yourname/.claude-account3
```

then the config path is probably correct and we should investigate the credentials or another override.

If it is empty or points somewhere else, that supports the config-path hypothesis.

### Why this causes the problem

```text
Wrong/missing config path
↓
Claude reads wrong directory
↓
wrong credentials are loaded
↓
wrong account becomes active
```

### Fix

The fix should target the cause.

For example:

```bash
CLAUDE_CONFIG_DIR=~/.claude-account3 claude
```

### Why the fix works

Before:

```text
Claude
↓
default ~/.claude
↓
Account 1
```

After:

```text
Claude
↓
~/.claude-account3
↓
Account 3
```

The solution works because it changes the exact configuration source that caused the wrong account to be selected.

### Verification

After starting Claude, verify that Account 3 is active.

If Account 1 still appears even though the config directory is correct, that tells us the root cause is probably elsewhere.

The next layer to inspect would be:

```text
credentials stored inside ~/.claude-account3
```

or another authentication override.

---

# Why This Example Is Good

It does not immediately say:

```text
Run this command.
```

Instead it teaches:

```text
Goal
↓
Symptom
↓
Expected path
↓
Actual path
↓
Divergence
↓
Hypothesis
↓
Test
↓
Fix
↓
Why fix works
↓
Verification
```

That allows the user to understand the system rather than memorize a command.

---

# Example 3 — Explaining Code

## Question

What does this function do?

```python
def resolve_candidate(query, candidates):
    scores = embed_and_score(query, candidates)
    best = max(scores, key=scores.get)
    return best
```

## Weak explanation

```text
The function takes a query and candidates, scores them, gets the maximum,
and returns it.
```

This is technically true but not very useful.

## Better explanation

### What is this function trying to accomplish?

Its job is to choose the candidate that best matches the user's query.

For example:

```text
User query:
"total data usage for the last 2 days"

Candidates:
A = daily data usage
B = account balance
C = roaming status
```

The function tries to determine which candidate is the closest match.

### Journey

```text
query + candidates
↓
embed_and_score(...)
↓
score each candidate
↓
find candidate with highest score
↓
return that candidate
```

### Line 1

```python
scores = embed_and_score(query, candidates)
```

This calls another function that compares the query with every candidate.

The result may look conceptually like:

```python
{
    "daily_data_usage": 0.91,
    "account_balance": 0.22,
    "roaming_status": 0.11
}
```

The important idea is:

```text
candidate
→ relevance score
```

A larger score means a stronger match.

### Line 2

```python
best = max(scores, key=scores.get)
```

`max(...)` finds the candidate whose value is the largest.

Given:

```python
{
    "daily_data_usage": 0.91,
    "account_balance": 0.22,
    "roaming_status": 0.11
}
```

the result becomes:

```text
daily_data_usage
```

because:

```text
0.91
```

is the highest score.

### Line 3

```python
return best
```

The function returns the selected candidate to the caller.

### Complete mental model

```text
Natural-language query
↓
compare against candidates
↓
produce similarity/relevance scores
↓
select largest score
↓
return best candidate
```

### Important limitation

This function assumes `scores` contains at least one valid candidate.

If `scores` is empty:

```python
max(scores, key=scores.get)
```

will fail.

So input validation or empty-result handling may be needed depending on the surrounding system.

---

# Why This Example Is Good

It explains:

* the purpose
* input
* output
* execution journey
* intermediate state
* a concrete example
* an edge case

before getting lost in syntax.

---

# Example 4 — LoRA

## Question

What is LoRA rank?

## Good explanation pattern

First establish the problem.

A neural network may contain very large weight matrices.

For example:

```text
4096 × 4096
```

contains:

```text
16,777,216
```

values.

During full fine-tuning, a large number of model weights may need to be updated.

LoRA takes a different approach.

Instead of directly learning a full-size update matrix:

```text
ΔW
```

LoRA represents that update using two much smaller matrices.

Conceptually:

```text
A
↓
small intermediate dimension
↓
B
```

and:

```text
ΔW = B × A
```

The **rank** controls the size of that small intermediate dimension.

Suppose:

```text
Original matrix:
4096 × 4096
```

and:

```text
LoRA rank = 16
```

Then the two LoRA matrices can have shapes such as:

```text
A:
16 × 4096

B:
4096 × 16
```

Number of LoRA parameters:

```text
16 × 4096 = 65,536

4096 × 16 = 65,536

Total = 131,072
```

Compare that with the original matrix:

```text
16,777,216
```

So instead of learning an unrestricted update with millions of independent values, LoRA learns the update through a much smaller low-rank representation.

### What rank changes

A smaller rank means:

```text
fewer trainable parameters
↓
lower memory usage
↓
less expressive adapter
```

A larger rank means:

```text
more trainable parameters
↓
more memory and compute
↓
potentially more capacity to represent the required update
```

Higher rank is not automatically better.

Once the adapter has enough capacity for the task, increasing rank further may provide little benefit.

### What remains unchanged

In typical LoRA fine-tuning:

```text
base model weights
→ frozen

LoRA matrices
→ trained
```

The effective transformation uses the base weight plus the learned LoRA contribution.

Conceptually:

```text
W_effective = W + ΔW
```

where:

```text
ΔW = B × A
```

The exact scaling can also involve LoRA alpha depending on the implementation.

### Mental model

Think of rank as:

```text
How much room does LoRA have to describe the weight update?
```

not:

```text
How big is the model?
```

---

# Example 5 — Formula Explanation

## Question

Explain cosine similarity.

## Good explanation pattern

### What are we trying to calculate?

We want to know whether two vectors point in a similar direction.

This is useful for things such as comparing embeddings.

Suppose:

```text
Vector A represents:
"car"

Vector B represents:
"automobile"
```

If their embedding vectors point in similar directions, cosine similarity will be high.

### Formula

```text
cosine_similarity(A, B) =
(A · B) / (||A|| × ||B||)
```

### Meaning of the symbols

```text
A · B
```

is the dot product.

It measures how strongly the vectors align.

```text
||A||
```

is the length, or magnitude, of vector A.

```text
||B||
```

is the length of vector B.

Dividing by the two lengths removes the effect of their absolute size.

### Numerical example

Take:

```text
A = [1, 0]

B = [1, 1]
```

Dot product:

```text
A · B

= (1 × 1) + (0 × 1)

= 1
```

Magnitude of A:

```text
sqrt(1² + 0²)
= 1
```

Magnitude of B:

```text
sqrt(1² + 1²)
= sqrt(2)
≈ 1.414
```

Therefore:

```text
cosine similarity
= 1 / (1 × 1.414)
≈ 0.707
```

### What does 0.707 mean?

The vectors point somewhat strongly in the same direction, but they are not identical in direction.

Typical interpretation:

```text
1
→ same direction

0
→ perpendicular / no directional similarity

-1
→ opposite direction
```

The exact interpretation depends on the application and embedding model.

---

# Example 6 — Architecture Explanation

## Question

Explain this RAG architecture.

## Good explanation pattern

Do not begin by defining every component separately.

Follow one user request.

### Goal

The system needs to answer a user's question using information from private documents.

### Journey

```text
User asks question
↓
Application receives question
↓
Question is converted into an embedding
↓
Vector database searches for similar document chunks
↓
Relevant chunks are returned
↓
Question + retrieved chunks are placed into the LLM prompt
↓
LLM generates answer
↓
Answer returned to user
```

Now explain each important stage.

### 1. User question

Example:

```text
"What is our refund policy?"
```

### 2. Embedding

The system converts the question into a vector representation.

This allows the system to compare the meaning of the question with the meaning of stored document chunks.

### 3. Retrieval

The vector database searches for chunks whose embeddings are similar to the query embedding.

Possible result:

```text
Chunk 18:
"Customers may request a refund within 30 days..."

Chunk 44:
"Refunds are returned to the original payment method..."
```

### 4. Prompt construction

The application creates something conceptually like:

```text
User question:
What is our refund policy?

Relevant company information:
- Customers may request a refund within 30 days.
- Refunds return to the original payment method.

Answer using the supplied information.
```

### 5. Generation

The LLM receives the question together with retrieved evidence and generates the response.

### Why retrieval is useful

Without retrieval:

```text
LLM
→ relies mainly on information already present in its model context/parameters
```

With RAG:

```text
LLM
→ receives relevant external information at request time
```

### Important distinction

The retrieved documents are generally not permanently learned by the model during this request.

They are supplied as context.

So:

```text
RAG
≠ training the model on the documents
```

That distinction should be made explicit because users often confuse retrieval with fine-tuning.

---

# Example 7 — Explaining a Failure Chain

## Logs

```text
Database authentication failed
Repository initialization failed
Service startup failed
HTTP server terminated
```

## Poor explanation

```text
The HTTP server failed to start.
```

That only describes the final symptom.

## Better explanation

Read the logs from the first meaningful failure forward.

```text
Database authentication failed
        ↓
repository cannot connect to database
        ↓
repository initialization fails
        ↓
application service cannot initialize
        ↓
HTTP server shuts down
```

The most visible error is:

```text
HTTP server terminated
```

but that is downstream.

The earlier database authentication failure is closer to the root cause.

Therefore the first thing to investigate is not the HTTP server.

It is the database authentication configuration.

This demonstrates an important troubleshooting rule:

```text
Last error shown
≠
root cause
```

Look for the first abnormal event that explains the errors that follow it.

---

# Example 8 — Explaining a Command

## Command

```bash
export CLAUDE_CONFIG_DIR="$HOME/.claude-account3"
```

## Good explanation

### What does this do?

It creates or updates an environment variable named:

```text
CLAUDE_CONFIG_DIR
```

inside the current shell.

Its value becomes:

```text
/Users/yourname/.claude-account3
```

assuming that is your home directory.

### Break it apart

```text
export
```

makes the variable available to programs launched from this shell.

```text
CLAUDE_CONFIG_DIR
```

is the variable name Claude looks at for its configuration directory.

```text
$HOME
```

expands to your home directory.

So:

```text
"$HOME/.claude-account3"
```

becomes something such as:

```text
/Users/sachin/.claude-account3
```

### Journey

```text
Shell stores environment variable
↓
Claude starts from this shell
↓
Claude receives CLAUDE_CONFIG_DIR
↓
Claude reads ~/.claude-account3
↓
that configuration is used
```

### Is it permanent?

Usually not.

Running `export` in a terminal normally affects the current shell session and programs launched from it.

Opening a new terminal may not have the same setting unless it has also been added to shell startup configuration.

That temporary-versus-persistent distinction should be explained when it matters.

---

# General Example Rules

Examples should be chosen because they clarify a mechanism.

A good example should usually contain:

```text
Starting state
↓
Action
↓
Intermediate state
↓
Result
```

For troubleshooting:

```text
Expected state
vs
Actual state
```

For formulas:

```text
Formula
↓
real numbers
↓
calculation
↓
meaning
```

For code:

```text
input
↓
execution
↓
intermediate values
↓
output
```

For architecture:

```text
one request
↓
all important components
↓
final response
```

---

# Keep Examples Small

Use the smallest example that demonstrates the mechanism correctly.

Do not introduce five services when two are enough to explain the relationship.

Do not use a 100-dimensional vector when a 2-dimensional vector demonstrates the mathematics.

Do not use a huge codebase example when a small function illustrates the execution flow.

Once the simple example is understood, connect it back to the real system.

---

# Do Not Let the Example Replace the Real Explanation

An example demonstrates the mechanism.

It does not define the mechanism.

Use this order:

```text
Explain concept
↓
show example
↓
connect example back to actual concept
```

After an analogy or simplified example, explain what is actually happening technically.

---

# Match Examples to the User's Context

When the user provides actual:

* filenames
* model names
* directory paths
* commands
* parameter values
* APIs
* architecture components
* logs

prefer using those values in the example.

This makes the explanation easier to connect to the user's real system.

Do not invent details and present them as though they came from the user's environment.

Clearly label hypothetical values when necessary.

---

# Final Example Check

Before using an example, ask:

1. Does this example clarify the actual mechanism?
2. Is it smaller and easier than the real system?
3. Does it preserve technical correctness?
4. Does it demonstrate cause and effect?
5. Is it clear which details are hypothetical?
6. Did I connect the example back to the real system?

If the example adds complexity without improving understanding, omit it.

