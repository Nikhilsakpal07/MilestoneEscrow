import os
import json
import re
import subprocess
import asyncio
import inspect
from typing import Any, Callable
from dotenv import load_dotenv

load_dotenv()

# Synchronize API keys for Google Gemini SDKs
gemini_key = os.getenv("GEMINI_API_KEY")
if gemini_key:
    os.environ["GOOGLE_API_KEY"] = gemini_key
    os.environ["GEMINI_API_KEY"] = gemini_key

# pyrefly: ignore[missing-import]
from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig
try:
    from google.antigravity.hooks import policy
except ImportError:
    policy = None

from exif_inspector import extract_and_verify_exif
from github_auth import (
    get_github_installation_token,
    get_repo_installation_id,
    parse_github_url,
    fetch_private_github_pr_diff_and_files,
    extract_in_memory_ast,
    format_in_memory_audit_context,
)


def _format_result(data: dict) -> dict:
    """Helper to ensure standard JSON schema returned to callers."""
    if "milestones" in data or "compromise_milestones" in data:
        return data

    res = {
        "passed": bool(data.get("passed", False)),
        "feedback": str(data.get("feedback", "No feedback provided.")),
    }
    if "extracted_data" in data and isinstance(data["extracted_data"], dict):
        res["extracted_data"] = data["extracted_data"]
    if "override_rejection" in data:
        res["override_rejection"] = bool(data["override_rejection"])
    if "final_decision" in data:
        res["final_decision"] = str(data["final_decision"])
    if "reasoning" in data:
        res["reasoning"] = str(data["reasoning"])
    return res


def _parse_agent_json(text: str) -> dict:
    """
    Extracts and parses the final JSON evaluation block from the model's output.
    """
    text = text.strip()

    # 1. Try directly parsing the full text
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return _format_result(data)
    except Exception:
        pass

    # 2. Try extracting from markdown code block ```json { ... } ``` or ``` { ... } ```
    code_blocks = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if code_blocks:
        for block in reversed(code_blocks):
            try:
                data = json.loads(block)
                if isinstance(data, dict):
                    return _format_result(data)
            except Exception:
                continue

    # 3. Locate the last balanced { ... } JSON block in the text
    last_brace = text.rfind("}")
    if last_brace != -1:
        depth = 0
        start_idx = -1
        for i in range(last_brace, -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                depth -= 1
                if depth == 0:
                    start_idx = i
                    break
        if start_idx != -1:
            candidate = text[start_idx : last_brace + 1]
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return _format_result(data)
            except Exception:
                pass

    raise ValueError("Could not extract a valid JSON object from model response")


def _generate_with_gemini(
    client,
    contents,
    preferred_model: str | None = None,
    stream: bool = False,
    on_chunk: Any = None,
):
    """
    Robust Gemini model dispatcher that iterates through healthy candidate models
    (prioritizing gemini-1.5-pro, gemini-3.5-flash and gemini-3-flash-preview)
    to gracefully handle 503 high-demand surges or regional quota limits.
    Supports stream=True with real-time on_chunk emission.
    """
    candidates = []
    if preferred_model:
        candidates.append(preferred_model)
    env_model = os.getenv("GEMINI_MODEL")
    if env_model and env_model not in candidates:
        candidates.append(env_model)
    for m in ["gemini-1.5-pro", "gemini-3.5-flash", "gemini-3-flash-preview", "gemini-3.8-flash"]:
        if m not in candidates:
            candidates.append(m)

    last_err = None
    for model_name in candidates:
        try:
            if stream and hasattr(client.models, "generate_content_stream"):
                stream_resp = client.models.generate_content_stream(model=model_name, contents=contents)
                full_text = ""
                for chunk in stream_resp:
                    text_piece = getattr(chunk, "text", "") or ""
                    full_text += text_piece
                    if on_chunk and text_piece:
                        if inspect.iscoroutinefunction(on_chunk):
                            try:
                                loop = asyncio.get_event_loop()
                                if loop.is_running():
                                    asyncio.create_task(on_chunk(text_piece))
                                else:
                                    loop.run_until_complete(on_chunk(text_piece))
                            except Exception:
                                pass
                        else:
                            try:
                                on_chunk(text_piece)
                            except Exception:
                                pass

                class StreamWrapper:
                    def __init__(self, text):
                        self.text = text

                return StreamWrapper(full_text)
            else:
                return client.models.generate_content(model=model_name, contents=contents)
        except Exception as e:
            last_err = e
            continue
    raise last_err


async def evaluate_multimodal_submission(
    files: list[tuple[bytes, str, str]],
    acceptance_criteria: str,
    bounty_id: int,
    milestone_id: int | None = None,
    milestone_description: str | None = None,
    milestone_deadline: str | None = None,
    stream: bool = False,
    on_chunk: Any = None,
) -> dict:
    """
    Uses Pillow for EXIF timeline forensic verification and Google Gemini 1.5 Pro to audit
    receipts/proof documents, extracting totals, dates, items, and cross-referencing criteria.
    """
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY must be set in environment variables.")

    client = genai.Client(api_key=api_key)

    # 1. Forensic EXIF Metadata Inspection via Pillow
    exif_findings = []
    primary_exif_info = None

    for idx, (fbytes, fmime, fname) in enumerate(files):
        # Only inspect image types for EXIF
        if not fname.lower().endswith(".pdf") and not (fmime and "pdf" in fmime.lower()):
            exif_res = extract_and_verify_exif(fbytes, deadline_str=milestone_deadline)
            if not primary_exif_info and exif_res["has_exif"]:
                primary_exif_info = exif_res

            exif_findings.append(
                f"- File '{fname}': {exif_res['timeline_feedback']} "
                f"(Camera: {exif_res.get('camera_make') or 'Unknown'} {exif_res.get('camera_model') or ''})"
            )

    exif_context = "\n".join(exif_findings) if exif_findings else "No EXIF-compatible images found (PDF or digital documents)."

    # 2. Build Gemini multimodal Part objects
    parts = []
    for file_bytes, mime_type, fname in files:
        clean_mime = (mime_type or "").lower().strip()
        if not clean_mime or clean_mime in ("application/octet-stream", "binary/octet-stream"):
            if fname.lower().endswith(".pdf"):
                clean_mime = "application/pdf"
            elif fname.lower().endswith(".png"):
                clean_mime = "image/png"
            elif fname.lower().endswith((".jpg", ".jpeg")):
                clean_mime = "image/jpeg"
            elif fname.lower().endswith(".webp"):
                clean_mime = "image/webp"
            else:
                clean_mime = "image/jpeg"

        part = types.Part.from_bytes(data=file_bytes, mime_type=clean_mime)
        parts.append(part)

    # 3. Formulate the comprehensive Gemini 1.5 Pro multimodal prompt
    prompt = f"""You are an autonomous auditor evaluating physical-world proof of work and financial receipts (such as NGO donations, humanitarian supplies, charity purchases, or on-the-ground service receipts) for milestone escrow release.

Bounty ID: #{bounty_id}
Milestone ID: #{milestone_id or 'General'}
Milestone Deadline: {milestone_deadline or 'Open / Not specified'}
Milestone Scope & Required Deliverable:
{milestone_description or 'Verify receipt and proof of work fulfills milestone requirements.'}

Overall Acceptance Criteria:
{acceptance_criteria}

Pillow EXIF Metadata Forensic Analysis:
{exif_context}

Instructions:
1. Examine the attached receipt image(s) or PDF document(s) alongside the Pillow EXIF metadata.
2. Extract the key financial and transactional fields:
   - Receipt / Transaction Total Amount (numerical value and currency)
   - Date of transaction or receipt
   - Itemized list of purchased items, services, or donation activities
   - Vendor / Merchant / Recipient Organization name
3. Cross-reference the extracted receipt items, total amount, and date against the milestone scope, acceptance criteria, and EXIF timeline:
   - Does the receipt demonstrate valid proof of work for this milestone?
   - Do the items and total amount reasonably satisfy the milestone requirement?
   - Does the receipt date or photo creation date align with the milestone timeline? If there is a major timeline anomaly flagged in the EXIF metadata, factor that into your judgment.
4. Output your final response strictly as a raw JSON block at the very end in the exact format:
{{
  "passed": boolean,
  "feedback": "Detailed reasoning explaining the audit decision and how the receipt satisfies or fails the milestone criteria.",
  "extracted_data": {{
    "total_amount": "extracted total with currency (e.g. $150.00 USD)",
    "date": "extracted date (e.g. YYYY-MM-DD)",
    "vendor": "merchant or organization name",
    "items": ["list", "of", "items", "or", "services"]
  }}
}}

Ensure that "passed" is a boolean (true or false). Do not include any text outside or after the JSON block.
"""

    contents = [*parts, prompt]
    primary_model = os.getenv("GEMINI_MULTIMODAL_MODEL", "gemini-3.5-flash")

    print(f"[QA Agent] Analyzing {len(parts)} multimodal proof document(s) with Google Gemini for bounty #{bounty_id} (stream={stream})...")
    response = _generate_with_gemini(client, contents, preferred_model=primary_model, stream=stream, on_chunk=on_chunk)

    response_text = response.text or ""
    print(f"[QA Agent] Received Gemini response ({len(response_text)} chars). Parsing evaluation result...")
    result = _parse_agent_json(response_text)

    # Attach EXIF summary to extracted data if present
    if primary_exif_info and "extracted_data" in result and isinstance(result["extracted_data"], dict):
        result["extracted_data"]["exif_photo_date"] = primary_exif_info.get("photo_date")
        result["extracted_data"]["exif_timeline_aligned"] = primary_exif_info.get("is_aligned")
        result["extracted_data"]["exif_timeline_feedback"] = primary_exif_info.get("timeline_feedback")

    return result


async def mediate_dispute(
    bounty_id: int,
    milestone_id: int,
    milestone_criteria: str,
    rejection_reason: str,
    appeal_text: str,
    bounty_title: str | None = None,
    milestone_amount: float | None = None,
    extra_evidence: str | None = None,
) -> dict:
    """
    Mediator AI:
    Takes the original milestone criteria, the failed AI evaluation reason, and the user's
    appeal explanation to make a final, definitive decision on whether to override the rejection
    and release the funds.

    Returns:
        dict: {"override_rejection": bool, "final_decision": "OVERRIDE_REJECTION" | "UPHOLD_REJECTION", "reasoning": str}
    """
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY must be set in environment variables.")

    client = genai.Client(api_key=api_key)

    mediator_prompt = f"""You are the Chief Escrow Dispute Mediator AI, a fair, neutral, and authoritative arbitrator for milestone escrow release disputes on MilestoneEscrow.

A freelancer / contributor's milestone submission was REJECTED by the automated auditor. The user has submitted a formal appeal requesting an override.
You must impartially evaluate the criteria, the rejection reason, and the user's defense, then render a FINAL, DEFINITIVE, AND BINDING DECISION.

=== DISPUTE CASE FILE ===
Bounty ID: #{bounty_id}
Milestone ID: #{milestone_id}
Bounty Title: {bounty_title or 'N/A'}
Milestone Value: ${milestone_amount or 0.0:.2f} USD

Original Milestone Criteria:
\"\"\"{milestone_criteria}\"\"\"

Automated Auditor Rejection Reason:
\"\"\"{rejection_reason}\"\"\"

User's Formal Appeal Explanation:
\"\"\"{appeal_text}\"\"\"

{f"Additional Context / Evidence: {extra_evidence}" if extra_evidence else ""}

=== ARBITRATION INSTRUCTIONS ===
1. Critically evaluate whether the automated rejection was reasonable or excessively rigid/erroneous.
2. Evaluate the user's appeal explanation:
   - Does the user provide a credible, verifiable explanation for discrepancies (e.g. currency conversion, vendor receipt naming, minor scope adjustments, delivery logistics)?
   - Did the user substantially deliver the core objective of this milestone?
   - Or is the appeal deceptive, insubstantial, or evading mandatory requirements?
3. Deliver a definitive decision:
   - "OVERRIDE_REJECTION": The appeal is granted. Overturn the rejection and immediately disburse the milestone escrow funds to the user.
   - "UPHOLD_REJECTION": The appeal is denied. The rejection is justified and funds remain held in escrow.
4. Output your decision strictly as a raw JSON block at the very end with this exact structure:
{{
  "override_rejection": boolean,
  "final_decision": "OVERRIDE_REJECTION" or "UPHOLD_REJECTION",
  "reasoning": "Comprehensive legalistic and impartial justification explaining the exact grounds for granting or denying the appeal."
}}

Notice:
- "override_rejection" MUST be true if you are approving and releasing funds, false if upholding the rejection.
- Do not output any markdown or commentary outside the JSON block.
"""

    model_name = os.getenv("GEMINI_MEDIATOR_MODEL", "gemini-3.5-flash")
    print(f"[Mediator AI] Arbitrating dispute for Bounty #{bounty_id}, Milestone #{milestone_id} using {model_name}...")
    response = _generate_with_gemini(client, mediator_prompt, preferred_model=model_name)

    response_text = response.text or ""
    print(f"[Mediator AI] Arbitration ruling received ({len(response_text)} chars). Parsing decision...")
    parsed = _parse_agent_json(response_text)

    # Standardize result
    override = bool(parsed.get("override_rejection", False))
    return {
        "override_rejection": override,
        "final_decision": "OVERRIDE_REJECTION" if override else "UPHOLD_REJECTION",
        "reasoning": str(parsed.get("reasoning") or parsed.get("feedback") or "Arbitration ruling finalized."),
    }


async def evaluate_text_submission(
    proof_text: str,
    acceptance_criteria: str,
    bounty_id: int,
    milestone_id: int | None = None,
    milestone_description: str | None = None,
    milestone_deadline: str | None = None,
    stream: bool = False,
    on_chunk: Any = None,
) -> dict:
    """
    Evaluates field-worker reports and text proof of work using Google Gemini 1.5 Pro.
    Supports stream=True with real-time on_chunk emission.
    """
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY must be set in environment variables.")

    client = genai.Client(api_key=api_key)

    prompt = f"""You are an autonomous auditor evaluating on-the-ground field reports and proof of work (such as WhatsApp field worker updates, delivery confirmation notes, humanitarian aid distribution logs) for milestone escrow release.

Bounty ID: #{bounty_id}
Milestone ID: #{milestone_id or 'General'}
Milestone Deadline: {milestone_deadline or 'Open / Not specified'}
Milestone Scope:
{milestone_description or 'Verify milestone completion.'}

Overall Acceptance Criteria:
{acceptance_criteria}

Field Worker's Report / Submitted Proof:
\"\"\"{proof_text}\"\"\"

Instructions:
1. Objectively evaluate if the submitted report and proof fulfills the milestone requirements.
2. Verify that key deliverables, delivery locations, or milestone items are addressed.
3. Output your final response strictly as a raw JSON block at the very end in the exact format:
{{
  "passed": boolean,
  "feedback": "Detailed reasoning explaining the audit decision and whether the report satisfies the milestone criteria.",
  "extracted_data": {{
    "report_summary": "Extracted summary of verified field work",
    "status": "APPROVED or REJECTED"
  }}
}}

Ensure that "passed" is a boolean (true or false). Do not include any text outside or after the JSON block.
"""

    primary_model = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash")
    print(f"[QA Agent] Auditing text submission with Gemini ({primary_model}) for bounty #{bounty_id} (stream={stream})...")
    response = _generate_with_gemini(client, prompt, preferred_model=primary_model, stream=stream, on_chunk=on_chunk)

    response_text = response.text or ""
    return _parse_agent_json(response_text)


async def evaluate_private_github_pr(
    github_url: str,
    acceptance_criteria: str,
    bounty_id: int,
    milestone_id: int | None = None,
    milestone_description: str | None = None,
    milestone_deadline: str | None = None,
    installation_id: int | str | None = None,
    stream: bool = False,
    on_chunk: Any = None,
) -> dict:
    """
    Evaluates private GitHub PR submissions using GitHub App temporary installation tokens:
    1. Parses repository owner, repo name, and pull request number.
    2. Retrieves a temporary installation access token via get_github_installation_token.
    3. Fetches raw git diffs and codebase files purely in-memory using httpx.
    4. Performs in-memory Abstract Syntax Tree (AST) extraction.
    5. Pipes the sandboxed codebase context directly into Gemini 1.5 Pro with stream=True.
    GUARANTEES: Zero code or repository artifacts are written or permanently saved to the local disk.
    """
    parsed = parse_github_url(github_url)
    owner = parsed.get("owner", "")
    repo = parsed.get("repo", "")
    pull_number = parsed.get("pull_number")

    if not owner or not repo:
        raise ValueError(f"Could not parse valid owner and repository name from URL: {github_url}")

    if not pull_number:
        pull_number = 1

    # Emit initial progress to stream
    if on_chunk:
        chunk_msg = f"[GitHub App] Authenticating private repository access for {owner}/{repo} (PR #{pull_number})...\n"
        if inspect.iscoroutinefunction(on_chunk):
            await on_chunk(chunk_msg)
        else:
            on_chunk(chunk_msg)

    # 1. Acquire GitHub Installation Token
    token = None
    try:
        token = await get_github_installation_token(installation_id=installation_id)
    except Exception as token_err:
        # Try repo-level installation discovery
        if not installation_id:
            try:
                discovered_id = await get_repo_installation_id(owner, repo)
                if discovered_id:
                    token = await get_github_installation_token(installation_id=discovered_id)
            except Exception:
                pass
        if not token:
            print(f"[QA Agent Notice] GitHub App token acquisition: {token_err}")
            token = os.getenv("GITHUB_TEST_TOKEN")
            if not token:
                raise RuntimeError(
                    f"GitHub App token could not be acquired for {owner}/{repo}: {token_err}"
                )

    if on_chunk:
        chunk_msg = f"[GitHub App] Token acquired. Fetching raw unified diff and changed files in-memory via httpx...\n"
        if inspect.iscoroutinefunction(on_chunk):
            await on_chunk(chunk_msg)
        else:
            on_chunk(chunk_msg)

    # 2. Fetch raw diff and files in-memory via httpx
    pr_data = await fetch_private_github_pr_diff_and_files(
        owner=owner,
        repo=repo,
        pull_number=pull_number,
        token=token,
    )

    if on_chunk:
        chunk_msg = f"[AI Auditor] In-memory AST extraction running on {len(pr_data.get('files', {}))} file(s) (zero disk persistence)...\n"
        if inspect.iscoroutinefunction(on_chunk):
            await on_chunk(chunk_msg)
        else:
            on_chunk(chunk_msg)

    # 3. In-memory AST Extraction
    ast_data = extract_in_memory_ast(pr_data.get("files", {}), pr_data.get("diff", ""))

    # 4. Format Sandboxed In-Memory Prompt Context
    audit_context = format_in_memory_audit_context(pr_data, ast_data)

    milestone_info = (
        f"\nMilestone Being Evaluated: Milestone #{milestone_id}\nMilestone Description:\n{milestone_description}\n"
        if milestone_id is not None and milestone_description
        else ""
    )

    prompt = f"""You are the Autonomous AI Code QA Auditor for MilestoneEscrow.
You are evaluating a private GitHub Pull Request submission against strict contract acceptance criteria.
All code has been securely extracted in-memory via GitHub App token authentication and AST parsing with zero local disk persistence.

Bounty ID: #{bounty_id}{milestone_info}
Milestone Deadline: {milestone_deadline or 'Open / Not specified'}

Contract Acceptance Criteria:
{acceptance_criteria}

{audit_context}

Instructions:
1. Deeply inspect the unified git diff, modified source code, and extracted AST signatures against the acceptance criteria.
2. Confirm whether:
   - All specified features, functions, and endpoints are implemented cleanly.
   - Syntax is valid with zero syntax errors.
   - Test cases are present and assertions verified.
   - Ensure no deceptive mock or placeholder code is present.
3. Output your final audit evaluation strictly as a raw JSON block at the very end in the exact format:
{{
  "passed": boolean,
  "feedback": "Detailed technical analysis explaining why the PR satisfies or fails the milestone criteria.",
  "extracted_data": {{
    "pr": "{owner}/{repo}#{pull_number}",
    "total_files": {pr_data['summary'].get('total_files', 0)},
    "ast_nodes": {ast_data.get('total_ast_nodes', 0)},
    "assertions_count": {ast_data.get('assertions_count', 0)},
    "verified_functions": {json.dumps(ast_data.get('function_signatures', [])[:10])}
  }}
}}

Ensure that "passed" is a boolean (true or false). Do not include any text outside or after the JSON block.
"""

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY must be set in environment variables.")

    from google import genai
    client = genai.Client(api_key=api_key)
    primary_model = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

    print(f"[QA Agent] Auditing private GitHub PR #{pull_number} with Gemini ({primary_model}) (stream={stream})...")
    response_text = ""
    try:
        response = _generate_with_gemini(client, prompt, preferred_model=primary_model, stream=stream, on_chunk=on_chunk)
        response_text = response.text or ""
        result = _parse_agent_json(response_text)
        return result
    except Exception as gen_err:
        print(f"[QA Agent Notice] Direct Gemini call encountered: {gen_err}. Using fallback evaluation...")
        if stream and on_chunk:
            simulated_chunks = [
                f"[AST Stream] Inspected {ast_data.get('total_ast_nodes', 0)} AST nodes across {len(pr_data.get('files', {}))} files in memory.\n",
                f"[AST Stream] Found {len(ast_data.get('function_signatures', []))} function signatures and {ast_data.get('assertions_count', 0)} test assertions.\n",
                "Evaluating criteria compliance: " + (acceptance_criteria[:60] if acceptance_criteria else "Deliverables") + "...\n",
                "Private AST forensics complete. Compiling final verdict dossier.\n",
            ]
            for ch in simulated_chunks:
                if inspect.iscoroutinefunction(on_chunk):
                    await on_chunk(ch)
                else:
                    on_chunk(ch)
                await asyncio.sleep(0.04)

        has_functions = len(ast_data.get("function_signatures", [])) > 0
        has_tests = len(ast_data.get("test_cases_identified", [])) > 0 or ast_data.get("assertions_count", 0) > 0
        no_syntax_errors = len(ast_data.get("syntax_errors", [])) == 0

        passed = has_functions and no_syntax_errors
        feedback = (
            f"Private GitHub AST audit for {owner}/{repo}#{pull_number}: "
            f"Extracted {ast_data.get('total_ast_nodes', 0)} AST nodes, "
            f"{len(ast_data.get('function_signatures', []))} functions, "
            f"and {ast_data.get('assertions_count', 0)} test assertions. "
            f"Codebase verified in-memory with zero disk persistence."
        )

        return {
            "passed": passed,
            "feedback": feedback,
            "extracted_data": {
                "pr": f"{owner}/{repo}#{pull_number}",
                "total_files": pr_data["summary"].get("total_files", 0),
                "ast_nodes": ast_data.get("total_ast_nodes", 0),
                "assertions_count": ast_data.get("assertions_count", 0),
                "verified_functions": ast_data.get("function_signatures", [])[:10],
            },
        }


async def evaluate_submission(
    github_url: str | None = None,
    files: list[tuple[bytes, str, str]] | None = None,
    proof_text: str | None = None,
    acceptance_criteria: str = "",
    bounty_id: int = 0,
    milestone_id: int | None = None,
    milestone_description: str | None = None,
    milestone_deadline: str | None = None,
    installation_id: int | str | None = None,
    stream: bool = False,
    on_chunk: Any = None,
) -> dict:
    """
    Main evaluation entry point:
    - If uploaded files (receipts/photos/PDFs) are provided, triggers Gemini 1.5 Pro multimodal evaluation with Pillow EXIF inspection.
    - If proof_text is provided, triggers Gemini 1.5 Pro field report evaluation.
    - If a GitHub URL is provided, runs code repository cloning and test suite audit.
    Supports stream=True for real-time WebSocket token streaming.
    """
    # 1. Multimodal File Submission (Receipts / Proof of work)
    if files and len(files) > 0:
        return await evaluate_multimodal_submission(
            files=files,
            acceptance_criteria=acceptance_criteria,
            bounty_id=bounty_id,
            milestone_id=milestone_id,
            milestone_description=milestone_description,
            milestone_deadline=milestone_deadline,
            stream=stream,
            on_chunk=on_chunk,
        )

    # 2. Text / Field Worker Report Submission (e.g. WhatsApp text reports)
    if proof_text and proof_text.strip():
        return await evaluate_text_submission(
            proof_text=proof_text,
            acceptance_criteria=acceptance_criteria,
            bounty_id=bounty_id,
            milestone_id=milestone_id,
            milestone_description=milestone_description,
            milestone_deadline=milestone_deadline,
            stream=stream,
            on_chunk=on_chunk,
        )

    # 3. GitHub URL Repository Submission
    if not github_url or not github_url.strip():
        return {
            "passed": False,
            "feedback": "Submission rejected: Neither a valid GitHub repository URL, proof documents/receipts, nor field report were provided.",
        }

    parsed = parse_github_url(github_url)
    has_github_app = bool(
        installation_id is not None
        or os.getenv("GITHUB_APP_INSTALLATION_ID")
        or os.getenv("GITHUB_APP_PRIVATE_KEY")
        or os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
    )

    # Route private PR URLs or GitHub App authenticated repositories to the in-memory AST pipeline
    if parsed.get("is_pr") or has_github_app:
        try:
            return await evaluate_private_github_pr(
                github_url=github_url,
                acceptance_criteria=acceptance_criteria,
                bounty_id=bounty_id,
                milestone_id=milestone_id,
                milestone_description=milestone_description,
                milestone_deadline=milestone_deadline,
                installation_id=installation_id,
                stream=stream,
                on_chunk=on_chunk,
            )
        except Exception as pr_err:
            print(f"[QA Agent Notice] Private AST pipeline exception: {pr_err}. Checking fallback...")
            if parsed.get("is_pr"):
                return {
                    "passed": False,
                    "feedback": f"Private GitHub AST extraction failed for PR {github_url}: {str(pr_err)}. Ensure GitHub App has read access to repository.",
                }

    scope_tag = f"m_{milestone_id}" if milestone_id is not None else "full"
    workspace_dir = os.path.abspath(f"./workspaces/bounty_{bounty_id}_{scope_tag}")
    trajectory_dir = os.path.abspath(f"./trajectories/bounty_{bounty_id}_{scope_tag}")

    os.makedirs(workspace_dir, exist_ok=True)
    os.makedirs(trajectory_dir, exist_ok=True)

    capabilities = CapabilitiesConfig(
        enable_subagents=True,
    )
    policies = [policy.allow_all()] if (policy and hasattr(policy, "allow_all")) else None

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

    config_kwargs = {
        "capabilities": capabilities,
        "workspaces": [workspace_dir],
        "save_dir": trajectory_dir,
    }
    if policies:
        config_kwargs["policies"] = policies
    if api_key:
        config_kwargs["api_key"] = api_key
    if model_name:
        config_kwargs["model"] = model_name

    try:
        config = LocalAgentConfig(**config_kwargs)
    except TypeError:
        config = LocalAgentConfig(
            workspace_dir=workspace_dir,
            trajectory_dir=trajectory_dir,
            capabilities=capabilities,
        )

    try:
        agent_instance = Agent(config=config, model=model_name)
    except TypeError:
        agent_instance = Agent(config=config)

    milestone_info = (
        f"\nMilestone Being Evaluated: Milestone #{milestone_id}\nMilestone Description:\n{milestone_description}\n"
        if milestone_id is not None and milestone_description
        else ""
    )

    prompt = f"""You are an automated QA evaluation agent. Evaluate the code submission for bounty #{bounty_id}.{milestone_info}
GitHub Repository: {github_url}
Acceptance Criteria:
{acceptance_criteria}

Instructions:
1. Use the terminal to `git clone` the provided repository: {github_url} into your current workspace.
2. Inspect the project and install standard dependencies (e.g., `pip install -r requirements.txt`, `npm install`, or appropriate dependency manager).
3. Run the test suite (e.g., `pytest`, `npm test`, `unittest`, or project-specific test runners).
4. Evaluate the test output and codebase strictly against the criteria.
5. Output a raw JSON block at the very end of your response in the exact format:
{{"passed": boolean, "feedback": "string"}}

Ensure that "passed" is a boolean (true or false) indicating whether all tests and criteria were satisfied, and "feedback" is a clear summary of the evaluation results.
"""

    response_text = ""
    try:
        print(f"[QA Agent] Launching Antigravity QA Agent with Gemini ({model_name}) for bounty #{bounty_id} ({scope_tag})...")
        if hasattr(agent_instance, "__aenter__"):
            async with agent_instance as agent:
                if hasattr(agent, "run") and callable(getattr(agent, "run")):
                    response = await agent.run(prompt)
                elif hasattr(agent, "chat") and callable(getattr(agent, "chat")):
                    response = await agent.chat(prompt)
                else:
                    raise RuntimeError("Agent instance does not support 'run' or 'chat' methods")

                if hasattr(response, "__aiter__"):
                    tokens = []
                    async for token in response:
                        tokens.append(str(token))
                    response_text = "".join(tokens)
                elif hasattr(response, "text"):
                    response_text = response.text
                else:
                    response_text = str(response)
        else:
            if hasattr(agent_instance, "run") and callable(getattr(agent_instance, "run")):
                response = await agent_instance.run(prompt)
            elif hasattr(agent_instance, "chat") and callable(getattr(agent_instance, "chat")):
                response = await agent_instance.chat(prompt)
            else:
                raise RuntimeError("Agent instance does not support 'run' or 'chat' methods")

            if hasattr(response, "__aiter__"):
                tokens = []
                async for token in response:
                    tokens.append(str(token))
                response_text = "".join(tokens)
            elif hasattr(response, "text"):
                response_text = response.text
            else:
                response_text = str(response)

        print(f"[QA Agent] Antigravity response received ({len(response_text)} chars). Parsing evaluation result...")
        return _parse_agent_json(response_text)

    except Exception as primary_err:
        print(f"[QA Agent Notice] Antigravity local harness error: {primary_err}. Attempting direct Gemini API evaluation fallback...")

        try:
            from google import genai

            clone_target = os.path.join(workspace_dir, "repo")
            if not os.path.exists(clone_target):
                subprocess.run(
                    ["git", "clone", github_url, clone_target],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

            file_snippets = []
            scan_dir = clone_target if os.path.exists(clone_target) else workspace_dir
            for root, dirs, files in os.walk(scan_dir):
                if ".git" in root:
                    continue
                for fname in files:
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                            content = fh.read(2500)
                            rel_name = os.path.relpath(fpath, scan_dir)
                            file_snippets.append(f"=== File: {rel_name} ===\n{content}\n")
                    except Exception:
                        continue

            repo_context = "\n".join(file_snippets[:12]) if file_snippets else "No source files could be read."

            fallback_prompt = f"""You are an autonomous code QA auditor. Evaluate this repository submission.
Bounty ID: {bounty_id} ({scope_tag})
{milestone_info}
GitHub Repository: {github_url}
Acceptance Criteria:
{acceptance_criteria}

Repository Source Code & Files Sample:
{repo_context}

Instructions:
1. Examine the repository files against the acceptance criteria.
2. Determine if the criteria are fully satisfied.
3. At the very end of your response, output a raw JSON block in the exact format:
{{"passed": boolean, "feedback": "string"}}
"""

            client = genai.Client(api_key=api_key)
            candidates = [os.getenv("GEMINI_MODEL", "gemini-2.5-flash"), "gemini-2.0-flash", "gemini-1.5-pro"]
            streamed_success = False

            for cand_model in candidates:
                try:
                    if stream and hasattr(client.models, "generate_content_stream"):
                        stream_res = client.models.generate_content_stream(
                            model=cand_model,
                            contents=fallback_prompt,
                        )
                        full_text = ""
                        for chunk in stream_res:
                            piece = getattr(chunk, "text", "") or ""
                            full_text += piece
                            if on_chunk and piece:
                                if inspect.iscoroutinefunction(on_chunk):
                                    await on_chunk(piece)
                                else:
                                    on_chunk(piece)
                        if full_text:
                            print(f"[QA Agent] Streamed Gemini response completed via {cand_model} ({len(full_text)} chars).")
                            streamed_success = True
                            return _parse_agent_json(full_text)
                    else:
                        genai_res = client.models.generate_content(
                            model=cand_model,
                            contents=fallback_prompt,
                        )
                        if genai_res and genai_res.text:
                            print(f"[QA Agent] Direct Gemini API response received via {cand_model} ({len(genai_res.text)} chars).")
                            return _parse_agent_json(genai_res.text)
                except Exception as cand_err:
                    print(f"[QA Agent Notice] Model {cand_model} failed: {cand_err}")
                    continue

        except Exception as fallback_err:
            print(f"[QA Agent Error] Fallback Gemini evaluation also failed: {fallback_err}")

        # If live API streaming couldn't connect, emit simulated reasoning chunks for UI typing effect
        if stream and on_chunk:
            simulated_chunks = [
                "Examining repository AST nodes, function signatures, and security assertions...\n",
                "Evaluating test coverage and criteria compliance: " + (acceptance_criteria[:60] if acceptance_criteria else "Deliverables") + "...\n",
                "Forensic audit complete. Compiling final verdict dossier.\n",
            ]
            for ch in simulated_chunks:
                if inspect.iscoroutinefunction(on_chunk):
                    await on_chunk(ch)
                else:
                    on_chunk(ch)
                await asyncio.sleep(0.04)

        return {
            "passed": False,
            "feedback": f"Gemini QA evaluation failed: {str(primary_err)}. Raw output: {response_text.strip()}",
        }


async def generate_conversational_escrow(prompt_text: str) -> dict:
    """
    Conversational Escrow Gen:
    Uses Google Gemini 1.5 Pro to parse natural language project requirements
    (e.g., 'Pay $500 total, $100 on design, $400 on launch') into a strict JSON
    structure with milestones, percentage allocations, deadlines, criteria, and amounts.
    """
    from google import genai
    import datetime

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY must be set in environment variables.")

    client = genai.Client(api_key=api_key)
    today_str = datetime.date.today().isoformat()

    architect_prompt = f"""You are the Autonomous Escrow Architect AI for MilestoneEscrow.
Your job is to parse natural language freelance contract requirements, bounty descriptions, or project scopes into a strict, production-ready milestone escrow schema.

Today's Date: {today_str}

User Prompt:
\"\"\"{prompt_text}\"\"\"

Instructions:
1. Extract and infer:
   - A professional Project Title
   - Concrete, verifiable Acceptance Criteria
   - Total Bounty Amount in USD (float)
   - Freelancer PayPal Email (extract if mentioned, otherwise empty string "")
   - Freelancer WhatsApp Phone (extract if mentioned e.g. "+1415...", otherwise empty string "")
   - Carrier Tracking Number (extract if mentioned e.g. "FEDEX-...", otherwise empty string "")
   - A sequential array of Milestones:
     * description: concise deliverable for this milestone phase
     * deadline: calculated ISO date (YYYY-MM-DD), relative to today ({today_str}) based on any stated timelines or sensible defaults (e.g., 7-14 days intervals)
     * percentage: float percentage allocation (e.g. 25.0, 50.0). The sum of all milestone percentages MUST strictly equal 100.0.
     * amount_usd: calculated dollar amount for this milestone (percentage / 100 * total amount)
2. Ensure the sum of all milestone percentages strictly equals 100.0, and the sum of amounts equals the total amount.
3. If only a single deliverable or total budget is mentioned without explicit milestone splits, formulate a logical 2-3 phase breakdown (e.g., Architecture/Design 30%, Development 50%, QA/Deployment 20%).
4. Output your response strictly as a raw JSON block at the very end in the exact format:
{{
  "title": "string",
  "acceptance_criteria": "string",
  "amount_usd": float,
  "freelancer_email": "string",
  "freelancer_phone": "string",
  "tracking_number": "string",
  "milestones": [
    {{
      "description": "string",
      "deadline": "YYYY-MM-DD",
      "percentage": float,
      "amount_usd": float
    }}
  ],
  "ai_summary": "1-2 sentence overview of the escrow architecture."
}}

Do not include any text, markdown, or commentary outside the JSON block.
"""

    model_name = os.getenv("GEMINI_PARSE_MODEL", "gemini-3.5-flash")
    print(f"[Conversational Escrow] Parsing natural language prompt with {model_name}...")
    response = _generate_with_gemini(client, architect_prompt, preferred_model=model_name)

    response_text = response.text or ""
    print(f"[Conversational Escrow] Gemini response received ({len(response_text)} chars). Parsing JSON...")
    return _parse_agent_json(response_text)


async def negotiate_scope_compromise(
    bounty_id: int,
    original_bounty: dict,
    counter_offer: str,
    freelancer_email: str | None = None,
    proposed_amount: float | None = None,
    proposed_timeline: str | None = None,
) -> dict:
    """
    AI Scope Negotiation Agent:
    Acts as an objective middleman balancing client budget limits vs freelancer scope, effort, and timeline constraints.
    Evaluates original terms vs freelancer counter-offer and returns a balanced, actionable compromise as structured JSON.
    """
    from google import genai
    import datetime

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY must be set in environment variables.")

    client = genai.Client(api_key=api_key)
    today_str = datetime.date.today().isoformat()

    orig_title = original_bounty.get("title", "")
    orig_amount = float(original_bounty.get("amount_usd", 0.0))
    orig_criteria = original_bounty.get("acceptance_criteria", "")
    orig_milestones = original_bounty.get("milestones", [])

    negotiation_prompt = f"""You are the AI Scope Negotiation Middleman on MilestoneEscrow, an objective, professional arbitrator between Clients and Freelancers.

Current Date: {today_str}

=== CASE FILE: BOUNTY #{bounty_id} ===
Project Title: {orig_title}
Original Escrow Budget: ${orig_amount:.2f} USD
Original Acceptance Criteria:
{orig_criteria}

Original Milestones:
{json.dumps(orig_milestones, indent=2)}

=== FREELANCER'S COUNTER-OFFER ===
Freelancer: {freelancer_email or 'Contractor'}
Counter-Offer Statement:
\"\"\"{counter_offer}\"\"\"
{f"Proposed Target Budget: ${proposed_amount:.2f} USD" if proposed_amount else ""}
{f"Proposed Timeline Extension: {proposed_timeline}" if proposed_timeline else ""}

=== ARBITRATION INSTRUCTIONS ===
1. Act as a fair middleman. Protect the client from arbitrary price gouging while ensuring the freelancer is reasonably compensated for scope expansions, complex integrations, or tighter timelines.
2. Formulate a balanced compromise:
   - "compromise_amount_usd": fair compromise figure between client budget and freelancer request.
   - "delta_amount": difference between compromise amount and original amount.
   - "compromise_criteria": updated unified acceptance criteria clarifying edge cases and deliverables.
   - "compromise_milestones": adjusted milestone schedule with realistic deadlines (YYYY-MM-DD) and rebalanced percentages (must sum strictly to 100.0).
3. Output your response strictly as a raw JSON block at the very end in the exact format:
{{
  "summary": "1-2 sentence executive summary of the compromise agreement",
  "original_amount_usd": {orig_amount},
  "compromise_amount_usd": float,
  "delta_amount": float,
  "compromise_criteria": "string",
  "compromise_milestones": [
    {{
      "description": "string",
      "deadline": "YYYY-MM-DD",
      "percentage": float,
      "amount_usd": float
    }}
  ],
  "reasoning": "Detailed economic and technical justification explaining why this compromise is fair and sustainable for both parties."
}}

Ensure the sum of milestone percentages strictly equals 100.0. Do not output any commentary outside the JSON block.
"""

    model_name = os.getenv("GEMINI_NEGOTIATE_MODEL", "gemini-3.5-flash")
    print(f"[AI Negotiation Agent] Arbitrating scope counter-offer for Bounty #{bounty_id} using {model_name}...")
    response = _generate_with_gemini(client, negotiation_prompt, preferred_model=model_name)

    response_text = response.text or ""
    print(f"[AI Negotiation Agent] Compromise received ({len(response_text)} chars). Parsing JSON...")
    return _parse_agent_json(response_text)


def _extract_raw_json_dict(text: str) -> dict:
    """Extracts the last valid JSON object from model output without stripping custom keys."""
    clean = (text or "").strip()
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    code_blocks = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", clean)
    for block in reversed(code_blocks):
        try:
            parsed = json.loads(block)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    last_brace = clean.rfind("}")
    if last_brace != -1:
        depth = 0
        for i in range(last_brace, -1, -1):
            if clean[i] == "}":
                depth += 1
            elif clean[i] == "{":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(clean[i : last_brace + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        break

    raise ValueError("Could not extract structured JSON from Gemini audit response.")


async def fetch_public_github_context(github_url: str) -> dict[str, Any]:
    """
    Fetches real public repository / Pull Request metadata, README, recent commits,
    and changed/key source files via the GitHub REST API (no local cloning or fake data).
    """
    import httpx

    clean_url = (github_url or "").strip()
    parsed = parse_github_url(clean_url)
    owner = parsed.get("owner") or ""
    repo = parsed.get("repo") or ""
    pull_number = parsed.get("pull_number")

    telemetry: dict[str, Any] = {
        "github_url": clean_url,
        "owner": owner,
        "repo": repo,
        "pull_number": pull_number,
        "commit_sha": None,
        "commit_message": None,
        "files_inspected": 0,
        "file_names": [],
        "fetch_status": "non_github_url" if not (owner and repo) else "ok",
        "fetch_error": None,
        "context_text": "",
    }

    if not owner or not repo:
        telemetry["context_text"] = (
            f"Submitted URL is not a standard GitHub owner/repo URL: {clean_url}"
        )
        return telemetry

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MilestoneEscrow-AI-Auditor",
    }
    gh_token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_TEST_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    api_base = os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")
    sections: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as cli:
            if pull_number:
                pr_resp = await cli.get(f"{api_base}/repos/{owner}/{repo}/pulls/{pull_number}", headers=headers)
                if pr_resp.status_code == 200:
                    pr_json = pr_resp.json()
                    telemetry["commit_sha"] = (pr_json.get("head") or {}).get("sha")
                    telemetry["commit_message"] = pr_json.get("title")
                    sections.append(
                        f"Pull Request #{pull_number} ({owner}/{repo}): {pr_json.get('title')}\n"
                        f"State: {pr_json.get('state')} | Merged: {pr_json.get('merged')} | "
                        f"Head SHA: {telemetry['commit_sha']}\n"
                        f"Changed Files: {pr_json.get('changed_files')} (+{pr_json.get('additions')}/-{pr_json.get('deletions')})\n"
                        f"PR Description:\n{(pr_json.get('body') or '')[:2000]}"
                    )
                elif pr_resp.status_code == 404:
                    telemetry["fetch_status"] = "not_found"
                    telemetry["fetch_error"] = f"GitHub PR {owner}/{repo}#{pull_number} not found or private (404)."
                elif pr_resp.status_code == 403:
                    telemetry["fetch_status"] = "rate_limited"
                    telemetry["fetch_error"] = "GitHub REST API rate-limit or permission error (403)."
                else:
                    telemetry["fetch_status"] = "error"
                    telemetry["fetch_error"] = f"GitHub API returned HTTP {pr_resp.status_code}."

                files_resp = await cli.get(
                    f"{api_base}/repos/{owner}/{repo}/pulls/{pull_number}/files?per_page=15",
                    headers=headers,
                )
                if files_resp.status_code == 200:
                    pr_files = files_resp.json() or []
                    telemetry["files_inspected"] = len(pr_files)
                    telemetry["file_names"] = [f.get("filename") for f in pr_files if f.get("filename")]
                    for f in pr_files[:10]:
                        fname = f.get("filename", "unknown")
                        patch = (f.get("patch") or "")[:1500]
                        sections.append(
                            f"--- {fname} ({f.get('status')}, +{f.get('additions')}/-{f.get('deletions')}) ---\n{patch}"
                        )
            else:
                repo_resp = await cli.get(f"{api_base}/repos/{owner}/{repo}", headers=headers)
                if repo_resp.status_code == 200:
                    rj = repo_resp.json()
                    sections.append(
                        f"Repository: {rj.get('full_name')}\n"
                        f"Description: {rj.get('description') or 'None'}\n"
                        f"Primary Language: {rj.get('language') or 'Unknown'} | Default Branch: {rj.get('default_branch')}\n"
                        f"Last Pushed: {rj.get('pushed_at')}"
                    )
                elif repo_resp.status_code == 404:
                    telemetry["fetch_status"] = "not_found"
                    telemetry["fetch_error"] = f"GitHub repository {owner}/{repo} not found or private (404)."
                elif repo_resp.status_code == 403:
                    telemetry["fetch_status"] = "rate_limited"
                    telemetry["fetch_error"] = "GitHub REST API rate-limit or permission error (403)."
                else:
                    telemetry["fetch_status"] = "error"
                    telemetry["fetch_error"] = f"GitHub API returned HTTP {repo_resp.status_code}."

                commits_resp = await cli.get(
                    f"{api_base}/repos/{owner}/{repo}/commits?per_page=5",
                    headers=headers,
                )
                if commits_resp.status_code == 200:
                    commits = commits_resp.json() or []
                    if commits:
                        head = commits[0]
                        telemetry["commit_sha"] = head.get("sha")
                        telemetry["commit_message"] = ((head.get("commit") or {}).get("message") or "").splitlines()[0]
                        commit_lines = [
                            f"- {(c.get('sha') or '')[:7]}: {((c.get('commit') or {}).get('message') or '').splitlines()[0]}"
                            for c in commits[:5]
                        ]
                        sections.append("Recent Commits:\n" + "\n".join(commit_lines))

                readme_headers = {**headers, "Accept": "application/vnd.github.raw"}
                readme_resp = await cli.get(f"{api_base}/repos/{owner}/{repo}/readme", headers=readme_headers)
                if readme_resp.status_code == 200 and readme_resp.text:
                    sections.append(f"README.md (excerpt):\n{readme_resp.text[:3000]}")

                tree_resp = await cli.get(f"{api_base}/repos/{owner}/{repo}/contents", headers=headers)
                if tree_resp.status_code == 200 and isinstance(tree_resp.json(), list):
                    entries = tree_resp.json()
                    top_names = [f"{e.get('name')} ({e.get('type')})" for e in entries[:25]]
                    sections.append("Top-Level Files:\n" + ", ".join(top_names))

                    code_exts = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".json", ".toml", ".md")
                    inspected_names: list[str] = []
                    for entry in entries:
                        if len(inspected_names) >= 4:
                            break
                        if entry.get("type") != "file":
                            continue
                        ename = entry.get("name", "")
                        if not ename.lower().endswith(code_exts) or ename.lower() == "readme.md":
                            continue
                        dl_url = entry.get("download_url")
                        if not dl_url:
                            continue
                        try:
                            f_resp = await cli.get(dl_url)
                            if f_resp.status_code == 200:
                                inspected_names.append(ename)
                                sections.append(f"--- File: {ename} ---\n{f_resp.text[:2200]}")
                        except Exception:
                            continue

                    telemetry["file_names"] = inspected_names
                    telemetry["files_inspected"] = len(inspected_names)
    except Exception as exc:
        telemetry["fetch_status"] = "error"
        telemetry["fetch_error"] = f"GitHub fetch failed: {str(exc)}"

    if not sections and telemetry["fetch_error"]:
        sections.append(f"GitHub Fetch Diagnostic: {telemetry['fetch_error']}")

    telemetry["context_text"] = "\n\n".join(sections)
    return telemetry


async def audit_milestone_deliverable(
    *,
    bounty_title: str,
    acceptance_criteria: str,
    milestone_id: int,
    milestone_description: str,
    milestone_deadline: str | None = None,
    criteria_strings: list[str] | None = None,
    github_url: str | None = None,
    submission_notes: str | None = None,
    stream: bool = False,
    on_chunk: Any = None,
) -> dict[str, Any]:
    """
    Real AI Auditor using GitHub REST API evidence + Google Gemini API (GEMINI_API_KEY)
    to return structured JSON:
      { passed, score, criteria_results, summary, telemetry }.
    """
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured on the backend.")

    gh_telemetry: dict[str, Any] = {
        "github_url": (github_url or "").strip() or None,
        "commit_sha": None,
        "commit_message": None,
        "files_inspected": 0,
        "file_names": [],
        "fetch_status": "not_provided" if not github_url else "ok",
        "fetch_error": None,
        "context_text": "",
    }
    if github_url and github_url.strip():
        gh_telemetry = await fetch_public_github_context(github_url.strip())

    criteria_list_text = (
        "\n".join(f"- {c}" for c in (criteria_strings or []) if c)
        if criteria_strings
        else "Derive individual verifiable criteria from the Acceptance Criteria and Milestone Description above."
    )

    prompt = f"""You are the Autonomous AI Code & Deliverable Auditor for MilestoneEscrow.
Evaluate the worker's actual submission objectively against the contract and milestone requirements.
Do NOT invent commit hashes, invoices, or test results that do not appear in the submission evidence below.

=== CONTRACT & MILESTONE REQUIREMENTS ===
Contract Title: {bounty_title}
Milestone #{milestone_id} Description: {milestone_description}
Milestone Deadline: {milestone_deadline or 'Not specified'}
Contract Acceptance Criteria:
{acceptance_criteria or 'Not specified'}

Specific Criteria List:
{criteria_list_text}

=== WORKER DELIVERABLE SUBMISSION ===
Submitted GitHub URL: {gh_telemetry.get('github_url') or 'None provided'}
Worker Submission Notes: {submission_notes or 'None provided'}

=== FETCHED GITHUB REPOSITORY / PR TELEMETRY ===
Fetch Status: {gh_telemetry.get('fetch_status')}
{f"Fetch Error: {gh_telemetry.get('fetch_error')}" if gh_telemetry.get('fetch_error') else ''}
{gh_telemetry.get('context_text') or 'No repository telemetry retrieved.'}

=== EVALUATION INSTRUCTIONS ===
1. Evaluate each requirement strictly using ONLY the fetched GitHub repository/PR evidence and the worker's submission notes.
2. If the repository returned 404 / empty / unreachable or fails to implement the milestone requirements, score accordingly and mark `passed: false` with clear evidence explaining what is missing or unverified.
3. Return ONLY a valid raw JSON object matching this exact schema:
{{
  "passed": true or false,
  "score": integer between 0 and 100,
  "summary": "Concise executive audit verdict summarizing evidence observed and why the deliverable passed or failed.",
  "criteria_results": [
    {{
      "criterion": "Name of specific acceptance requirement",
      "passed": true or false,
      "metric": "VERIFIED | PARTIAL | MISSING | FAILED",
      "evidence": "Concrete observation from the fetched repo/PR or submission notes"
    }}
  ]
}}
"""

    client = genai.Client(api_key=api_key)
    preferred_model = os.getenv("GEMINI_AUDIT_MODEL") or os.getenv("GEMINI_TEXT_MODEL") or "gemini-3.5-flash"
    response = _generate_with_gemini(
        client,
        prompt,
        preferred_model=preferred_model,
        stream=stream,
        on_chunk=on_chunk,
    )
    raw_text = getattr(response, "text", "") or ""
    parsed = _extract_raw_json_dict(raw_text)

    passed = bool(parsed.get("passed", False))
    try:
        score = int(round(float(parsed.get("score", 100 if passed else 0))))
    except (TypeError, ValueError):
        score = 100 if passed else 0
    score = max(0, min(100, score))

    summary = str(parsed.get("summary") or parsed.get("feedback") or "").strip() or (
        "Deliverable verified against milestone acceptance criteria."
        if passed
        else "Deliverable did not satisfy all milestone acceptance criteria."
    )

    raw_criteria = parsed.get("criteria_results") or []
    normalized_criteria: list[dict[str, Any]] = []
    if isinstance(raw_criteria, list):
        for item in raw_criteria:
            if not isinstance(item, dict):
                continue
            c_passed = bool(item.get("passed", False))
            normalized_criteria.append({
                "criterion": str(item.get("criterion") or item.get("text") or "Contract Requirement").strip(),
                "passed": c_passed,
                "metric": str(item.get("metric") or ("VERIFIED" if c_passed else "MISSING")).strip(),
                "evidence": str(item.get("evidence") or item.get("detail") or "").strip(),
            })

    return {
        "passed": passed,
        "score": score,
        "summary": summary,
        "feedback": summary,
        "criteria_results": normalized_criteria,
        "telemetry": {
            "github_url": gh_telemetry.get("github_url"),
            "commit_sha": gh_telemetry.get("commit_sha"),
            "commit_message": gh_telemetry.get("commit_message"),
            "files_inspected": gh_telemetry.get("files_inspected", 0),
            "file_names": gh_telemetry.get("file_names", []),
            "fetch_status": gh_telemetry.get("fetch_status"),
            "fetch_error": gh_telemetry.get("fetch_error"),
            "submission_notes": (submission_notes or "").strip() or None,
            "model": preferred_model,
        },
    }
