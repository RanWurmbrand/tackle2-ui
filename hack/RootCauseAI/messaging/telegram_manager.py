import os
import json
import time
import requests
from pathlib import Path
import io
class TelegramManager:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not self.token or not self.chat_id:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

        self.api_url = f"https://api.telegram.org/bot{self.token}"

    # ----------------------------
    # Send message with 3 buttons
    # ----------------------------
    def send_bugfix_message(self, text: str):
        r = requests.post(f"{self.api_url}/sendMessage", data={
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps({
               "inline_keyboard": [
                    [
                        {"text": "🔄 Rerun", "callback_data": "rerun"},
                        {"text": "🛠 Fix & Rerun", "callback_data": "fix_and_rerun"},
                    ],
                    [
                        {"text": "💬 Suggest", "callback_data": "suggest"},
                        {"text": "⛔ Terminate", "callback_data": "terminate"}
                    ]
                ]
            })
        })

        if not r.ok:
            raise RuntimeError(f"Telegram error: {r.text}")
        self.last_message_id = r.json().get("result", {}).get("message_id")
    # ----------------------------
    # Wait for user clicking a button
    # ----------------------------
    def wait_for_user_response(self):
        res = requests.get(f"{self.api_url}/getUpdates")
        updates = res.json().get("result", [])
        last_update_id = updates[-1]["update_id"] + 1 if updates else None
        while True:
            res = requests.get(
                f"{self.api_url}/getUpdates",
                params={"offset": last_update_id, "timeout": 10}
            )

            if not res.ok:
                time.sleep(1)
                continue

            updates = res.json().get("result", [])
            for update in updates:
                last_update_id = update["update_id"] + 1

                if "callback_query" in update:
                    cb = update["callback_query"]
                    action = cb["data"]
                    message_id = cb["message"]["message_id"]

                    requests.post(f"{self.api_url}/editMessageReplyMarkup", data={
                        "chat_id": self.chat_id,
                        "message_id": message_id,
                        "reply_markup": json.dumps({"inline_keyboard": []})
                    })

                    requests.post(f"{self.api_url}/answerCallbackQuery", data={
                        "callback_query_id": cb["id"]
                    })

                    return action

            time.sleep(0.5)

    # ----------------------------
    # Combine send + wait
    # ----------------------------
    def notify_and_wait(self, text: str) -> str:
        self.send_bugfix_message(text)
        return self.wait_for_user_response()
    
    def send_message(self, text: str):
        r = requests.post(f"{self.api_url}/sendMessage", data={
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        })
        if not r.ok:
            raise RuntimeError(f"Telegram error: {r.text}")
    def send_document(self, content: str, filename: str, caption: str = None):
        """Send a text string as a file attachment."""
        file_obj = io.BytesIO(content.encode('utf-8'))
        file_obj.name = filename
        
        data = {"chat_id": self.chat_id}
        if caption:
            data["caption"] = caption[:1024]  # Telegram caption limit
            data["parse_mode"] = "HTML"
        
        r = requests.post(
            f"{self.api_url}/sendDocument",
            data=data,
            files={"document": file_obj}
        )
        
        if not r.ok:
            raise RuntimeError(f"Telegram error: {r.text}")    
    def wait_for_text_message(self) -> str:
        """Wait for user to send a text message"""
        res = requests.get(f"{self.api_url}/getUpdates")
        updates = res.json().get("result", [])
        last_update_id = updates[-1]["update_id"] + 1 if updates else None
        
        while True:
            res = requests.get(
                f"{self.api_url}/getUpdates",
                params={"offset": last_update_id, "timeout": 30}
            )
            
            if not res.ok:
                time.sleep(1)
                continue
            
            updates = res.json().get("result", [])
            for update in updates:
                last_update_id = update["update_id"] + 1
                
                if "message" in update and "text" in update["message"]:
                    return update["message"]["text"]
            
            time.sleep(0.5)
