# draw.io natural-language MCP agent

Create and continuously edit draw.io diagrams by chatting with an AI agent.
The agent is an MCP client that talks to a **local** MCP server. The server owns the diagram state, so every message builds on the current diagram.

For instance, you can perform an interaction like the following

```bash
python -m drawio_agent.agent
```

```text
 you> draw the architecture of my NIS2 checker ...
 you> put the specialist agents inside an "Analysis" boundary
 you> make the evidence store a database and colour it green
 you> undo
 you> switch to top-to-bottom
```

The agent can also work in a specified **notation**.
Currently supported notations:
  - ArchiMate 3.2
  - UML 2.5
  - Azure cloud architecture
It then uses only the shapes, icons and relationship types pertaining to that notation. These come from a **shape catalogue** stemming from the following sources:
  - draw.io's own libraries
  - draw.io library files
  - your existing diagrams

```bash
python -m drawio_agent.agent --notation archimate
python -m drawio_agent.agent --notation uml
python -m drawio_agent.agent --notation azure
```

## How it works

```text
┌──────────────┐  chat   ┌────────────────────────┐  MCP (stdio or HTTP)  ┌───────────────────────────────┐
│  You (REPL)  │ ──────▶ │  agent                 │ ────────────────────▶ │  server.py (FastMCP)          │
└──────────────┘         │  LLM tool-calling loop │ ◀──────────────────── │  DiagramService               │
                         │  + conversation memory │   tool results        │  ├─ ShapeCatalog  (shapes)    │
                         │  + current state       │                       │  ├─ NotationRegistry (rules)  │
                         │  + active notation     │                       │  ├─ <name>.json   (state)     │
                         └────────────────────────┘                       │  ├─ <name>.history.json       │
                                                                          │  └─ <name>.drawio (output)    │
                                                                          └───────────────────────────────┘
```

- **The state is a structured model**, not XML: nodes, edges and groups (boundaries, layers, nested containers). The LLM edits it with small tools (`add_nodes`, `update_nodes`, `remove_edges` and so on) and never writes XML.
- **Every change** is validated. The `.drawio` file is then re-rendered, and an undo snapshot is stored (up to 50 steps).
- **Each turn**, the agent gives the model the active diagram's current content. This lets you refer to things like "the API" or "that arrow".
- **Errors** go back to the model, which corrects the call and retries. Examples: an edge pointing to a missing node, an unknown shape key, or a relationship that doesn't exist in the notation. Unknown shape keys come back with "did you mean" suggestions.
- **Automatic layout** is layered. Groups, including nested groups, are laid out as non-overlapping blocks. Shapes keep their catalogue size, and icons with labels underneath get room for the label. Imported `.drawio` files keep their original positions (`manual` mode) until you ask to rearrange them.
- **Everything runs locally.** At runtime, the only network call goes to the LLM endpoint you configure. The catalogue builder can optionally download draw.io's library definitions once, at build time.

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
If your model rejects `temperature`, as some reasoning models do, leave `LLM_TEMPERATURE` empty.

Optionally, build the full shape catalogue (see [Shape catalogue](#shape-catalogue)):

```bash
python build_catalog.py --download archimate3 azure2 uml25
```

Without this step the agent uses the bundled seed shapes. There are about 140 of them, covering the common ArchiMate, UML and Azure elements.
Local bundled seed shapes can be added editing drawio_agent/catalog/seeds/*.json catalog files.

## Run

```bash
python -m drawio_agent.agent [--diagram NAME] [--notation archimate|uml|azure|none]
```

The agent starts the MCP server automatically as a subprocess over stdio.

### Example sessions

Generic mode:

```text
[no diagram] you> Create an architecture diagram called nis2_arch: a user uses a web app, which calls an
                  orchestrator agent. The orchestrator delegates to a code agent, a documentation agent and an
                  infrastructure agent. All three write findings to an evidence store.
   - create_diagram {"title": "NIS2 compliance checker"}
   - add_nodes {"nodes": [...]}
   - add_edges {"edges": [...]}
agent> Created 'nis2_arch' with 7 components and 7 connections.
```

ArchiMate:

```text
$ python -m drawio_agent.agent --notation archimate
Notation: ArchiMate 3.2.
[no diagram|archimate] you> Create claims_ea: the Claims Handling business process is served by the Claims
                            application service, realized by the ClaimsApp component, which writes the Claim
                            data object and runs on a Kubernetes cluster node.
   - create_diagram {"diagram": "claims_ea", "notation": "archimate"}
   - add_nodes {"nodes": [{"id": "handle_claims", "shape": "archimate3.business_process", ...
   - add_edges {"edges": [{"source": "claims_svc", "target": "handle_claims", "relation": "serving"}, ...
[claims_ea|archimate] you> put the elements in Business, Application and Technology layers
[claims_ea|archimate] you> NIS2 is a driver for a requirement "Incident reporting within 24h" that ClaimsApp realizes
```

Azure:

```text
$ python -m drawio_agent.agent --notation azure
[no diagram|azure] you> web app behind Front Door, App Service in a VNet-integrated subnet, Azure SQL via
                        private endpoint, secrets in Key Vault, everything in West Europe
   - search_shapes {"query": "front door"}
   - upsert_groups {"groups": [{"id": "weu", "shape": "azure_boundaries.region_boundary"}, {"id": "vnet", "parent": "weu", ...
```

UML:

```text
$ python -m drawio_agent.agent --notation uml
[no diagram|uml] you> class diagram: Order (id, total, checkout()) composed of OrderLines; Customer places Orders
```

### Commands

| Command | Effect |
|---|---|
| `/new <name> [title]` | Create a diagram (with the active notation) and make it active |
| `/open <name>` | Switch to another diagram |
| `/import <path> [name]` | Import an existing `.drawio` file and continue editing it |
| `/list` | List the diagrams in the workspace, with their notation |
| `/show` | Print the active diagram's model |
| `/undo` | Revert the last change |
| `/layout` | Rearrange the diagram automatically |
| `/view` | Open the `.drawio` file in the default application |
| `/notation [name]` | Show or change the notation: `archimate`, `uml`, `azure`, `none` |
| `/notations` | List the available notations |
| `/shapes <query>` | Search the shape catalogue within the active notation |
| `/libraries` | List the shape libraries and their sizes |
| `/reset` | Clear the conversation memory; diagrams are kept |
| `/exit` | Quit |

## Notations

A notation is a property of the diagram, stored in `<name>.json`, plus the agent's working mode.

| Situation | Behaviour |
|---|---|
| No `--notation`, generic diagram | v3 behaviour: same system prompt and same tools; nodes use generic `kind`s |
| No `--notation`, diagram has a notation (e.g. created or imported earlier) | The agent adopts the diagram's notation |
| `--notation X` (or `/notation X`) | Forced. New diagrams get notation X, opened diagrams are switched to X, and the agent can't change it |
| `--notation none` | Forced generic mode |

When a notation is active:

- **Prompt.** The system prompt gets a notation guide. It combines the hand-written rules in `<notation>.md` with generated facts: allowed libraries, relationship types and their meaning, container shapes, and the full shape list when the notation has few shapes (ArchiMate, UML). For large libraries (Azure), the agent is told to use `search_shapes` instead.
- **Tools.** The tools `search_shapes`, `list_shape_libraries`, `list_notations` and `get_notation_guide` are offered to the model. In generic mode they are hidden.
- **Arguments.** The agent forces the notation into shape searches and into `create_diagram` and `replace_diagram`.
- **Server checks.** The server enforces the rules independently of the agent. Shapes must exist and belong to the notation's libraries. Nodes need a `shape`, and edges need a `relation` when the notation requires one. Relationship endpoint constraints are checked (e.g. UML include/extend only between use cases).
- **Legacy content.** Rules apply to the elements being added or changed. Content that existed before the notation was set stays editable, and `get_diagram` reports it under `notation_issues`. You can then ask, for example, "convert the remaining boxes to ArchiMate elements".

### Built-in notations

| Name (aliases) | Libraries | Relations | Defaults |
|---|---|---|---|
| `archimate` (`archimate3`, `archimate32`) | `archimate3`, `archimate_containers` | composition, aggregation, assignment, realization, serving, access (+ read/write/readwrite), influence, triggering, flow, specialization, association (+ directed). Required | top-to-bottom; groups drawn as ArchiMate Grouping; layer bands available |
| `uml` (`uml2`, `uml25`) | `uml`, `uml25` | association, directed_association, aggregation, composition, generalization, realization, dependency, usage, include, extend, communication, control_flow, object_flow, transition, deployment, manifestation, note_link. Required | top-to-bottom; groups drawn as packages |
| `azure` (`azure2`) | `azure2`, `azure_boundaries` | flow (default), async_flow, dependency, private_connection, network_link, replication, monitoring | left-to-right; region, subscription, resource group, VNet, subnet, availability zone and on-premises boundaries, nestable |

**UML class-like shapes** use *compartments*. Separate the name, attributes and operations with a line containing only `--`, for example `Order\n--\n- id: UUID\n--\n+ checkout(): Invoice`. The box grows with its content.

### Customising and adding notations

Each notation is two files in `drawio_agent/notations/definitions/`:

- **`<name>.json`**: the structure. Includes `libraries`, `relations` (description, draw.io edge `style`, optional `default_label` and `source_categories`/`target_categories`), `require_shapes`, `require_relations`, `default_relation`, `default_direction`, `default_group_shape`, `inline_shape_limit` and `aliases`.
- **`<name>.md`**: the guidance injected into the system prompt. **Edit this file to give the agent your rules, constraints and domain knowledge**, for example your company's ArchiMate viewpoints or Azure naming conventions.

To add a notation (e.g. C4) or override a built-in one without touching the package, put `<name>.json` and `<name>.md` in a folder and set `NOTATIONS_DIR` to it. A file with the same name replaces the built-in one.

## Shape catalogue

Nodes and groups reference shapes by **key**, e.g. `archimate3.application_component`, `azure2.key_vaults`, `uml.class` or `azure_boundaries.subnet_boundary`. The catalogue maps each key to its draw.io style, default size, category and search keywords.

At runtime, the server loads the bundled **seed** shapes plus the catalogue file `SHAPE_CATALOG` (default `./catalog/shapes.json`). Entries in the file override seeds with the same key.

### Building the catalogue

```bash
python build_catalog.py --help
```

| Source | Option | What you get |
|---|---|---|
| draw.io's own library definitions (`Sidebar-*.js`) | `--download archimate3 azure2 uml25` (cached in `catalog/sources/`), or `--drawio-repo <clone of jgraph/drawio> --sidebars ArchiMate3 Azure2 UML25`, or `--sidebar FILE[=LIB]` | The exact shapes of the draw.io palettes: all ArchiMate 3 elements, hundreds of Azure icons, UML 2.5 shapes. Also available: `c4`, `bpmn`, `kubernetes` |
| draw.io library files (`.xml`, *File > Open Library*) | `--library FILE[=LIB]` | Any icon pack or custom library, including image libraries |
| Existing diagrams | `--harvest FOLDER_OR_FILE[=LIB]` | Every distinct shape style you actually use. Styles matching known shapes only add your labels as search keywords |

Examples:

```bash
# online, once (the only network access in the project, at build time)
python build_catalog.py --download archimate3 azure2 uml25

# offline (no internet or behind a proxy): from a local clone of https://github.com/jgraph/drawio
python build_catalog.py --drawio-repo ~/src/drawio --sidebars ArchiMate3 Azure2 UML25

# add your company's icon library to the Azure notation and harvest your ArchiMate conventions
python build_catalog.py --download archimate3 azure2 uml25 \
    --library ~/libs/company-azure.xml=azure2 \
    --harvest ~/architecture/ea-models=archimate3
```

Precedence rules:

1. **Seeds** are the lowest priority.
2. **Sidebar and library sources** replace the seed entries of the same library completely, because draw.io's own definitions are exact.
3. **Harvested** styles that match an existing shape enrich its keywords. New styles become entries of the harvest library.

To make harvested or custom libraries available to a notation, give them a library name the notation already lists (as in `=archimate3` above), or add the name to the notation's `libraries`.

About the seeds: they are a curated, offline fallback. They use draw.io's `mxgraph.archimate3` styles, the `img/lib/azure2/...` icon paths and standard UML shapes. They were written by hand, not extracted from draw.io. A few ArchiMate icon types or Azure icon paths may therefore not match your draw.io version exactly, and draw.io shows a mismatch as a generic box or a missing image. Building the catalogue from draw.io's sidebars (first table row) replaces them with the exact definitions.

The Azure icons reference draw.io's bundled image paths (`img/lib/azure2/...`). draw.io Desktop, app.diagrams.net and the VS Code Draw.io extension display them. Other viewers may not. Icons from `--library` files usually embed the SVG, so they display everywhere.

## Viewing the diagram while you chat

The output is a standard, uncompressed `diagrams/<name>.drawio` file. Any draw.io editor can open it.

- **VS Code with the "Draw.io Integration" extension**: keep the file open next to the terminal. The extension normally reloads it when the file changes on disk, as long as the editor has no unsaved changes.
- **draw.io Desktop**: use `/view`. draw.io Desktop doesn't watch the file for changes, so reopen it after edits.

If you change a diagram by hand in draw.io, run `/import diagrams/<name>.drawio <name>_v2` to continue from your manual version. Imported diagrams keep your positions and styles. Nested containers become nested groups. Shapes that match the catalogue get their shape key, and if most of them belong to one notation, that notation is detected automatically.

## Using the MCP server from other clients

The MCP server also works without the agent, from clients such as VS Code GitHub Copilot (agent mode), Claude Desktop or the MCP Inspector.

- **stdio**: `python -m drawio_agent.server`. A ready-made `.vscode/mcp.json` is included.
- **HTTP**: `python -m drawio_agent.server --transport streamable-http --port 8000`, with the endpoint `http://127.0.0.1:8000/mcp`. To point the agent at it, set `MCP_SERVER_URL=http://127.0.0.1:8000/mcp`.

### Tools

| Area | Tools |
|---|---|
| Diagrams | `list_diagrams`, `get_diagram`, `create_diagram` (`notation`), `import_drawio`, `replace_diagram` (`notation`), `set_diagram_properties` (`title`, `direction`, `layout_mode`, `notation`), `relayout`, `undo` |
| Content | `add_nodes`, `update_nodes`, `remove_nodes` (nodes: `kind` or `shape`); `add_edges`, `update_edges`, `remove_edges` (edges: optional `relation`, endpoints may be nodes or groups); `upsert_groups`, `remove_groups` (groups: optional container `shape` and `parent` for nesting) |
| Shapes and notations | `search_shapes` (`query`, `notation`, `library`, `element_type`, `category`, `limit`), `list_shape_libraries`, `list_notations`, `get_notation_guide` |

Generic node kinds: `actor, service, agent, database, storage, external, process, decision, document, queue, note`.

## Security notes

- The server reads and writes only inside `DRAWIO_WORKSPACE`. Diagram names are validated, and writes are atomic.
- `import_drawio` only reads `.drawio` and `.xml` files from the workspace, the current directory, or the folders listed in `DRAWIO_IMPORT_DIRS`.
- The catalogue builder never executes draw.io's JavaScript. It reads the files with a small static parser and skips anything that isn't a plain string or number expression. Downloads are restricted to fixed `raw.githubusercontent.com/jgraph/drawio` URLs and happen only when you pass `--download`.
- The HTTP transport binds to `127.0.0.1` by default and has **no authentication**. Do not expose it on a network unless an authenticated gateway sits in front of it.
- Diagram content is sent to the LLM endpoint you configure. To keep it inside your tenant, use your organisation's Azure OpenAI resource. To keep it on your machine, use a local OpenAI-compatible model.

## Tests

```bash
pytest -q
```

The tests cover:

- Generic editing, undo, validation errors, layout without overlaps (including nested groups), and import of compressed and uncompressed files.
- Notation rules for ArchiMate, UML and Azure: shapes, relations, categories, the touched-only policy, rendering, compartments and label space.
- The catalogue: search, the sidebar parser on draw.io-structured JavaScript, mxlibrary files, harvesting, builder precedence and the CLI.
- The agent: a byte-identical v3 prompt in generic mode, tool hiding, forced and adopted notations with argument enforcement, a multi-turn conversation with self-correction, and the REPL commands.

## Project layout

```text
build_catalog.py           catalogue builder entry point
catalog/shapes.json        built catalogue (generated; optional)
drawio_agent/
  agent/                   MCP client side
    cli.py                 argument parsing (--diagram, --notation) and start-up
    core.py                ChatAgent: LLM tool loop, memory, active diagram and notation
    policy.py              which tools the LLM sees; argument completion/enforcement
    prompts.py             system prompt composition (generic = v3 prompt)
    repl.py                interactive commands
    mcp_client.py          stdio / streamable-HTTP connection
    schema.py              MCP -> OpenAI tool schema conversion
  server.py                MCP server (FastMCP) exposing the tools
  service.py               DiagramService: the tool operations
  repository.py            persistence, undo history, atomic writes
  validation.py            structural and notation rules
  models.py                diagram model and tool input schemas
  styles.py                style/size/label resolution (kind, catalogue shape, relation)
  layout.py                layered automatic layout with nested groups
  render.py                model -> draw.io XML
  importer.py              .drawio -> model (catalogue shape matching)
  drawio_codec.py          draw.io format helpers (compression, styles)
  catalog/
    catalog.py             ShapeCatalog: lookup, search, suggestions, style matching
    entries.py             ShapeEntry / LibraryInfo
    sources.py             Seed, Sidebar, MxLibrary and DiagramHarvest sources
    jsparse.py             static reader for draw.io Sidebar-*.js files
    builder.py             CatalogBuilder (precedence) and SidebarDownloader
    cli.py                 build_catalog.py options
    seeds/*.json           bundled seed shapes
  notations/
    model.py registry.py guide.py
    definitions/           archimate.json/.md, uml.json/.md, azure.json/.md
  config.py llm.py
tests/
```
