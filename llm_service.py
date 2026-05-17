"""
LLM service for AI Automation Radar.

Provider-agnostic: reads LLM_PROVIDER env var (openai | anthropic | gemini | mock).
Falls back to mock when LLM_PROVIDER is unset — no external calls, no crashes.

Public API:
    result = await generate_automation_insights(task_data, blocker_data, window_days)
    # result = {"findings": [...], "summary": "..."}
"""
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
_PROVIDER = os.getenv("LLM_PROVIDER", "mock").lower().strip()
_API_KEY = os.getenv("LLM_API_KEY", "")
_MODEL_OPENAI = os.getenv("LLM_MODEL", "gpt-4o-mini")
_MODEL_ANTHROPIC = os.getenv("LLM_MODEL", "claude-3-haiku-20240307")
_MODEL_GEMINI = os.getenv("LLM_MODEL", "gemini-2.0-flash")

_LLM_TIMEOUT = 60.0  # seconds — LLM responses can be slow

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(aggregated_task_data: str, blocker_data: str, window_days: int) -> tuple[str, str]:
    system_prompt = (
        "You are an AI analyst reviewing remote team standup data. "
        "Your job is to identify repetitive manual tasks that could be automated. "
        "Respond ONLY with valid JSON matching this exact schema — no extra text outside the JSON:\n"
        "{\n"
        '  "findings": [\n'
        "    {\n"
        '      "task_pattern": "<short descriptive name for the recurring task>",\n'
        '      "frequency": <integer — total mentions across the period>,\n'
        '      "affected_members": ["member name 1", "..."],\n'
        '      "source": "checkins" | "blockers" | "both",\n'
        '      "suggested_tools": ["Tool 1", "Tool 2"],\n'
        '      "reasoning": "<1-2 sentence explanation of why this is automatable>"\n'
        "    }\n"
        "  ],\n"
        '  "summary": "<2-3 sentence executive summary of the biggest automation opportunity>"\n'
        "}\n"
        "Return an empty findings array if no clear patterns exist. "
        "Do NOT add any text outside the JSON object."
    )
    user_prompt = (
        f"Team standup data for the last {window_days} days:\n\n"
        f"{aggregated_task_data}\n\n"
        f"Recurring blockers:\n{blocker_data}\n\n"
        "Identify all automation opportunities and return them as JSON."
    )
    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# Parse + validate LLM output
# ---------------------------------------------------------------------------

def _parse_and_validate(raw: str) -> dict:
    """Parse LLM JSON output and verify it contains required keys."""
    # Strip markdown code fences if present (some models wrap in ```json ... ```)
    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Remove first and last line if they are code fence markers
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("LLM response is not a JSON object")
    if "findings" not in data or not isinstance(data["findings"], list):
        raise ValueError("LLM response missing 'findings' list")
    if "summary" not in data:
        data["summary"] = ""

    # Validate each finding has required keys; drop malformed ones silently
    valid_findings = []
    for f in data["findings"]:
        if isinstance(f, dict) and "task_pattern" in f:
            # Ensure all expected keys have defaults if missing
            f.setdefault("frequency", 0)
            f.setdefault("affected_members", [])
            f.setdefault("source", "checkins")
            f.setdefault("suggested_tools", [])
            f.setdefault("reasoning", "")
            valid_findings.append(f)
    data["findings"] = valid_findings

    return data


# ---------------------------------------------------------------------------
# Provider: OpenAI
# ---------------------------------------------------------------------------

async def _call_openai(system_prompt: str, user_prompt: str) -> dict:
    if not _API_KEY:
        raise ValueError("LLM_API_KEY is not set — cannot call OpenAI")

    payload = {
        "model": _MODEL_OPENAI,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
    }
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code != 200:
        raise ValueError(f"OpenAI API error {resp.status_code}: {resp.text[:300]}")

    raw = resp.json()["choices"][0]["message"]["content"]
    return _parse_and_validate(raw)


# ---------------------------------------------------------------------------
# Provider: Anthropic
# ---------------------------------------------------------------------------

async def _call_anthropic(system_prompt: str, user_prompt: str) -> dict:
    if not _API_KEY:
        raise ValueError("LLM_API_KEY is not set — cannot call Anthropic")

    payload = {
        "model": _MODEL_ANTHROPIC,
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": _API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code != 200:
        raise ValueError(f"Anthropic API error {resp.status_code}: {resp.text[:300]}")

    raw = resp.json()["content"][0]["text"]
    return _parse_and_validate(raw)


# ---------------------------------------------------------------------------
# Provider: Gemini
# ---------------------------------------------------------------------------

async def _call_gemini(system_prompt: str, user_prompt: str) -> dict:
    if not _API_KEY:
        raise ValueError("LLM_API_KEY is not set — cannot call Gemini")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_MODEL_GEMINI}:generateContent?key={_API_KEY}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        resp = await client.post(url, headers={"Content-Type": "application/json"}, json=payload)
    if resp.status_code != 200:
        raise ValueError(f"Gemini API error {resp.status_code}: {resp.text[:300]}")

    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_and_validate(raw)


# ---------------------------------------------------------------------------
# Provider: Mock (default when LLM_PROVIDER is unset)
# ---------------------------------------------------------------------------

def _mock_response(window_days: int) -> dict:
    return {
        "findings": [
            {
                "task_pattern": "Manual status report generation",
                "frequency": 12,
                "affected_members": ["Sample Member"],
                "source": "checkins",
                "suggested_tools": ["Notion AI", "Zapier", "n8n"],
                "reasoning": (
                    "This task is mentioned repeatedly across check-ins and follows a "
                    "predictable pattern. Zapier can automate report aggregation while "
                    "Notion AI can draft the narrative."
                ),
            },
            {
                "task_pattern": "Deployment environment setup",
                "frequency": 7,
                "affected_members": ["Sample Member"],
                "source": "blockers",
                "suggested_tools": ["Docker", "GitHub Actions"],
                "reasoning": (
                    "Recurring environment setup blockers suggest a manual provisioning "
                    "step. Docker Compose and a CI/CD pipeline would eliminate this completely."
                ),
            },
        ],
        "summary": (
            f"Based on {window_days} days of data, your team has two strong automation candidates: "
            "reporting workflows and environment setup. "
            "(Mock data — set LLM_PROVIDER and LLM_API_KEY to enable real AI analysis.)"
        ),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def generate_automation_insights(
    aggregated_task_data: str,
    blocker_data: str,
    window_days: int,
) -> dict:
    """
    Call the configured LLM provider and return structured automation findings.

    Returns:
        {"findings": [...], "summary": "..."}

    Raises:
        ValueError: if the LLM returns unparseable output or an API error occurs.
    """
    provider = _PROVIDER

    if provider == "mock" or not provider:
        logger.info("LLM_PROVIDER=mock — returning mock automation insights")
        return _mock_response(window_days)

    system_prompt, user_prompt = _build_prompt(aggregated_task_data, blocker_data, window_days)
    logger.info("Calling LLM provider=%s model=%s", provider, _MODEL_OPENAI if provider == "openai" else _MODEL_ANTHROPIC)

    if provider == "openai":
        return await _call_openai(system_prompt, user_prompt)
    elif provider == "anthropic":
        return await _call_anthropic(system_prompt, user_prompt)
    elif provider == "gemini":
        return await _call_gemini(system_prompt, user_prompt)
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER='{provider}'. "
            "Valid values: openai, anthropic, gemini, mock"
        )


# ---------------------------------------------------------------------------
# Ai Task Radar — nested team/member/task prompt (single LLM call per team/week)
# ---------------------------------------------------------------------------

def _build_radar_prompt(team_name: str, members_payload: list[dict], window_days: int) -> tuple[str, str]:
    """
    members_payload: [
      {"user_id": "uuid|null", "name": "Alice", "phrases": ["rebuilt staging", "..."]}
    ]
    """
    system_prompt = (
        "You are an AI automation analyst reviewing a remote team's standup history. "
        "For each member, infer the concrete recurring tasks implied by their check-in phrases, "
        "score each task's automation potential on a 0-100 scale, and classify it into a tier:\n"
        "  P1 = high automation (>=80) — name specific tools and a ready-to-paste prompt.\n"
        "  P2 = medium automation (50-79) — give a general automation suggestion.\n"
        "  P3 = low automation (<50)    — give a short step-by-step workflow a human should follow.\n"
        "Also compute a member_score (0-100) as the weighted average of that member's task scores, "
        "and a team_score as the mean of member_scores.\n\n"
        "Respond ONLY with valid JSON matching this exact schema — no extra text outside the JSON:\n"
        "{\n"
        '  "team_score": <0-100 integer>,\n'
        '  "summary": "<2-3 sentence executive summary of the team\'s automation posture>",\n'
        '  "members": [\n'
        "    {\n"
        '      "user_id": "<echo back exactly, or null if unknown>",\n'
        '      "name": "<member name>",\n'
        '      "member_score": <0-100 integer>,\n'
        '      "tasks": [\n'
        "        {\n"
        '          "task_title": "<short title>",\n'
        '          "task_description": "<1-2 sentence description>",\n'
        '          "automation_score": <0-100 integer>,\n'
        '          "tier": "P1" | "P2" | "P3",\n'
        '          "mention_frequency": <integer — how many times this recurring pattern appears across the member\'s phrases in this window>,\n'
        '          "weekly_hours_saved": <number — realistic hours per week this member would get back if this task were automated>,\n'
        '          "suggested_tools": [ { "name": "<tool>", "prompt": "<ready prompt or null>" } ],\n'
        '          "suggested_workflow": "<step-by-step text, only for P3; otherwise null>",\n'
        '          "general_suggestion": "<one-paragraph suggestion, only for P2; otherwise null>"\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules:\n"
        "- If a member has no actionable tasks, include them with an empty tasks array and member_score=0.\n"
        "- suggested_tools MUST be non-empty only when tier='P1'; for P2/P3 it can be an empty array.\n"
        "- mention_frequency must be >= 1 and reflect how often the pattern actually recurs in the phrases.\n"
        "- weekly_hours_saved must be a realistic non-negative number (typically 0.2-8.0 per task).\n"
        "- Never invent members that are not in the input.\n"
        "- Return the same user_id string you received (do not modify UUIDs)."
    )

    # Inline the payload as compact JSON so the model can see the full mapping cleanly.
    payload_json = json.dumps(
        {"team_name": team_name, "window_days": window_days, "members": members_payload},
        ensure_ascii=False,
    )
    user_prompt = (
        f"Team: {team_name}\n"
        f"Window: last {window_days} days\n\n"
        f"Members and their aggregated standup phrases (JSON):\n{payload_json}\n\n"
        "Return the nested JSON described in the system prompt."
    )
    return system_prompt, user_prompt


def _parse_and_validate_radar(raw: str) -> dict:
    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("LLM response is not a JSON object")

    data.setdefault("team_score", 0)
    data.setdefault("summary", "")
    if not isinstance(data.get("members"), list):
        raise ValueError("LLM response missing 'members' list")

    clean_members = []
    for m in data["members"]:
        if not isinstance(m, dict) or "name" not in m:
            continue
        m.setdefault("user_id", None)
        m.setdefault("member_score", 0)
        m.setdefault("tasks", [])
        clean_tasks = []
        for t in m["tasks"]:
            if not isinstance(t, dict) or "task_title" not in t:
                continue
            t.setdefault("task_description", "")
            t.setdefault("automation_score", 0)
            t.setdefault("mention_frequency", 0)
            t.setdefault("weekly_hours_saved", 0)
            tier = str(t.get("tier") or "P3").upper()
            if tier not in ("P1", "P2", "P3"):
                tier = "P3"
            t["tier"] = tier
            tools = t.get("suggested_tools") or []
            if not isinstance(tools, list):
                tools = []
            norm_tools = []
            for tool in tools:
                if isinstance(tool, dict) and tool.get("name"):
                    norm_tools.append({"name": tool["name"], "prompt": tool.get("prompt")})
                elif isinstance(tool, str) and tool.strip():
                    norm_tools.append({"name": tool.strip(), "prompt": None})
            t["suggested_tools"] = norm_tools
            t.setdefault("suggested_workflow", None)
            t.setdefault("general_suggestion", None)
            clean_tasks.append(t)
        m["tasks"] = clean_tasks
        clean_members.append(m)
    data["members"] = clean_members

    return data


async def _call_openai_radar(system_prompt: str, user_prompt: str) -> dict:
    if not _API_KEY:
        raise ValueError("LLM_API_KEY is not set — cannot call OpenAI")
    payload = {
        "model": _MODEL_OPENAI,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
    }
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {_API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code != 200:
        raise ValueError(f"OpenAI API error {resp.status_code}: {resp.text[:300]}")
    raw = resp.json()["choices"][0]["message"]["content"]
    return _parse_and_validate_radar(raw)


async def _call_anthropic_radar(system_prompt: str, user_prompt: str) -> dict:
    if not _API_KEY:
        raise ValueError("LLM_API_KEY is not set — cannot call Anthropic")
    payload = {
        "model": _MODEL_ANTHROPIC,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": _API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code != 200:
        raise ValueError(f"Anthropic API error {resp.status_code}: {resp.text[:300]}")
    raw = resp.json()["content"][0]["text"]
    return _parse_and_validate_radar(raw)


async def _call_gemini_radar(system_prompt: str, user_prompt: str) -> dict:
    if not _API_KEY:
        raise ValueError("LLM_API_KEY is not set — cannot call Gemini")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_MODEL_GEMINI}:generateContent?key={_API_KEY}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        resp = await client.post(url, headers={"Content-Type": "application/json"}, json=payload)
    if resp.status_code != 200:
        raise ValueError(f"Gemini API error {resp.status_code}: {resp.text[:300]}")

    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_and_validate_radar(raw)


def _mock_radar_response(team_name: str, members_payload: list[dict], window_days: int) -> dict:
    members_out = []
    for idx, m in enumerate(members_payload):
        # deterministic-ish scoring so the UI looks interesting across bands
        base = 30 + (idx * 19) % 65
        tasks = [
            {
                "task_title": "Draft weekly status report",
                "task_description": "Aggregating per-sprint status updates from multiple docs into a single summary.",
                "automation_score": max(80, base),
                "tier": "P1",
                "suggested_tools": [
                    {"name": "Notion AI", "prompt": "Summarize the following weekly notes into an executive status: {{notes}}"},
                    {"name": "Zapier", "prompt": "Trigger a weekly digest when my Google Doc is updated."},
                ],
                "suggested_workflow": None,
                "general_suggestion": None,
            },
            {
                "task_title": "Respond to recurring customer follow-ups",
                "task_description": "Answering the same onboarding questions by hand.",
                "automation_score": 60,
                "tier": "P2",
                "suggested_tools": [],
                "suggested_workflow": None,
                "general_suggestion": "A shared FAQ macro library plus an email-templating tool would cut this in half.",
            },
            {
                "task_title": "Manual deployment smoke checks",
                "task_description": "Clicking through staging to verify post-deploy.",
                "automation_score": 35,
                "tier": "P3",
                "suggested_tools": [],
                "suggested_workflow": "1) Export checklist to Notion. 2) Assign rotating owner. 3) Add screenshot step. 4) Log outcome in shared sheet.",
                "general_suggestion": None,
            },
        ]
        member_score = int(sum(t["automation_score"] for t in tasks) / len(tasks))
        members_out.append({
            "user_id": m.get("user_id"),
            "name": m.get("name") or f"Member {idx+1}",
            "member_score": member_score,
            "tasks": tasks,
        })

    team_score = int(sum(m["member_score"] for m in members_out) / max(1, len(members_out)))
    return {
        "team_score": team_score,
        "summary": (
            f"Over the last {window_days} days {team_name} shows clear P1 opportunities around "
            "reporting and onboarding replies, with a few P3 manual QA steps worth formalizing. "
            "(Mock data — set LLM_PROVIDER and LLM_API_KEY to enable real AI analysis.)"
        ),
        "members": members_out,
    }


async def generate_ai_task_radar(
    team_name: str,
    members_payload: list[dict],
    window_days: int,
) -> dict:
    """
    Single LLM call producing the full nested Ai Task Radar structure.

    members_payload: [{"user_id": str|None, "name": str, "phrases": list[str]}]
    Returns:
        {"team_score": int, "summary": str, "members": [ ... nested tasks ... ]}
    """
    provider = _PROVIDER
    if provider == "mock" or not provider:
        logger.info("LLM_PROVIDER=mock — returning mock Ai Task Radar output")
        return _mock_radar_response(team_name, members_payload, window_days)

    system_prompt, user_prompt = _build_radar_prompt(team_name, members_payload, window_days)
    logger.info("Calling radar LLM provider=%s", provider)

    if provider == "openai":
        return await _call_openai_radar(system_prompt, user_prompt)
    elif provider == "anthropic":
        return await _call_anthropic_radar(system_prompt, user_prompt)
    elif provider == "gemini":
        return await _call_gemini_radar(system_prompt, user_prompt)
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER='{provider}'. Valid values: openai, anthropic, gemini, mock"
        )
