import asyncio, json, os, subprocess
import httpx
from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession
from databricks.sdk import WorkspaceClient

MCP_URL = "https://arq-group-databricks-training.cloud.databricks.com/ai-gateway/mcp-services/system.ai.github"
OWNER = "ncsau-manjeet-singh"
REPO = "test_ms_repo"

# Uses the OAuth login stored by the Databricks CLI (~/.databrickscfg + token cache)
w = WorkspaceClient(profile="arq-training", auth_type="databricks-cli")

# Get a fresh bearer token from the CLI auth (no PAT needed)
auth_header = w.config.authenticate()["Authorization"]   # "Bearer <token>"
token = auth_header.split(" ", 1)[1]

#DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN") or _databricks_token()
DATABRICKS_TOKEN = token

async def list_branches(session, owner, repo, per_page=100):
    """Call the list_branches tool page by page until no more results."""
    branches, page = [], 1
    while True:
        result = await session.call_tool(
            "list_branches",
            {"owner": owner, "repo": repo, "page": page, "per_page": per_page},
        )
        if result.is_error:
            raise RuntimeError(" ".join(c.text for c in result.content if hasattr(c, "text")))

        # The tool returns JSON as text content
        batch = []
        for c in result.content:
            if getattr(c, "type", None) == "text":
                data = json.loads(c.text)
                batch.extend(data if isinstance(data, list) else data.get("branches", []))

        branches.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return branches

async def create_pull_request(session, owner, repo, head, base):
    """Create a pull request and return the MCP tool result."""
    title = f"Merge {head} into {base}"
    body = (
        "## Summary\n"
        f"This pull request merges the `{head}` branch into `{base}`.\n\n"
        "## Changes\n"
        "- Integrates the latest changes from the feature branch.\n"
        "- Keeps the development branch up to date for further integration and testing.\n\n"
        "## Validation\n"
        "- Verify feature behavior against the development environment.\n"
        "- Complete the repository's standard checks and review process before merging.\n"
    )

    result = await session.call_tool(
        "create_pull_request",
        {
            "owner": owner,
            "repo": repo,
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": False,
            "maintainer_can_modify": True,
        },
    )
    if result.is_error:
        error_text = " ".join(
            c.text for c in result.content if hasattr(c, "text")
        )
        if "403" in error_text or "Resource not accessible" in error_text:
            raise RuntimeError(
                f"Unable to create the pull request: GitHub write access is denied for "
                f"{owner}/{repo}. Install the Databricks GitHub Connector app on this "
                "repository and grant it repository access: "
                "https://github.com/apps/databricks-github-connector/installations/new"
            )
        raise RuntimeError(error_text)
    return result

async def list_pull_requests_for_branch(session, owner, repo, branch):
    """Return pull requests whose head branch matches the given branch."""
    pull_requests = []
    page = 1

    while True:
        result = await session.call_tool(
            "list_pull_requests",
            {
                "owner": owner,
                "repo": repo,
                "head": f"{owner}:{branch}",
                "state": "all",
                "page": page,
                "perPage": 100,
            },
        )
        if result.is_error:
            error_text = " ".join(
                c.text for c in result.content if hasattr(c, "text")
            )
            raise RuntimeError(error_text)

        batch = []
        for content in result.content:
            if getattr(content, "type", None) == "text":
                data = json.loads(content.text)
                if isinstance(data, list):
                    batch.extend(data)
                else:
                    batch.extend(
                        data.get("pull_requests")
                        or data.get("pullRequests")
                        or data.get("items")
                        or []
                    )

        pull_requests.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    return pull_requests

async def main():
    http_client = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {DATABRICKS_TOKEN}"}, timeout=60
    )
    async with http_client, streamable_http_client(MCP_URL, http_client=http_client) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            branches = await list_branches(session, OWNER, REPO)
            print(f"{len(branches)} branches in {OWNER}/{REPO}:")
            for b in branches:
                print(f"- {b['name']}" + (" (protected)" if b.get("protected") else ""))

            pull_requests = await list_pull_requests_for_branch(
                session, OWNER, REPO, branch="feature1"
            )
            print(
                f"{len(pull_requests)} pull requests from "
                f"{OWNER}:feature1 in {OWNER}/{REPO}:"
            )
            for pull_request in pull_requests:
                number = pull_request.get("number")
                title = pull_request.get("title")
                state = pull_request.get("state")
                print(f"- #{number} [{state}] {title}")

            #pull_request = await create_pull_request(
            #    session, OWNER, REPO, head="feature1", base="develop"
            #)
            #for content in pull_request.content:
            #   if getattr(content, "type", None) == "text":
            #        print(content.text)

def find_runtime_errors(error):
    if isinstance(error, BaseExceptionGroup):
        return [
            nested_error
            for nested in error.exceptions
            for nested_error in find_runtime_errors(nested)
        ]
    return [error] if isinstance(error, RuntimeError) else []


try:
    asyncio.run(main())
except BaseExceptionGroup as error_group:
    runtime_errors = find_runtime_errors(error_group)
    if runtime_errors:
        print(f"Error: {runtime_errors[0]}")
    else:
        raise
except RuntimeError as error:
    print(f"Error: {error}")