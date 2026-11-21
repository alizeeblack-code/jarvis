"""Utility functions for worker: question extraction, tool input summary"""

import json
import re


def finalize_question(questions: list, q: dict):
    """Finalize a question: add type and auto-generate choices if needed"""
    if q["choices"]:
        q["type"] = "choice"
    else:
        text = q["question"]
        yn_patterns = ["是否", "是不是", "要不要", "需不需要",
                        "合适", "可以吗", "好吗", "对吗",
                        "should", "do we", "is it", "can we"]
        is_yn = any(p in text.lower() for p in yn_patterns)
        if is_yn:
            q["choices"] = ["是", "否"]
            q["type"] = "choice"
        else:
            q["type"] = "text"
    questions.append(q)


def extract_questions(plan_text: str) -> list:
    """Extract questions and choices from plan text.

    Only extracts questions from "问题" or "澄清" sections to avoid
    picking up rhetorical questions from descriptive text.

    Returns: [{"question": "...", "choices": [...], "type": "choice|text"}]
    """
    if not plan_text:
        return []

    # Find the questions/clarification section
    q_section = _extract_question_section(plan_text)
    if not q_section:
        return []

    lines = q_section.split("\n")
    questions, current_q, non_choice_lines = [], None, 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_q and non_choice_lines > 0:
                finalize_question(questions, current_q)
                current_q = None
            continue

        # Stop at next ## section
        if stripped.startswith("## "):
            break

        is_q = ((stripped.endswith("？") or stripped.endswith("?"))
                and len(stripped) > 5)
        if is_q and stripped.startswith("#"):
            is_q = False

        if is_q:
            if current_q:
                finalize_question(questions, current_q)
            current_q = {"question": stripped, "choices": []}
            non_choice_lines = 0
        elif current_q is not None:
            m = re.match(
                r'^[\-\*•]\s+(.+)|^[a-zA-Z][\.\)]\s*(.+)', stripped)
            if m:
                choice = (m.group(1) or m.group(2) or "").strip()
                if choice and len(choice) > 2:
                    current_q["choices"].append(choice)
            else:
                non_choice_lines += 1
                if non_choice_lines >= 2 and not current_q["choices"]:
                    finalize_question(questions, current_q)
                    current_q = None

    if current_q:
        finalize_question(questions, current_q)
    return questions[:10]


def _extract_question_section(text: str) -> str:
    """Find the questions/clarification section in plan text."""
    patterns = [
        r'##\s*(?:问题|澄清|Questions|Clarif)',
        r'##\s*\d+\.\s*(?:问题|澄清)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            # Return from this header to next ## or end
            start = m.end()
            next_h2 = re.search(r'\n##\s', text[start:])
            end = start + next_h2.start() if next_h2 else len(text)
            return text[start:end]
    return ""


def summarize_tool_input(tool_name: str, tool_input: dict) -> str:
    """Create a short readable summary of tool input"""
    if not tool_input:
        return ""
    if tool_name == "Bash":
        return tool_input.get("command", "")[:300]
    if tool_name in ("Read", "Write"):
        fp = tool_input.get("file_path", "")
        offset = tool_input.get("offset", "")
        extra = f" (offset={offset}, limit={tool_input.get('limit','')})" if offset else ""
        return f"{fp}{extra}"[:300]
    if tool_name == "Edit":
        fp = tool_input.get("file_path", "")
        old = tool_input.get("old_string", "")[:80]
        return f"{fp} ({old}...)"[:300]
    if tool_name in ("Grep", "Glob"):
        return tool_input.get("pattern", "")[:200]
    for k, v in tool_input.items():
        return f"{k}={str(v)[:200]}"[:300]
    return ""


def extract_plan_json(plan_text: str) -> dict:
    """Extract structured plan from JSON code block in plan text.

    Looks for ```json ... ``` block and parses it.
    Returns dict with {summary, subtasks, questions, risks} or None.
    """
    if not plan_text:
        return None
    m = re.search(r'```json\s*\n(.+?)\n\s*```', plan_text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None

    # Normalize subtasks
    subtasks = []
    for i, st in enumerate(data.get("subtasks", [])):
        subtasks.append({
            "index": i + 1,
            "title": st.get("title", ""),
            "description": st.get("description", ""),
            "files": st.get("files", []),
            "depends_on": st.get("depends_on", []),
        })

    # Normalize questions
    questions = []
    for q in data.get("questions", []):
        question_text = q if isinstance(q, str) else q.get("question", "")
        suggestion = q.get("suggestion", "") if isinstance(q, dict) else ""
        choices = [suggestion] if suggestion else []
        questions.append({
            "question": question_text,
            "choices": choices,
            "type": "choice" if choices else "text",
        })

    return {
        "summary": data.get("summary", ""),
        "subtasks": subtasks,
        "questions": questions,
        "risks": data.get("risks", []),
    }


def extract_subtasks(plan_text: str) -> list:
    """Extract structured sub-tasks from plan content.

    Parses markdown sections like:
      ### 子任务 1: Title
      - **描述**: Do something
      - **文件**: a.py, b.js
      - **依赖**: 无 / 子任务 2

    Returns list of dicts:
      [{"title": "...", "description": "...", "files": [...],
        "depends_on": None|int, "index": 1}, ...]
    """
    subtasks = []
    # Try multiple heading formats in priority order:
    patterns = [
        # Format 1: ### 子任务 1: Title / #### 子任务 1-1: Title
        re.compile(
            r'^#{3,4}\s*(?:子任务|sub-?task)?\s*'
            r'(\d+(?:[-\.]\d+)?)'
            r'[.:：]\s*(.+)',
            re.IGNORECASE | re.MULTILINE),
        # Format 2: **子任务 1.1：Title** (bold inline format)
        re.compile(
            r'^\*\*(?:子任务|sub-?task)\s*'
            r'(\d+(?:[-\.]\d+)?)'
            r'[.:：]\s*(.+?)\*\*',
            re.IGNORECASE | re.MULTILINE),
    ]

    matches = []
    for pattern in patterns:
        matches = list(pattern.finditer(plan_text))
        if matches:
            break
    if not matches:
        return []

    for i, m in enumerate(matches):
        raw_idx = m.group(1)
        # Normalize index: "1-1" → sequential counter
        idx = i + 1
        title = m.group(2).strip()
        # Extract content until next heading at same or higher level or end
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(plan_text)
        body = plan_text[start:end]

        # Parse fields from body
        desc = (_extract_field(body, "描述|description")
                or _extract_field(body, "改动|changes|内容")
                or body.strip()[:200])
        files_str = _extract_field(body, "文件|files|涉及文件")
        depends_str = _extract_field(body, "依赖|depends|dependency")

        files = [f.strip().strip('`') for f in re.split(r'[,，、]', files_str)
                 if f.strip().strip('`')] if files_str else []

        # Parse dependency: "无" / "子任务 2" / "Sub-task 1, 3"
        depends_on = []
        if depends_str and depends_str not in ("无", "none", "None", "无依赖", "-"):
            dep_nums = re.findall(r'(\d+)', depends_str)
            depends_on = [int(n) for n in dep_nums]

        subtasks.append({
            "index": idx,
            "title": title,
            "description": desc or title,
            "files": files,
            "depends_on": depends_on,
        })

    return subtasks


def _extract_field(body: str, field_pattern: str) -> str:
    """Extract field: value from markdown body.
    Supports: - **field**: value  /  - field：value  /  - field: value"""
    patterns = [
        rf'[-*]\s*\*\*(?:{field_pattern})\*\*[：:]\s*(.+)',
        rf'[-*]\s*(?:{field_pattern})[：:]\s*(.+)',
    ]
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""
