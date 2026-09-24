# Spike 4.1: Claude Agent SDK skills, plugins, Read scoping and hooks

- **Date:** 2026-09-24
- **SDK:** `claude-agent-sdk` 0.2.152, which bundles Claude Code CLI 2.1.259, in the `kayak-video` env.
- **Model:** `haiku`. The behaviour under test is CLI-side and doesn't depend on the model.
- **Total spike spend:** about $0.22.
- **Method:** a throwaway script, kept outside the repo, ran small live `query()` calls against a minimal local test plugin.
  - The plugin is `spike-plugin`, with skills `alpha` and `beta`. `alpha` has a `reference.md` holding token `ZEBRA-42`.
  - The cwd is the repo root. That folder has `.claude/skills/` (bmad-\*, lossless-teacher) and `CLAUDE.md`, so any leak of developer tooling would show up.
  - Base options match the pipeline's: `permission_mode="dontAsk"`, `setting_sources=[]` and `strict_mcp_config=True`.

## Answers

| # | Question | Answer |
|---|---|---|
| (a) | Plugin skills load while `setting_sources=[]` | **Yes** |
| (b) | `skills=[...]` restricts a top-level `query()`; name format | **Yes.** Bare `alpha` and qualified `spike-plugin:alpha` both work. The model and the init message use the qualified form. |
| (c) | `structured_output` survives Agent-tool delegation | **Yes** (informational only) |
| (d) | Concurrent `query()` calls in one process, sharing one SDK MCP server | **Yes**, with no interference |
| (e) | `Read` can be limited to the plugin folder with `allowed_tools` rules under `dontAsk` | **No.** A rule *grants* the plugin folder, but nothing restricts Read *to* it: the cwd and the temp dir stay readable. Fallback: a `PreToolUse` path check. |
| (f) | Init keys listing skills and plugins; zero `memoryFiles` | **Keys:** `skills` and `plugins`. The `skills` list is **unfiltered**: every discovered skill appears, not the `skills=[...]` set. `memoryFiles == []` with `setting_sources=[]`. |
| (g) | `tool_response` shape in `PostToolUse` for an in-process SDK MCP tool | A **list of content blocks**, `[{"type": "text", "text": "<json string>"}]` |

## Evidence

### (a) Plugin skills load with `setting_sources=[]`

The options were `plugins=[{"type": "local", "path": <plugin>}]`, `tools=["Skill", "Read"]` and no `skills` filter. The init `SystemMessage` data contained:

```text
plugins: [{'name': 'spike-plugin', 'path': '.../spike/plugin', 'source': 'spike-plugin@inline', 'version': '0.0.1'}]
skills:  ['deep-research', 'spike-plugin:alpha', 'spike-plugin:beta', 'design-sync', 'dataviz', 'update-config',
          'verify', 'debug', 'code-review', 'simplify', 'batch', 'fewer-permission-prompts', 'doctor', 'loop',
          'schedule', 'claude-api', 'workflow-authoring', 'run', 'run-skill-generator']
```

- The plugin's skills are present, under the names `<plugin>:<skill>`.
- None of the repo's `.claude/skills` (no `bmad-*`, no `lossless-teacher`) appear, and none of the user's skills (`$CLAUDE_CONFIG_DIR/skills`: `hyperframes-*`, `media-use`, …) appear.
- The other names are skills bundled into the CLI.
- No fallback is needed.

### (b) `skills=[...]` restricts the session

**Bare format**, `skills=["alpha"]`. The prompt asked the model to invoke `beta` and then `alpha`:

```text
Skill {'skill': 'beta'}               -> is_error=True  "<tool_use_error>Skill beta is not in this session's skills allowlist</tool_use_error>"
Skill {'skill': 'spike-plugin:alpha'} -> "Launching skill: spike-plugin:alpha"   (answer: ALPHA-7731)
```

**Qualified format**, `skills=["spike-plugin:alpha"]`:

```text
Skill {'skill': 'spike-plugin:beta'}  -> is_error=True  "Skill spike-plugin:beta is not in this session's skills allowlist"
Skill {'skill': 'spike-plugin:alpha'} -> "Launching skill: spike-plugin:alpha"
```

**A bundled skill**, with `skills=["alpha"]`, asked to invoke `debug`. The model didn't try, and said: *"The only skill currently available is: spike-plugin:alpha …"*. So the filter also hides bundled skills from the model's listing.

**Conclusion:** both name formats work. The pipeline uses the qualified `book-reels:<skill>` form, because it is unambiguous against bundled skill names and it is the form the init message reports.

### (c) `structured_output` with subagent delegation

The options were `tools=["Agent"]`, `agents={"helper": AgentDefinition(...)}` and a JSON-schema `output_format`. The model's tool calls were `Agent {'subagent_type': 'helper', ...}` and then `StructuredOutput {'answer': 'PINEAPPLE'}`.

The result was `subtype=success` and `structured_output={'answer': 'PINEAPPLE'}`. So `structured_output` survives delegation.

### (d) Concurrency with a shared module-level SDK MCP server

- **Setup:** one module-level `create_sdk_mcp_server(name="spk", tools=[echo_tag])`. The handler does `await asyncio.sleep(1)`. Three `query()` calls were launched with `asyncio.gather`, each with its own tag, and all three used the same server instance.
- **Wall time:** 7.6 s for all three.
- **Results:** each query got its own answer.

```text
D RED-1   -> {'echo': 'RED-1'}   success
D GREEN-2 -> {'echo': 'GREEN-2'} success
D BLUE-3  -> {'echo': 'BLUE-3'}  success
```

- **Hook calls:** one `PostToolUse` closure was shared by all three queries. It was called exactly 3 times, once per query, each time with that query's own payload.
- **Caveat:** this proves the SDK plumbing only. The pipeline's vendor handlers block the event loop, and that remains Story 4.3's concern.

### (e) Scoping `Read` to the plugin folder

**Test 1: files under the temp dir and in the cwd.** The prompt read three files: the plugin's `reference.md`, a file outside the plugin (under the temp dir), and `AGENTS.md` in the cwd.

| Options | plugin `reference.md` | temp-dir file outside plugin | `AGENTS.md` in cwd |
|---|---|---|---|
| `allowed_tools=["Read(//<plugin>/**)"]` | OK | OK | OK |
| same, with `add_dirs=[plugin]` | OK | OK | OK |
| `allowed_tools=[]`, no rule | OK | OK | OK |

Under `dontAsk`, Read is auto-allowed inside the cwd and inside the OS temp dir. The spike plugin sat under `/private/tmp`, so this test couldn't tell whether a rule was doing anything. It was repeated with a target far outside both.

**Test 2: a target outside the cwd and the temp dir** (`/opt/homebrew/.../claude_agent_sdk/_version.py`).

| Options | Result |
|---|---|
| no rule, no `add_dirs` | ERR: *"Permission to use Read has been denied because Claude Code is running in don't ask mode."* |
| `allowed_tools=["Read(//opt/.../claude_agent_sdk/**)"]` only | OK |
| that rule plus `add_dirs=[dir]` | OK |
| `add_dirs=[dir]` only, no rule | OK |
| `disallowed_tools=["Read(//opt/**)"]` | ERR: *"File is in a directory that is denied by your permission settings."* |

**What this shows:**

- The `Read(//abs/path/**)` allow rule does grant a folder outside the cwd. `add_dirs` isn't needed.
- Allow rules only *add* access. The cwd, which for the pipeline is the project workspace, and the temp dir stay readable whatever the rule.
- Deny rules take precedence over allow rules, so "deny everything except the plugin" can't be written as permission rules.
- **So the answer to (e) is no**, and the story's fallback applies.

**Test 3: the fallback.** A `PreToolUse` hook (`matcher="Read"`) resolves `tool_input["file_path"]` and denies any path that isn't the plugin root or inside it:

```text
plugin reference.md -> OK   "The alpha reference token is ZEBRA-42."
far /opt file       -> ERR  "Read is limited to the skills plugin folder <plugin>"
AGENTS.md (in cwd)  -> ERR  "Read is limited to the skills plugin folder <plugin>"
```

The deny is honoured under `dontAsk`, and the hook's `permissionDecisionReason` is what the model sees.

### (f) Init keys and `memoryFiles`

**Init message keys:**

```text
['agents', 'analytics_disabled', 'apiKeySource', 'capabilities', 'claude_code_version', 'cwd',
 'fast_mode_disabled_reason', 'fast_mode_state', 'mcp_servers', 'memory_paths', 'messaging_socket_path',
 'model', 'output_style', 'permissionMode', 'plugins', 'product_feedback_disabled', 'session_id',
 'skills', 'slash_commands', 'subtype', 'terminal_slash_commands', 'tools', 'type', 'uuid']
```

- **`skills`** is a list of name strings. Plugin skills carry the prefix `<plugin>:`. No per-skill source is given.
- **`plugins`** is a list of `{name, path, source, version}` entries.

**The `skills` list is not filtered.** With `skills=["alpha"]`, the init message still listed `spike-plugin:beta` and every bundled skill (see (a)). The filter acts on the model's listing and on the Skill tool, as (b) showed, not on the init message. So the story's fallback check applies: every listed skill must come from `book-reels`, and none may come from `.claude/skills`. The per-agent restriction is proven by (b).

**Bundled skills.** The CLI bundles skills (`debug`, `simplify`, `dataviz`, …) that would make the literal check "every listed skill is `book-reels:*`" fail on every run. The bundled binary contains the env switch `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS`. With `env={"CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1"}`:

```text
skills:  ['spike-plugin:alpha', 'spike-plugin:beta', 'doctor']
plugins: [{'name': 'spike-plugin', ..., 'source': 'spike-plugin@inline'}]
```

- With the switch set, the listing and the Skill tool still worked (`Skill spike-plugin:alpha -> Launching skill`).
- Only `doctor`, a CLI built-in, remains outside the plugin.

**`memoryFiles`.** These came from `ClaudeSDKClient.get_context_usage()` after one `"Reply OK."` turn, with the cwd at the repo root:

```text
setting_sources=[]          -> memoryFiles: []
setting_sources=["project"] -> memoryFiles: [{"path": "/Users/sachinpb/.claude/CLAUDE.md", "type": "Project", "tokens": 73},
                                             {"path": "/Users/sachinpb/.claude/rules/context7.md", "type": "Project", "tokens": 562},
                                             {"path": "/Users/sachinpb/PycharmProjects/book_reels/CLAUDE.md", "type": "Project", "tokens": 614}]
```

The second run is the control: it proves the probe detects CLAUDE.md when CLAUDE.md is allowed. With `[]`, zero memory files load, which confirms FR3's structural guarantee.

### (g) `PostToolUse` payload for an in-process SDK MCP tool

The handler returned `{"content": [{"type": "text", "text": json.dumps({...})}]}`. The hook received:

```text
keys: ['cwd', 'duration_ms', 'hook_event_name', 'permission_mode', 'prompt_id', 'session_id',
       'tool_input', 'tool_name', 'tool_response', 'tool_use_id', 'transcript_path']
tool_name: 'mcp__spk__echo_tag'
tool_response (type list): [{"type": "text", "text": "{\"echo\": \"RED-1\", \"failure\": {\"reason\": \"demo\", \"code\": \"X\"}}"}]
```

So Story 4.2's failure-contract parser must iterate `tool_response` as a list of content blocks, and `json.loads` the `text` of each `type == "text"` block.

## Paths chosen for Story 4.1

1. **Plugin loading:** `plugins=[{"type": "local", "path": <absolute path built from __file__>}]` with `setting_sources=[]`. Fallback (a) isn't needed.
2. **Skill names:** qualified, `book-reels:<skill>`, in `skills=[...]`.
3. **Init check:** the fallback variant, because the init `skills` list is unfiltered.
   - Every listed skill must be `book-reels:*` or the CLI built-in `doctor`.
   - `plugins` must be exactly `[book-reels]`.
   - Every skill in the agent's own list must be present.
   - Pipeline agents set `env={"CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1"}`, so that bundled skills neither pad the listing nor defeat the check.
4. **Read scoping:** the `Read(//<abs plugin>/**)` rule in `allowed_tools`, without `add_dirs`, because the plugin sits outside the project cwd. On top of it, the fallback `PreToolUse` path-check hook denies `Read` outside the plugin folder. The hook applies to every agent that has no Read of its own today: story, visual, veo and stills. `asset_analyst` keeps its unrestricted bare `Read`, which it needs for the screenshots.
