import os
import re
import ast
import time
import logging
from typing import Any
import httpx
import jwt
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("github_auth")
logger.setLevel(logging.INFO)

GITHUB_API_BASE_URL = os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")


def load_github_app_private_key() -> str:
    """
    Loads the GitHub App Private Key (.pem) from environment variables.
    Checks:
    1. GITHUB_APP_PRIVATE_KEY_PATH (path to .pem file on disk)
    2. GITHUB_APP_PRIVATE_KEY (either path to .pem file or direct PEM content string)
    """
    key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
    if key_path and os.path.isfile(key_path):
        with open(key_path, "r", encoding="utf-8") as f:
            return f.read().strip()

    raw_key = os.getenv("GITHUB_APP_PRIVATE_KEY")
    if raw_key:
        raw_key = raw_key.strip()
        if os.path.isfile(raw_key):
            with open(raw_key, "r", encoding="utf-8") as f:
                return f.read().strip()
        if "BEGIN RSA PRIVATE KEY" in raw_key or "BEGIN PRIVATE KEY" in raw_key:
            # Handle potential escaped newlines in environment variable
            return raw_key.replace("\\n", "\n")

    raise ValueError(
        "GitHub App Private Key not found. Please set GITHUB_APP_PRIVATE_KEY_PATH or "
        "GITHUB_APP_PRIVATE_KEY in your environment with a valid RSA .pem private key."
    )


def generate_github_app_jwt(
    app_id: str | int | None = None,
    private_key_pem: str | None = None,
    expiration_seconds: int = 600,
) -> str:
    """
    Generates a cryptographically signed RS256 JWT for GitHub App authentication.
    Claims:
    - iat: Issued at time (60 seconds in the past to compensate for clock skew)
    - exp: Expiration time (default 10 minutes, GitHub maximum)
    - iss: GitHub App ID
    """
    resolved_app_id = app_id or os.getenv("GITHUB_APP_ID")
    if not resolved_app_id:
        raise ValueError(
            "GitHub App ID not configured. Please set GITHUB_APP_ID or pass app_id."
        )

    resolved_pem = private_key_pem or load_github_app_private_key()

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + min(expiration_seconds, 600),
        "iss": str(resolved_app_id),
    }

    signed_jwt = jwt.encode(payload, resolved_pem, algorithm="RS256")
    return signed_jwt if isinstance(signed_jwt, str) else signed_jwt.decode("utf-8")


async def get_github_installation_token(
    installation_id: int | str | None = None,
    app_jwt: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """
    Exchanges the GitHub App JWT for a temporary repository access token
    by calling POST /app/installations/{installation_id}/access_tokens.
    """
    resolved_installation_id = installation_id or os.getenv("GITHUB_APP_INSTALLATION_ID")
    if not resolved_installation_id:
        raise ValueError(
            "GitHub installation_id is required. Please pass installation_id or set GITHUB_APP_INSTALLATION_ID."
        )

    jwt_token = app_jwt or generate_github_app_jwt()

    url = f"{GITHUB_API_BASE_URL}/app/installations/{resolved_installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MilestoneEscrow-AI-Auditor",
    }

    if client is not None:
        resp = await client.post(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["token"]

    async with httpx.AsyncClient(timeout=30.0) as async_cli:
        resp = await async_cli.post(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["token"]


def get_github_installation_token_sync(
    installation_id: int | str | None = None,
    app_jwt: str | None = None,
) -> str:
    """
    Synchronous wrapper for exchanging a GitHub App JWT for a temporary installation token.
    """
    resolved_installation_id = installation_id or os.getenv("GITHUB_APP_INSTALLATION_ID")
    if not resolved_installation_id:
        raise ValueError(
            "GitHub installation_id is required. Please pass installation_id or set GITHUB_APP_INSTALLATION_ID."
        )

    jwt_token = app_jwt or generate_github_app_jwt()

    url = f"{GITHUB_API_BASE_URL}/app/installations/{resolved_installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MilestoneEscrow-AI-Auditor",
    }

    with httpx.Client(timeout=30.0) as cli:
        resp = cli.post(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["token"]


async def get_repo_installation_id(
    owner: str,
    repo: str,
    app_jwt: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> int | None:
    """
    Discovers the GitHub App installation ID for a specific repository
    by calling GET /repos/{owner}/{repo}/installation with the App JWT.
    """
    jwt_token = app_jwt or generate_github_app_jwt()
    url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/installation"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MilestoneEscrow-AI-Auditor",
    }

    try:
        if client is not None:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json().get("id")
        else:
            async with httpx.AsyncClient(timeout=20.0) as async_cli:
                resp = await async_cli.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json().get("id")
    except Exception as exc:
        logger.warning("Could not auto-resolve installation ID for %s/%s: %s", owner, repo, exc)

    return None


def parse_github_url(url: str) -> dict[str, Any]:
    """
    Parses a GitHub URL into structured components (owner, repo, pull_number, is_pr).
    Supports:
    - https://github.com/owner/repo/pull/123
    - https://github.com/owner/repo/pull/123/files
    - https://github.com/owner/repo
    - git@github.com:owner/repo.git
    """
    clean = url.strip()
    pr_match = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+)/pull/(\d+)", clean, re.IGNORECASE)
    if pr_match:
        return {
            "is_pr": True,
            "owner": pr_match.group(1),
            "repo": pr_match.group(2).removesuffix(".git"),
            "pull_number": int(pr_match.group(3)),
        }

    repo_match = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+)", clean, re.IGNORECASE)
    if repo_match:
        return {
            "is_pr": False,
            "owner": repo_match.group(1),
            "repo": repo_match.group(2).removesuffix(".git"),
            "pull_number": None,
        }

    return {
        "is_pr": False,
        "owner": "",
        "repo": "",
        "pull_number": None,
    }


async def fetch_private_github_pr_diff_and_files(
    owner: str,
    repo: str,
    pull_number: int,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Uses the GitHub installation token with httpx to fetch:
    1. Raw unified git diff (Accept: application/vnd.github.v3.diff)
    2. Changed files list and patches (Accept: application/vnd.github+json)
    3. Raw codebase file contents in-memory (Accept: application/vnd.github.raw)

    Ensures all content is kept purely in memory and NEVER written to the local disk.
    """
    base = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls/{pull_number}"

    # 1. Fetch raw unified git diff
    diff_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3.diff",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MilestoneEscrow-AI-Auditor",
    }

    # 2. Fetch changed files list & metadata
    json_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MilestoneEscrow-AI-Auditor",
    }

    # 3. Fetch raw file contents in-memory
    raw_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.raw",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MilestoneEscrow-AI-Auditor",
    }

    diff_text = ""
    files_list: list[dict[str, Any]] = []
    files_content_dict: dict[str, str] = {}
    patches_dict: dict[str, str] = {}

    async def _execute_fetches(cli: httpx.AsyncClient):
        nonlocal diff_text, files_list, files_content_dict, patches_dict

        # Fetch PR diff
        diff_res = await cli.get(base, headers=diff_headers)
        if diff_res.status_code == 200:
            diff_text = diff_res.text

        # Fetch PR files
        files_res = await cli.get(f"{base}/files", headers=json_headers)
        if files_res.status_code == 200:
            files_list = files_res.json()

        # Download raw file contents in-memory for code files
        code_extensions = (
            ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".sql", ".sh",
            ".yaml", ".yml", ".toml", ".rs", ".go", ".cpp", ".c", ".h"
        )
        for item in files_list:
            fname = item.get("filename", "")
            patch = item.get("patch", "")
            if patch:
                patches_dict[fname] = patch

            # Skip removed files or non-code extensions
            if item.get("status") == "removed" or not fname.lower().endswith(code_extensions):
                continue

            raw_url = item.get("raw_url")
            contents_url = item.get("contents_url")

            file_content: str | None = None
            if contents_url:
                c_res = await cli.get(contents_url, headers=raw_headers)
                if c_res.status_code == 200:
                    file_content = c_res.text
            elif raw_url:
                r_res = await cli.get(raw_url, headers={"Authorization": f"Bearer {token}"})
                if r_res.status_code == 200:
                    file_content = r_res.text

            # If full file fetch failed or not available, use the patch in memory
            if file_content is not None:
                files_content_dict[fname] = file_content
            elif patch:
                files_content_dict[fname] = f"# Extracted from PR Patch:\n{patch}"

    if client is not None:
        await _execute_fetches(client)
    else:
        async with httpx.AsyncClient(timeout=45.0) as async_cli:
            await _execute_fetches(async_cli)

    return {
        "owner": owner,
        "repo": repo,
        "pull_number": pull_number,
        "diff": diff_text,
        "files": files_content_dict,
        "patches": patches_dict,
        "summary": {
            "total_files": len(files_list),
            "additions": sum(f.get("additions", 0) for f in files_list),
            "deletions": sum(f.get("deletions", 0) for f in files_list),
            "filenames": [f.get("filename", "") for f in files_list],
        },
    }


def extract_in_memory_ast(files_dict: dict[str, str], diff_text: str = "") -> dict[str, Any]:
    """
    Performs Abstract Syntax Tree (AST) extraction and syntactic linting
    purely in memory on the fetched codebase files.
    Never writes any file to the local filesystem.
    """
    ast_findings: list[dict[str, Any]] = []
    function_signatures: list[str] = []
    class_names: list[str] = []
    test_cases: list[str] = []
    assertions_count = 0
    total_ast_nodes = 0
    syntax_errors: list[str] = []

    for fname, source_code in files_dict.items():
        if fname.lower().endswith(".py"):
            try:
                tree = ast.parse(source_code, filename=fname)
                file_functions = []
                file_classes = []
                file_nodes = 0

                for node in ast.walk(tree):
                    file_nodes += 1
                    total_ast_nodes += 1
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = [a.arg for a in node.args.args]
                        sig = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}({', '.join(args)})"
                        file_functions.append(sig)
                        function_signatures.append(f"{fname} -> {sig}")
                        if node.name.startswith("test_") or "test" in node.name.lower():
                            test_cases.append(f"{fname}::{node.name}")
                    elif isinstance(node, ast.ClassDef):
                        file_classes.append(node.name)
                        class_names.append(f"{fname} -> class {node.name}")
                    elif isinstance(node, ast.Assert):
                        assertions_count += 1

                ast_findings.append({
                    "filename": fname,
                    "language": "python",
                    "ast_nodes": file_nodes,
                    "functions": file_functions,
                    "classes": file_classes,
                    "valid_ast": True,
                })
            except SyntaxError as syn_err:
                syntax_errors.append(f"{fname}: SyntaxError at line {syn_err.lineno}: {syn_err.msg}")
                ast_findings.append({
                    "filename": fname,
                    "language": "python",
                    "valid_ast": False,
                    "error": str(syn_err),
                })
            except Exception as exc:
                syntax_errors.append(f"{fname}: AST parse failed: {str(exc)}")

        elif fname.lower().endswith((".ts", ".tsx", ".js", ".jsx")):
            # Heuristic in-memory syntactic parsing for TypeScript / JavaScript
            funcs = re.findall(r"(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_]+)\s*\(([^)]*)\)", source_code)
            arrow_funcs = re.findall(r"(?:export\s+)?(?:const|let)\s+([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>", source_code)
            classes = re.findall(r"class\s+([a-zA-Z0-9_]+)", source_code)
            tests = re.findall(r"(?:it|test|describe)\s*\(\s*['\"]([^'\"]+)['\"]", source_code)
            asserts = len(re.findall(r"(?:expect|assert)\s*\(", source_code))

            js_funcs = [f"function {f[0]}({f[1].strip()})" for f in funcs] + [f"const {a[0]} = ({a[1].strip()}) =>" for a in arrow_funcs]
            for jf in js_funcs:
                function_signatures.append(f"{fname} -> {jf}")
            for jc in classes:
                class_names.append(f"{fname} -> class {jc}")
            for jt in tests:
                test_cases.append(f"{fname}::'{jt}'")
            assertions_count += asserts

            ast_findings.append({
                "filename": fname,
                "language": "typescript/javascript",
                "functions": js_funcs,
                "classes": classes,
                "test_cases": tests,
                "valid_ast": True,
            })

    return {
        "total_files_analyzed": len(files_dict),
        "total_ast_nodes": total_ast_nodes,
        "function_signatures": function_signatures,
        "class_names": class_names,
        "test_cases_identified": test_cases,
        "assertions_count": assertions_count,
        "syntax_errors": syntax_errors,
        "file_breakdown": ast_findings,
    }


def format_in_memory_audit_context(
    pr_data: dict[str, Any],
    ast_data: dict[str, Any],
    max_diff_length: int = 15000,
    max_file_length: int = 4000,
) -> str:
    """
    Formats the secure in-memory codebase, git diff, and AST structures
    into an isolated, sandboxed context prompt for the Gemini AI model.
    Guarantees zero filesystem footprint.
    """
    owner = pr_data.get("owner", "")
    repo = pr_data.get("repo", "")
    pull_number = pr_data.get("pull_number", "")
    summary = pr_data.get("summary", {})
    diff = pr_data.get("diff", "")
    files = pr_data.get("files", {})

    truncated_diff = diff[:max_diff_length] + ("\n... [diff truncated for token window]" if len(diff) > max_diff_length else "")

    file_snippets: list[str] = []
    for fname, content in list(files.items())[:15]:
        snippet = content[:max_file_length] + ("\n... [file truncated]" if len(content) > max_file_length else "")
        file_snippets.append(f"=== File: {fname} ===\n{snippet}\n")

    files_block = "\n".join(file_snippets) if file_snippets else "No changed code files could be fetched."

    functions_block = "\n".join(f"  - {f}" for f in ast_data.get("function_signatures", [])[:25]) or "  - None identified"
    classes_block = "\n".join(f"  - {c}" for c in ast_data.get("class_names", [])[:15]) or "  - None identified"
    tests_block = "\n".join(f"  - {t}" for t in ast_data.get("test_cases_identified", [])[:15]) or "  - None identified"
    errors_block = "\n".join(f"  - {e}" for e in ast_data.get("syntax_errors", [])) or "  - None"

    return f"""=== SECURE IN-MEMORY PRIVATE GITHUB ARTIFACTS (GITHUB APP AUTHENTICATION) ===
Repository: {owner}/{repo}
Target Pull Request: #{pull_number}
Summary: {summary.get('total_files', 0)} files changed (+{summary.get('additions', 0)} / -{summary.get('deletions', 0)})

[1. IN-MEMORY ABSTRACT SYNTAX TREE (AST) FORENSICS]
Total AST Nodes: {ast_data.get('total_ast_nodes', 0)}
Assertions Detected: {ast_data.get('assertions_count', 0)}
Syntax Errors:
{errors_block}

Defined Functions & Signatures:
{functions_block}

Defined Classes:
{classes_block}

Identified Test Cases:
{tests_block}

[2. RAW UNIFIED GIT DIFF]
{truncated_diff}

[3. CHANGED SOURCE FILES (IN-MEMORY SNAPSHOT)]
{files_block}
"""

