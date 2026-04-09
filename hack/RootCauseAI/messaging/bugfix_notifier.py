# bugfix_notifier.py
# Reads bug-fix result JSON + hint file, sends formatted message to Telegram.

import os
import sys
import json
import requests
from pathlib import Path
from dotenv import load_dotenv
from messaging.telegram_manager import TelegramManager
from html import escape
load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
class BugFixMessageBuilder:
    def __init__(self):
        self.root = ROOT
        self.hints_dir = self.root / "artifacts" / "hints"
        self.fixes_dir = self.root / "artifacts" / "bug_fixes"

    def _get_latest(self, folder: Path, prefix: str) -> Path:
        files = sorted(folder.glob(f"{prefix}*.json"),
                       key=lambda p: p.stat().st_mtime,
                       reverse=True)
        if not files:
            raise RuntimeError(f"No files found in folder: {folder}")
        return files[0]

    def load_latest_hint(self) -> tuple[Path, dict]:
        path = self._get_latest(self.hints_dir, "hint_")
        data = json.loads(path.read_text(encoding="utf-8"))
        return path, data

    def load_latest_fix(self) -> tuple[Path, dict]:
        path = self._get_latest(self.fixes_dir, "fix_")
        data = json.loads(path.read_text(encoding="utf-8"))
        return path, data

    def build_message(self) -> str:
        # Extract values
        hint_path, hint_data = self.load_latest_hint()
        fix_path, fix_data = self.load_latest_fix()
        # Check for user guidance
        user_guidance = hint_data.get("user_guidance")
        guidance_section = ""
        if user_guidance:
            guidance_section = f"<b>👤 Your Guidance:</b>\n<i>{escape(user_guidance)}</i>\n\n"

        if fix_data.get("type") == "partial_analysis":
            context_list = fix_data.get("context_gathered", [])
            reason = fix_data.get("reason", "")

            msg = (
                "<b>⚠️ Partial Analysis</b>\n\n"
                f"{guidance_section}"
                f"<b>🧩 Hint:</b>\n{escape(str(hint_data.get('cause', 'Unknown')))}\n\n"
                f"<b>❓ Status:</b>\n{escape(reason)}\n\n"
                f"<b>📂 Context gathered:</b>\n"
            )
            for ctx in context_list[:5]:
                msg += f"• <code>{escape(ctx[:100])}</code>\n"

            msg += "\n<i>The agent couldn't complete the analysis. You can try 'Suggest' to guide it.</i>"
            return msg, len(msg) > 4000

        if fix_data.get("type") == "analysis":
            analysis_text = fix_data.get("text", "")
            msg = (
                "<b>🔍 AI Analysis</b>\n\n"
                f"{guidance_section}"
                f"<b>🧩 Hint:</b>\n{escape(str(hint_data.get('cause', 'Unknown')))}\n\n"
                f"<b>📝 Analysis:</b>\n{escape(analysis_text[:2000])}\n\n"
                "<i>The AI could not provide a specific fix, but shared this analysis.</i>"
            )
            return msg, len(msg) > 4000
        hint_first_line = str(hint_data.get("cause", "Unknown")).split("\n")[0]
        reason = fix_data.get("reason", "No reason provided.")
        functions = fix_data.get("functions_to_edit", [])

        # --- Build CURRENT vs SUGGESTED from a diff-like patch_suggestion ---
        raw_patch = (fix_data.get("patch_suggestion") or "").replace("```diff", "").replace("```", "").strip()
        lines = raw_patch.splitlines()

        current_lines: list[str] = []
        suggested_lines: list[str] = []

        for line in lines:
            if line.startswith("+"):
                # new-only line (appears only in suggested code)
                suggested_lines.append(line[1:])
            elif line.startswith("-"):
                # removed line (appears only in current code)
                current_lines.append(line[1:])
            else:
                # context / unchanged line → appears in both current and suggested
                current_lines.append(line)
                suggested_lines.append(line)

        current_block = "\n".join(current_lines).strip()
        suggested_block = "\n".join(suggested_lines).strip()

        current_block = escape(current_block)
        suggested_block = escape(suggested_block)

        # Format function list
        fn_list = (
            "\n".join(f"• <code>{escape(f)}</code>" for f in functions)
            if functions else "<i>None</i>"
        )

        # Build HTML message
        msg = (
            "<b>🚨 Bug Fix Summary</b>\n\n"
            f"{guidance_section}"
            f"<b>🧩 Hint:</b>\n{escape(hint_first_line)}\n\n"
            f"<b>📂 Functions to Edit:</b>\n{fn_list}\n\n"
            f"<b>💡 Reason:</b>\n{escape(reason)}\n\n"
            f"<b>🧠 Patch Suggestion:</b>\n"
            f"<b>Current code:</b>\n<pre>{current_block or '(not available)'}</pre>\n\n"
            f"<b>Suggested fix:</b>\n<pre>{suggested_block}</pre>\n\n"
        )

        is_long = len(msg) > 4000
        return msg, is_long

    
def main():
    builder = BugFixMessageBuilder()
    message = builder.build_message()
    tm = TelegramManager()
    tm.send_bugfix_message(message) 
    user_choice = tm.wait_for_user_response()
    print(user_choice)

if __name__ == "__main__":
    main()
