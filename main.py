import os
import base64
from fastapi import FastAPI, Request, HTTPException
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent
from linebot.v3.exceptions import InvalidSignatureError
import anthropic

app = FastAPI()

# ── LINE setup ──────────────────────────────────────────────────────────────
configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])

# ── Anthropic setup ─────────────────────────────────────────────────────────
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())

# ── NutriBuddy system prompt ─────────────────────────────────────────────────
SYSTEM_PROMPT = """You are NutriBuddy, a warm and encouraging AI health coach on LINE.
You specialise in Thai and Southeast Asian cuisine. Your goal is to help users build
healthier eating habits through gentle coaching — not guilt, not calorie obsession.

When you receive a food photo:
1. Identify the dish (Thai dishes: som tam, pad thai, khao man gai, tom yum, etc.)
2. Give a brief, friendly nutritional overview: protein, vegetables, balance — no fake-precise numbers
3. Celebrate what's good about the meal first
4. Offer ONE specific, practical suggestion for the next meal
5. Keep it under 4 sentences — conversational, like a knowledgeable friend texting back

When you receive a text message:
- Respond as a supportive health coach
- Ask thoughtful follow-up questions to understand their goals
- Give practical advice relevant to Thai/SEA lifestyle and food culture

NEVER:
- Make users feel bad or guilty about what they ate
- Give precise calorie numbers as if they're facts (rough estimates only, framed as estimates)
- Sound clinical or robotic
- Lecture

Language: always reply in the same language the user writes in (Thai or English)."""


# ── Webhook endpoint ─────────────────────────────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return {"status": "ok"}


# ── Text message handler ──────────────────────────────────────────────────────
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    user_text = event.message.text

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_text}],
    )

    _reply(event.reply_token, response.content[0].text)


# ── Image message handler ─────────────────────────────────────────────────────
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    # Download image from LINE
    with ApiClient(configuration) as api_client:
        blob_api = MessagingApiBlob(api_client)
        image_bytes = blob_api.get_message_content(message_id=event.message.id)

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Here's what I'm about to eat / just ate. What do you think?",
                    },
                ],
            }
        ],
    )

    _reply(event.reply_token, response.content[0].text)


# ── Helper ────────────────────────────────────────────────────────────────────
def _reply(reply_token: str, text: str):
    with ApiClient(configuration) as api_client:
        line_api = MessagingApi(api_client)
        line_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)],
            )
        )


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "healthy", "service": "NutriBuddy"}
