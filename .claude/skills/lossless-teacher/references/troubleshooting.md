# Troubleshooting Guide

Use this reference when the user is:

* debugging an error
* troubleshooting unexpected behavior
* asking why something failed
* trying to identify a root cause
* asking how to fix a technical problem
* showing logs, terminal output, stack traces, configuration, or error messages
* asking why a previously working system stopped working

The goal is not only to fix the problem.

The goal is to make the user understand:

1. what should have happened
2. what actually happened
3. where the two paths became different
4. what caused that difference
5. why that caused the visible problem
6. how the proposed solution corrects the cause
7. how to verify the fix

---

# Core Troubleshooting Principle

Do not jump directly from:

```text
Problem
↓
Command to fix it
```

Instead follow:

```text
Problem observed
      ↓
Understand the environment
      ↓
Expected behavior
      ↓
Actual behavior
      ↓
Find the divergence point
      ↓
Identify immediate cause
      ↓
Identify root cause
      ↓
Show why the root cause creates the symptom
      ↓
Apply the smallest appropriate fix
      ↓
Explain why the fix works
      ↓
Verify the result
```

The user should understand the journey before being asked to memorize a fix.

---

# 1. Start With the User's Goal

Before analyzing the failure, establish what the user was originally trying to accomplish.

Explain it simply.

For example:

```text
Goal:
Run Claude using Account 3.
```

or:

```text
Goal:
Start the API server and make it reachable on port 8000.
```

or:

```text
Goal:
Load the model across two GPUs.
```

This prevents the troubleshooting discussion from becoming disconnected from the original objective.

---

# 2. State the Visible Symptom

Clearly separate what the user observes from the underlying cause.

Example:

```text
Observed symptom:
Claude starts successfully, but it is logged into Account 1 instead of Account 3.
```

The symptom is what the user sees.

It is not automatically the root cause.

Do not describe a suspected explanation as the symptom.

Bad:

```text
Your environment variable is wrong.
```

Better:

```text
Claude is opening with the wrong account.
```

Then investigate why.

---

# 3. Explain What Should Normally Happen

Before explaining the problem, show the normal path.

Use a simple journey.

Example:

```text
Command
   ↓
Shell reads environment variables
   ↓
Claude starts
   ↓
Claude reads CLAUDE_CONFIG_DIR
   ↓
Claude loads credentials from that directory
   ↓
Correct account is authenticated
```

Explain each important stage in simple words.

This gives the user a correct reference model.

Without this, the root cause may feel arbitrary.

---

# 4. Trace What Actually Happened

Now reconstruct the real path using the available evidence.

Use chronological order.

Example:

```text
You ran:

claude

   ↓

No custom CLAUDE_CONFIG_DIR was supplied

   ↓

Claude used its default config directory

~/.claude

   ↓

That directory contains Account 1 credentials

   ↓

Claude authenticated as Account 1
```

Do not skip directly from the initial action to the final symptom.

Show the intermediate decisions that caused the final result.

---

# 5. Find the Divergence Point

Compare:

```text
EXPECTED PATH
```

with:

```text
ACTUAL PATH
```

Identify the first meaningful place where they became different.

Example:

```text
EXPECTED

claude
↓
~/.claude-account3
↓
Account 3


ACTUAL

claude
↓
~/.claude
↓
Account 1
```

The divergence happened here:

```text
Expected config directory:
~/.claude-account3

Actual config directory:
~/.claude
```

This divergence point is often the most useful place to begin root-cause analysis.

---

# 6. Separate Symptom, Immediate Cause, and Root Cause

When relevant, explicitly distinguish them.

## Symptom

What the user notices.

Example:

```text
Claude shows Account 1.
```

## Immediate cause

The event directly responsible for the symptom.

Example:

```text
Claude loaded credentials from ~/.claude.
```

## Root cause

The deeper reason the immediate cause happened.

Example:

```text
CLAUDE_CONFIG_DIR was not set to ~/.claude-account3 when Claude started.
```

## Contributing factors

Conditions that made the problem possible or confusing.

Example:

```text
Multiple Claude account directories exist on the same machine.
```

Use this distinction when it improves clarity.

Do not force every problem into exactly four categories when the issue is trivial.

---

# 7. Explain WHY the Root Cause Produced the Symptom

Do not stop after naming the root cause.

Show the cause-and-effect chain.

Example:

```text
No CLAUDE_CONFIG_DIR
        ↓
Claude uses default ~/.claude
        ↓
~/.claude contains Account 1 credentials
        ↓
Claude authenticates using those credentials
        ↓
Account 1 appears
```

The user should be able to answer:

> Why did this specific root cause create this specific visible behavior?

If that connection is not clear, the diagnosis is incomplete.

---

# 8. Show a Concrete Example

After explaining the cause, create a small example that makes the behavior obvious.

Example:

Suppose the machine contains:

```text
~/.claude
    → Account 1

~/.claude-account2
    → Account 2

~/.claude-account3
    → Account 3
```

Running:

```bash
claude
```

can lead to:

```text
claude
↓
default configuration
↓
~/.claude
↓
Account 1
```

Running:

```bash
CLAUDE_CONFIG_DIR=~/.claude-account3 claude
```

changes the journey to:

```text
claude
↓
CLAUDE_CONFIG_DIR
↓
~/.claude-account3
↓
Account 3
```

Examples should clarify the mechanism.

Do not use examples that introduce unnecessary unrelated complexity.

---

# 9. Determine Whether the Root Cause Is Proven

Do not claim certainty without enough evidence.

Distinguish between:

```text
Known fact
Likely explanation
Possible explanation
Unknown
```

If the root cause has not yet been demonstrated, say so.

Example:

```text
Based on the output so far, the most likely cause is that Claude is reading
the default config directory.

We can confirm that by checking the current CLAUDE_CONFIG_DIR value.
```

Then give the smallest useful test.

Avoid:

```text
This definitely happens because X.
```

when X has not been verified.

---

# 10. Prefer Observation Before Modification

Do not immediately change the system when a simple inspection can reveal the cause.

Prefer:

```text
Observe
↓
Form hypothesis
↓
Test hypothesis
↓
Identify cause
↓
Make smallest necessary change
↓
Verify
```

over:

```text
Change setting A
Change setting B
Delete cache
Reinstall package
Restart everything
Hope it works
```

Troubleshooting should be evidence-driven.

---

# 11. Use the Smallest Diagnostic Test

When several causes are possible, choose a test that separates them.

For example:

Possible causes:

```text
Wrong environment variable
Wrong token in the directory
Alias overriding the command
Cached authentication
```

Do not immediately fix all four.

Instead ask:

```text
What value is CLAUDE_CONFIG_DIR currently using?
```

or run an equivalent inspection command.

The result should narrow the search.

Think of troubleshooting as a decision tree.

---

# 12. Explain Commands Before or Immediately After Using Them

When giving a diagnostic command, explain:

* what it checks
* why we are checking it
* whether it changes anything
* what output would support the hypothesis

Example:

```bash
echo "$CLAUDE_CONFIG_DIR"
```

Explain:

```text
This does not change anything.

It only prints the value of CLAUDE_CONFIG_DIR in the current shell.

If it prints:

~/.claude-account3

then Claude should be receiving Account 3's config path.

If it is empty, Claude may fall back to its default configuration.
```

Do not give unexplained terminal commands when the user is learning the system.

---

# 13. Explain the Solution in Simple Terms First

Once the cause is understood, state what needs to change before showing implementation details.

Use:

```text
What we need to change
```

Example:

```text
We need to make Claude read Account 3's configuration directory instead of
the default ~/.claude directory.
```

Only then show the implementation.

Example:

```bash
CLAUDE_CONFIG_DIR=~/.claude-account3 claude
```

This allows the user to understand the purpose of the command before seeing its syntax.

---

# 14. Explain the Important Parts of the Fix

Break down important commands or configuration changes.

Example:

```bash
CLAUDE_CONFIG_DIR=~/.claude-account3 claude
```

Explain:

```text
CLAUDE_CONFIG_DIR=
    tells Claude which config directory to use

~/.claude-account3
    is the directory containing Account 3's configuration

claude
    starts Claude with that environment setting
```

Avoid over-explaining obvious punctuation unless it matters.

Focus on parts that affect behavior.

---

# 15. Explain Why the Solution Works

Always connect the solution directly to the root cause.

Example:

Before:

```text
No explicit config path
↓
Default ~/.claude
↓
Account 1
```

After:

```text
Explicit config path
↓
~/.claude-account3
↓
Account 3
```

Then state the connection:

```text
The issue happened because Claude was reading the wrong configuration
directory.

The solution works because it explicitly changes the configuration directory
Claude reads when it starts.
```

The user should understand why the fix is logical rather than treating it as a magic command.

---

# 16. Explain Whether the Fix Is Temporary or Permanent

This distinction is important for configuration problems.

For example:

```bash
CLAUDE_CONFIG_DIR=~/.claude-account3 claude
```

may affect only that command invocation.

An alias or shell configuration entry may provide persistent behavior.

Explain the difference when relevant.

Use terms such as:

```text
Temporary:
applies only to this command/session.

Persistent:
saved in configuration and applied again later.
```

Do not let the user assume a temporary fix will survive a new terminal or restart.

---

# 17. Give a Verification Step

A fix is not complete until the user knows how to check it.

Use:

```text
Fix
↓
Verification
↓
Expected result
```

Explain:

* what to run
* what output to expect
* what visible behavior should change

Example:

```text
After launching Claude with Account 3's configuration, check which account
Claude reports.

Expected result:

Account 3 should now be active.
```

When possible, use a concrete command or observable result.

---

# 18. Explain What Failure of Verification Means

If the expected result does not appear, explain what this teaches us.

Example:

```text
If CLAUDE_CONFIG_DIR is correct but Claude still shows Account 1, then the
directory selection is probably not the root cause.

The next thing to inspect would be the credentials stored inside
~/.claude-account3 or whether another layer is overriding authentication.
```

Verification is not only a final checkbox.

It also provides new diagnostic evidence.

---

# 19. Troubleshoot One Layer at a Time

When systems contain multiple layers, locate the failure before investigating everything.

For example:

```text
T3
↓
Provider configuration
↓
Shell/environment
↓
Claude CLI
↓
Claude config directory
↓
Credentials
↓
Anthropic service
```

Ask:

```text
Which layer is working?
Which layer is failing?
```

Do not mix all layers together.

A visible problem in the UI may originate several layers below it.

---

# 20. Trace Values Through the System

For configuration and code problems, follow important values.

Example:

```text
User selects Account 3 in T3
        ↓
T3 chooses provider configuration
        ↓
T3 sets CLAUDE_CONFIG_DIR
        ↓
Claude process receives environment variable
        ↓
Claude reads directory
        ↓
Claude loads credentials
```

If the final account is wrong, find where the value first became incorrect.

Use this pattern:

```text
Where was the value created?
↓
Where was it passed?
↓
Where was it transformed?
↓
Where was it consumed?
```

This is especially useful for:

* environment variables
* API keys
* tokens
* request parameters
* paths
* configuration settings
* IDs
* database values

---

# 21. For Code Bugs, Compare Expected and Actual Execution

Do not merely explain the faulty line.

Reconstruct execution.

Example:

```text
EXPECTED

request
↓
validate user
↓
load data
↓
return response


ACTUAL

request
↓
validate user
↓
load data
↓
exception
↓
500 response
```

Then identify why the exception occurred.

The key question is:

> At what exact point did execution stop behaving as intended?

---

# 22. For Logs, Build a Timeline

When the user provides many logs, do not explain each line independently.

Build a sequence.

Example:

```text
10:15:01 — server starts
10:15:02 — database connection begins
10:15:03 — authentication fails
10:15:03 — database client reports connection failure
10:15:04 — API startup aborts
```

Then explain:

```text
The API startup error is downstream.

The earlier authentication failure is closer to the root cause.
```

Separate:

```text
Root-cause log
```

from:

```text
Secondary errors caused by the root problem
```

---

# 23. Distinguish Errors From Consequences

One failure can create several later errors.

Example:

```text
Database connection fails
        ↓
repository cannot initialize
        ↓
service cannot initialize
        ↓
API startup fails
```

The last error may be the most visible but not the most useful.

Look earlier in the chain for the first abnormal event.

Do not automatically treat the final stack-trace line as the root cause.

---

# 24. Explain Why the Issue Appeared Now

When useful, distinguish:

```text
Why can this problem happen?
```

from:

```text
Why did it start happening now?
```

Common triggers include:

* configuration changed
* dependency version changed
* environment variable disappeared
* token expired
* service restarted
* cache was cleared
* machine changed
* shell changed
* path changed
* permissions changed
* application upgraded
* new default behavior was introduced
* credentials were replaced
* network environment changed

Do not invent a trigger.

Only identify it when evidence supports it.

---

# 25. Avoid Random Fix Lists

Do not respond with:

```text
Try A.
If that fails try B.
Then C.
Then reinstall D.
Then restart everything.
```

unless the evidence genuinely requires a broad recovery sequence.

Prefer:

```text
Current evidence points to A.

Let's verify A using this test.

If the result is X, do this.

If the result is Y, investigate B.
```

This helps the user learn diagnostic reasoning.

---

# 26. Avoid Destructive Actions Until Necessary

Do not recommend actions such as:

```text
rm -rf
delete config
clear all caches
reset application
drop database
reinstall everything
```

as early troubleshooting steps.

First inspect the relevant state.

If destructive action is genuinely necessary:

1. explain what will be removed
2. explain why removal is necessary
3. explain whether recovery is possible
4. preserve important data when appropriate
5. give the exact command only after the consequence is clear

---

# 27. Do Not Hide Uncertainty

If several explanations remain possible, say so.

Example:

```text
We have confirmed that T3 is sending the correct config directory.

That rules out the provider-path problem.

The remaining likely causes are now:

1. the credentials inside that directory
2. an authentication override
```

This is better than pretending the diagnosis is complete.

---

# 28. Prefer Root-Cause Fixes Over Symptom Workarounds

Distinguish:

```text
Workaround
```

from:

```text
Root-cause fix
```

Example:

Restarting a service may temporarily remove a symptom.

Correcting an invalid configuration may fix the root cause.

If giving a workaround, label it clearly and explain its limitation.

Do not present a workaround as though it permanently fixes the underlying cause.

---

# 29. Preserve the User's Working State

When proposing changes, avoid unnecessarily breaking things that already work.

Prefer the smallest targeted change.

For example:

```text
Change one provider configuration
```

is usually safer than:

```text
Delete all Claude configuration and recreate every account
```

Explain why a narrow change is sufficient.

---

# 30. Use Before-and-After Explanations

For configuration problems, a before/after diagram is often the clearest explanation.

Example:

```text
BEFORE

T3
↓
Claude
↓
default config
↓
Account 1
```

```text
AFTER

T3
↓
CLAUDE_CONFIG_DIR=~/.claude-account3
↓
Claude
↓
Account 3 config
↓
Account 3
```

Use this whenever it makes the fix easier to understand.

---

# Preferred Troubleshooting Response Structure

For substantial troubleshooting problems, normally organize the explanation approximately like this:

## What you're trying to do

Briefly state the user's intended outcome.

## What you're seeing

Describe the symptom.

## How it should normally work

Show the expected journey.

## What is actually happening

Trace the observed journey.

## Where it goes wrong

Identify the divergence point.

## Root cause

Explain the underlying cause.

If the root cause is not proven, label it as a hypothesis and explain how to verify it.

## Why that causes the problem

Show the causal chain.

## Simple example

Use a concrete example to make the mechanism obvious.

## How to fix it

Give the smallest safe solution.

## Why the fix works

Connect it back to the root cause.

## How to verify

Show what confirms success.

## If it still doesn't work

Give the next evidence-based diagnostic step.

Do not mechanically include every heading for tiny issues.

Keep the reasoning order even when the response is shorter.

---

# Final Troubleshooting Check

Before finishing, verify that the explanation answers these questions:

1. What was the user trying to do?
2. What symptom did they observe?
3. What should normally happen?
4. What actually happened?
5. Where did expected and actual behavior diverge?
6. What is the immediate cause?
7. What is the root cause?
8. What evidence supports that diagnosis?
9. Why does that root cause produce the visible symptom?
10. What exactly does the solution change?
11. Why does that change fix the root cause?
12. How can the user verify success?
13. If verification fails, what does that tell us?

If any important link in this chain is missing, improve the explanation before finishing.

