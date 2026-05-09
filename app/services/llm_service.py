"""
LLM service — provider-agnostic. Reads LLM_PROVIDER env var: openai | anthropic | mock.
Falls back to mock when provider is unset — no external calls, no crashes.
"""
import json
import structlog

import httpx

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

_settings = get_settings()
_PROVIDER = _settings.llm_provider
_API_KEY = _settings.llm_api_key
_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-haiku-20240307",
    "gemini": "gemini-1.5-flash",
}
_MODEL = _settings.llm_model or _DEFAULT_MODELS.get(_PROVIDER, "gemini-1.5-flash")
_LLM_TIMEOUT = 60.0


# ── Prompt builders ───────────────────────────────────────────────────────────

def _build_prompt(aggregated_task_data: str, blocker_data: str, window_days: int) -> tuple[str, str]:
    system_prompt = (
        "You are an AI analyst reviewing remote team standup data. "
        "Your job is to identify repetitive manual tasks that could be automated. "
        "Respond ONLY with valid JSON matching this exact schema — no extra text outside the JSON:\n"
        '{"findings": [{"task_pattern": "...", "frequency": 0, "affected_members": [], '
        '"source": "checkins|blockers|both", "suggested_tools": [], "reasoning": "..."}], '
        '"summary": "..."}\n'
        "Return an empty findings array if no clear patterns exist."
    )
    user_prompt = (
        f"Team standup data for the last {window_days} days:\n\n"
        f"{aggregated_task_data}\n\n"
        f"Recurring blockers:\n{blocker_data}\n\n"
        "Identify all automation opportunities and return them as JSON."
    )
    return system_prompt, user_prompt


def _build_radar_prompt(team_name: str, members_payload: list[dict], window_days: int) -> tuple[str, str]:
    system_prompt = (
        "You are an AI automation analyst reviewing a remote team's standup history. "
        "For each member, infer the concrete recurring tasks implied by their check-in phrases, "
        "score each task's automation potential on a 0-100 scale, and classify it into a tier:\n"
        "  P1 = high automation (>=80) — name specific tools and a ready-to-paste prompt.\n"
        "  P2 = medium automation (50-79) — give a general automation suggestion.\n"
        "  P3 = low automation (<50) — give a short step-by-step workflow a human should follow.\n"
        "Compute member_score as weighted average of task scores; team_score as mean of member_scores.\n\n"
        "Respond ONLY with valid JSON matching this exact schema — no extra text:\n"
        '{"team_score": 0, "summary": "...", "members": [{"user_id": "...", "name": "...", '
        '"member_score": 0, "tasks": [{"task_title": "...", "task_description": "...", '
        '"automation_score": 0, "tier": "P1|P2|P3", "suggested_tools": [{"name": "...", "prompt": "..."}], '
        '"suggested_workflow": null, "general_suggestion": null}]}]}'
    )
    payload_json = json.dumps(
        {"team_name": team_name, "window_days": window_days, "members": members_payload},
        ensure_ascii=False,
    )
    user_prompt = (
        f"Team: {team_name}\nWindow: last {window_days} days\n\n"
        f"Members and standup phrases (JSON):\n{payload_json}\n\n"
        "Return the nested JSON described in the system prompt."
    )
    return system_prompt, user_prompt


# ── Parsers ───────────────────────────────────────────────────────────────────

def _strip_code_fences(raw: str) -> str:
    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:] if lines[0].startswith("```") else lines
        lines = lines[:-1] if lines and lines[-1].strip() == "```" else lines
        return "\n".join(lines)
    return stripped


def _parse_findings(raw: str) -> dict:
    try:
        data = json.loads(_strip_code_fences(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError("LLM response missing 'findings' list")
    data.setdefault("summary", "")
    valid = []
    for f in data["findings"]:
        if isinstance(f, dict) and "task_pattern" in f:
            f.setdefault("frequency", 0)
            f.setdefault("affected_members", [])
            f.setdefault("source", "checkins")
            f.setdefault("suggested_tools", [])
            f.setdefault("reasoning", "")
            valid.append(f)
    data["findings"] = valid
    return data


def _parse_radar(raw: str) -> dict:
    try:
        data = json.loads(_strip_code_fences(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("members"), list):
        raise ValueError("LLM response missing 'members' list")
    data.setdefault("team_score", 0)
    data.setdefault("summary", "")
    clean_members = []
    for m in data["members"]:
        if not isinstance(m, dict) or "name" not in m:
            continue
        m.setdefault("user_id", None)
        m.setdefault("member_score", 0)
        clean_tasks = []
        for t in (m.get("tasks") or []):
            if not isinstance(t, dict) or "task_title" not in t:
                continue
            t.setdefault("task_description", "")
            t.setdefault("automation_score", 0)
            tier = str(t.get("tier") or "P3").upper()
            t["tier"] = tier if tier in ("P1", "P2", "P3") else "P3"
            norm_tools = []
            for tool in (t.get("suggested_tools") or []):
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


# ── Provider calls ────────────────────────────────────────────────────────────

async def _call_openai(system: str, user: str, *, radar: bool = False) -> dict:
    if not _API_KEY:
        raise ValueError("LLM_API_KEY is not set — cannot call OpenAI")
    payload = {
        "model": _MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
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
    return _parse_radar(raw) if radar else _parse_findings(raw)


async def _call_anthropic(system: str, user: str, *, radar: bool = False) -> dict:
    if not _API_KEY:
        raise ValueError("LLM_API_KEY is not set — cannot call Anthropic")
    payload = {
        "model": _MODEL,
        "max_tokens": 4096 if radar else 1024,
        "system": system,
        "messages": [{"role": "user", "content": user}],
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
    return _parse_radar(raw) if radar else _parse_findings(raw)


async def _call_gemini(system: str, user: str, *, radar: bool = False) -> dict:
    if not _API_KEY:
        raise ValueError("LLM_API_KEY is not set — cannot call Gemini")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{_MODEL}"
        f":generateContent?key={_API_KEY}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.3,
        },
    }
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        resp = await client.post(url, json=payload)
    if resp.status_code != 200:
        raise ValueError(f"Gemini API error {resp.status_code}: {resp.text[:300]}")
    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_radar(raw) if radar else _parse_findings(raw)


# ── Mock responses ────────────────────────────────────────────────────────────

def _mock_findings(window_days: int) -> dict:
    return {
        "findings": [
            {
                "task_pattern": "Manual status report generation",
                "frequency": 12,
                "affected_members": ["Sample Member"],
                "source": "checkins",
                "suggested_tools": ["Notion AI", "Zapier"],
                "reasoning": "Mentioned repeatedly; predictable pattern suits Zapier + Notion AI.",
            },
        ],
        "summary": (
            f"Based on {window_days} days of data, your team has automation candidates. "
            "(Mock — set LLM_PROVIDER + LLM_API_KEY to enable real analysis.)"
        ),
    }


def _mock_radar(team_name: str, members_payload: list[dict], window_days: int) -> dict:
    members_out = []
    for idx, m in enumerate(members_payload):
        base = 30 + (idx * 19) % 65
        tasks = [
            {
                "task_title": "Draft weekly status report",
                "task_description": "Aggregating sprint notes into a single summary.",
                "automation_score": max(80, base),
                "tier": "P1",
                "suggested_tools": [{"name": "Notion AI", "prompt": "Summarize: {{notes}}"}],
                "suggested_workflow": None,
                "general_suggestion": None,
            },
            {
                "task_title": "Respond to recurring customer follow-ups",
                "task_description": "Answering the same onboarding questions manually.",
                "automation_score": 60,
                "tier": "P2",
                "suggested_tools": [],
                "suggested_workflow": None,
                "general_suggestion": "A shared FAQ macro library would cut this in half.",
            },
        ]
        member_score = int(sum(t["automation_score"] for t in tasks) / len(tasks))
        members_out.append({"user_id": m.get("user_id"), "name": m.get("name") or f"Member {idx+1}", "member_score": member_score, "tasks": tasks})

    team_score = int(sum(m["member_score"] for m in members_out) / max(1, len(members_out)))
    return {
        "team_score": team_score,
        "summary": f"Over {window_days} days {team_name} shows P1 opportunities. (Mock data.)",
        "members": members_out,
    }


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_automation_insights(
    aggregated_task_data: str,
    blocker_data: str,
    window_days: int,
) -> dict:
    if _PROVIDER == "mock":
        logger.info("llm.mock", mode="findings")
        return _mock_findings(window_days)
    system, user = _build_prompt(aggregated_task_data, blocker_data, window_days)
    logger.info("llm.call", provider=_PROVIDER, mode="findings")
    if _PROVIDER == "openai":
        return await _call_openai(system, user)
    elif _PROVIDER == "anthropic":
        return await _call_anthropic(system, user)
    elif _PROVIDER == "gemini":
        return await _call_gemini(system, user)
    raise ValueError(f"Unknown LLM_PROVIDER='{_PROVIDER}'")


async def generate_ai_task_radar(
    team_name: str,
    members_payload: list[dict],
    window_days: int,
) -> dict:
    if _PROVIDER == "mock":
        logger.info("llm.mock", mode="radar")
        return _mock_radar(team_name, members_payload, window_days)
    system, user = _build_radar_prompt(team_name, members_payload, window_days)
    logger.info("llm.call", provider=_PROVIDER, mode="radar")
    if _PROVIDER == "openai":
        return await _call_openai(system, user, radar=True)
    elif _PROVIDER == "anthropic":
        return await _call_anthropic(system, user, radar=True)
    elif _PROVIDER == "gemini":
        return await _call_gemini(system, user, radar=True)
    raise ValueError(f"Unknown LLM_PROVIDER='{_PROVIDER}'")
