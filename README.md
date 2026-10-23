# draw.io natural-language MCP agent

Create and edit continuously draw.io diagrams by chatting with an AI agent.

The agent is an MCP client.
It is designed to talk to a **local** MCP server that owns the diagram state, so every message builds on the current diagram.

```text
 you> draw the architecture of my NIS2 checker ...
 you> put the specialist agents inside an "Analysis" boundary
 you> make the evidence store a database and colour it green
 you> undo
 you> switch to top-to-bottom
```

## How it works

```text
┌──────────────┐  chat   ┌───────────────────────┐  MCP (stdio or HTTP)  ┌──────────────────────────┐
│  You (REPL)  │ ──────▶ │  agent.py             │ ────────────────────▶ │  server.py (FastMCP)      │
└──────────────┘         │  LLM tool-calling loop │ ◀──────────────────── │  DiagramService           │
                         │  + conversation memory │   tool results        │  ├─ <name>.json   (state) │
                         │  + current state       │                       │  ├─ <name>.history.json   │
                         └───────────────────────┘                        │  └─ <name>.drawio (output)│
                                                                          └──────────────────────────┘
```

* **The state is a structured model**, not XML: nodes, edges and groups (boundaries/layers). The LLM edits it with small tools (`add_nodes`, `update_nodes`, `remove_edges` and so on). It never writes XML.
* **Every change** is validated. The `.drawio` file is then re-rendered, and an undo snapshot is stored (up to 50 steps).
* **Each turn**, the agent gives the model the active diagram's current content. This lets you refer to things like "the API" or "that arrow".
* **Errors**, such as an edge pointing to a node that doesn't exist, go back to the model. It then corrects the call and retries by itself.
* **Automatic layout** is layered, and groups are laid out as non-overlapping blocks. Imported `.drawio` files keep their original positions (`manual` mode) until you ask to rearrange them.
* **Everything runs locally.** The only network call goes to the LLM endpoint you configure, which can be Azure OpenAI, OpenAI, or a local OpenAI-compatible server. Currently, no public draw.io service is contacted.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # Windows: Copy-Item .env.example .env
```

Edit `.env`. For Azure OpenAI:

```env
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=YOUR_DEPLOYMENT_NAME   # a model that supports tool calling (e.g. gpt-4.1, gpt-4o)
AZURE_USE_ENTRA_ID=false
AZURE_OPENAI_API_KEY=...
```

To use Entra ID instead of a key, set `AZURE_USE_ENTRA_ID=true` and sign in with `az login`.
If your model rejects `temperature`, which some reasoning models do, set `LLM_TEMPERATURE=` to an empty value.

## Run

```bash
python -m drawio_agent.agent
```

The agent starts the MCP server automatically as a subprocess over stdio. To start with an existing diagram:

```bash
python -m drawio_agent.agent --diagram nis2_architecture
```

### Example session

```text
[no diagram] you> Create an architecture diagram called nis2_arch: a user uses a web app, which calls an
                  orchestrator agent. The orchestrator delegates to a code agent, a documentation agent and an
                  infrastructure agent. All three write findings to an evidence store. A report generator reads
                  the evidence store.
   - create_diagram {"title": "NIS2 compliance checker"}
   - add_nodes {"nodes": [...]}
   - add_edges {"edges": [...]}
agent> Created 'nis2_arch' with 8 components and 8 connections.

[nis2_arch] you> group the three specialist agents in a boundary called "Analysis layer"
[nis2_arch] you> add a NIS2 control mapper between the evidence store and the report generator
[nis2_arch] you> make the arrows from the agents dashed and label them "findings (JSON)"
[nis2_arch] you> /undo
[nis2_arch] you> /view
```

### Commands

| Command | Effect |
|---|---|
| `/new <name> [title]` | Create a diagram and make it active |
| `/open <name>` | Switch to another diagram |
| `/import <path> [name]` | Import an existing `.drawio` file and continue editing it |
| `/list` | List the diagrams in the workspace |
| `/show` | Print the active diagram's model |
| `/undo` | Revert the last change |
| `/layout` | Rearrange the diagram automatically |
| `/view` | Open the `.drawio` file in the default application |
| `/reset` | Clear the conversation memory; diagrams are kept |
| `/exit` | Quit |

## Viewing the diagram while you chat

The output is a standard, uncompressed `diagrams/<name>.drawio` file. Any draw.io editor can open it.

* **VS Code + "Draw.io Integration" extension**: keep the file open next to the terminal. The extension normally reloads it when the file changes on disk, as long as the editor has no unsaved changes.
* **draw.io Desktop**: use `/view`. draw.io Desktop does not watch the file for changes, so reopen it after edits.

If you change a diagram by hand in draw.io, use `/import diagrams/<name>.drawio <name>_v2` to continue from your manual version. Imported diagrams keep your positions and styles.

## Using the MCP server from other clients

The MCP server works without the agent too. You can use it from VS Code GitHub Copilot (agent mode), Claude Desktop or the MCP Inspector, for example.

* **stdio**: `python -m drawio_agent.server`. A ready-made `.vscode/mcp.json` is included. You can for instance connect Github Copilot sessions to it so it can be used as a provider of utilities to edit drawio files.
* **HTTP**: `python -m drawio_agent.server --transport streamable-http --port 8000`, with the endpoint `http://127.0.0.1:8000/mcp`. To point the agent at it, set `MCP_SERVER_URL=http://127.0.0.1:8000/mcp`.

### Tools

`list_diagrams`, `get_diagram`, `create_diagram`, `import_drawio`, `add_nodes`, `update_nodes`, `remove_nodes`, `add_edges`, `update_edges`, `remove_edges`, `upsert_groups`, `remove_groups`, `set_diagram_properties`, `relayout`, `replace_diagram`, `undo`.

Node kinds: `actor, service, agent, database, storage, external, process, decision, document, queue, note`.

## Security notes

* The server reads and writes only inside `DRAWIO_WORKSPACE`. Diagram names are validated, and writes are atomic.
* `import_drawio` only reads `.drawio`/`.xml` files from the workspace, the current directory, or folders listed in `DRAWIO_IMPORT_DIRS`.
* The HTTP transport binds to `127.0.0.1` by default and has **no authentication**. Do not expose it on a network unless an authenticated gateway sits in front of it.
* Diagram content is sent to the LLM endpoint you configure. To keep it inside your tenant, use your organisation's Azure OpenAI resource. To keep it on your machine, use a local OpenAI-compatible model.

## Tests

```bash
pytest -q
```

The tests cover the diagram service (editing, cascading deletes, undo, validation errors, overlap-free layout, and importing compressed and uncompressed files). They also cover a multi-turn conversation that uses a scripted LLM, including self-correction after a tool error.

## Project layout

```text
drawio_agent/
  agent.py      interactive MCP client + LLM tool loop + REPL commands
  server.py     MCP server (FastMCP) exposing the diagram tools
  service.py    stateful operations, validation, undo, persistence
  models.py     diagram model and tool input schemas
  layout.py     automatic layered layout with group blocks
  render.py     model -> draw.io XML
  importer.py   .drawio (compressed/uncompressed) -> model
  llm.py        Azure OpenAI / OpenAI client factory
  config.py     .env configuration
tests/
```
