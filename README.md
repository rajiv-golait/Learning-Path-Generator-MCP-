# Learning Path Generator (MCP)

A [Streamlit](https://streamlit.io/) app that uses **LangGraph**, **Google Gemini**, and **remote MCP servers** (e.g. [Composio](https://composio.dev/) or [Pipedream](https://mcp.pipedream.com/)) to build a **day-wise learning path**: content in **Google Drive** or **Notion**, plus a **YouTube playlist** of curated videos.

## Features

- Sidebar configuration for Gemini, optional Composio API key, and HTTPS MCP URLs (YouTube required; Drive **or** Notion).
- Connects to MCP over **streamable HTTP** or **SSE** (auto-selected; Composio v3 uses streamable HTTP only).
- Sends **`x-api-key`** to Composio MCP endpoints when `COMPOSIO_API_KEY` is set (avoids `401 Unauthorized` on most projects).
- Patches MCP tool schemas so **Gemini** accepts array parameters (and strips keys Gemini ignores).
- **Tool errors** (e.g. YouTube `videoNotFound`) are returned to the model so the agent can retry instead of crashing the run.

## Prerequisites

- **Python 3.10+** (3.11 recommended).
- **Google AI Studio** [API key](https://aistudio.google.com/) for Gemini (`GOOGLE_API_KEY` / “Google API Key” in the app).
- At least one **YouTube** MCP server URL; optionally **Google Drive** or **Notion** MCP URLs from the same kind of host.
- For **Composio** URLs (`*.composio.dev`): a project **API key** starting with `ak_` (see [Authentication](https://v3.docs.composio.dev/reference/authentication)).

On Windows, if `python` is not on your PATH, use the **py** launcher: `py -m venv venv`.

## Quick start

```bash
git clone https://github.com/revanthgopi-nw/mcp-learning-path-demo.git
cd mcp-learning-path-demo
```

Create and activate a virtual environment, then install dependencies:

**Windows (PowerShell)**

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Environment variables (optional)

Create a `.env` file in the project root (do not commit it). Supported variables:

| Variable | Purpose |
|----------|---------|
| `COMPOSIO_API_KEY` | Composio project API key (`ak_…`); sent as `x-api-key` on MCP requests. |
| `COMPOSIO_ORG_API_KEY` | Optional org key; sent as `x-org-api-key` if set. |
| `MCP_TRANSPORT` | Force `streamable_http` or `sse` (default: auto; Composio URLs → streamable HTTP only). |

The app calls `load_dotenv()` and pre-fills the Composio sidebar field from `COMPOSIO_API_KEY` when present.

### MCP URLs

Paste **only the HTTPS MCP endpoint** (the URL your provider shows for the server), for example:

- Composio: `https://backend.composio.dev/v3/mcp/<server-id>/mcp?user_id=<user-id>`
- Pipedream: copy the MCP URL from each app’s configuration page.

Do **not** paste CLI commands such as `npx @composio/mcp@latest setup ...`.

### Run

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501). Enter your **Google API key**, **Composio API key** (if using Composio), MCP URLs, a learning goal, then **Generate Learning Path**.

## Project layout

| File | Role |
|------|------|
| `app.py` | Streamlit UI, sidebar config, progress display. |
| `utils.py` | MCP client, Gemini model, schema patches, `ToolNode` error handling, agent setup. |
| `prompt.py` | System-style instructions for the agent (tool names, Drive/YouTube workflow). |
| `requirements.txt` | Python dependencies. |

## Troubleshooting

| Symptom | Likely cause | What to try |
|---------|----------------|-------------|
| `401 Unauthorized` on Composio MCP | Missing API key on requests | Set `COMPOSIO_API_KEY` or enter the key in the sidebar. |
| `405 Method Not Allowed` (SSE) | Composio v3 is not SSE | Already handled: Composio URLs use streamable HTTP only. |
| `TaskGroup` / nested HTTP errors | Transport or auth mismatch | Check URL, key, and expanded error in Streamlit **Full error** section. |
| Gemini `INVALID_ARGUMENT` on `tools[...].items` | MCP JSON Schema arrays without `items` | Handled in code via `_patch_mcp_tools_for_gemini`. |
| `youtube_search_videos is not a valid tool` | That tool is not enabled on your MCP server | Enable YouTube search (or similar) in Composio/Pipedream, or follow `prompt.py`: use only tools listed with exact names (e.g. `YOUTUBE_ADD_VIDEO_TO_PLAYLIST`). |
| `videoNotFound` | Invalid or removed video ID | Agent is instructed to swap IDs; enable search tools or use stable, well-known videos. |
| Model calls `googledrive_create_file` | Wrong tool name | Composio tools are usually `GOOGLEDRIVE_*` (see prompt). |

## Security

- Never commit `.env` or API keys. This repo’s `.gitignore` excludes `.env` and `venv/`.
- Rotate keys if they were exposed in logs or chat.

## License

Refer to the upstream repository for license information.

## Acknowledgements

- Course / demo foundation: **NxtWave** “Learning Path Generator” project ([mcp-learning-path-demo](https://github.com/revanthgopi-nw/mcp-learning-path-demo)).
- Integrations: **LangChain**, **LangGraph**, **langchain-mcp-adapters**, **Google Gen AI**, **Streamlit**, **Composio** / **Pipedream**.
