import os
import json
import asyncio
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

import traceback

# 1. 필수 재료 세팅 (보안 밀폐 용기 오픈)
load_dotenv()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("주방에 불이 안 들어오네요! .env 파일에 API 키를 확인해 주세요.")

# 2. 제미나이 불 조절 및 성격(양념) 부여
genai.configure(api_key=GEMINI_API_KEY)

hacker_persona = """
너는 세상을 비웃는 시니컬한 천재 해커야. 
말투는 차갑고 직설적이며, 불필요하고 친절한 인사는 절대 하지 마. 
질문이 수준 낮으면 가볍게 핀잔을 주되, 코딩이나 시스템 아키텍처, 트레이딩에 대한 질문에는 소름 돋을 정도로 완벽하고 날카로운 해결책을 제시해. 
대답은 간결하게 핵심만 말하고, 가끔 '루저', '백도어', '패킷' 같은 단어를 자연스럽게 섞어 써.
대화 상대방의 이름을 알면 가끔씩 언급하며 무시하는 태도를 보여줘.
"""

# 현시점 최신 모델 사용
model = genai.GenerativeModel(
    model_name='gemini-3-flash-preview',
    system_instruction=hacker_persona
)

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

# 3. 식재료 보관함 (Markdown DB 읽기/쓰기)
def get_log_path(user_id):
    return os.path.join(LOG_DIR, f"{user_id}.md")

def load_history(user_id):
    path = get_log_path(user_id)
    if not os.path.exists(path):
        return []
    
    history = []
    current_role = None
    current_parts = []
    
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith("### User:"):
                if current_role:
                    history.append({"role": current_role, "parts": ["\n".join(current_parts).strip()]})
                current_role = "user"
                current_parts = []
            elif line.startswith("### Model:"):
                if current_role:
                    history.append({"role": current_role, "parts": ["\n".join(current_parts).strip()]})
                current_role = "model"
                current_parts = []
            else:
                if current_role:
                    current_parts.append(line.strip())
        
        if current_role:
            history.append({"role": current_role, "parts": ["\n".join(current_parts).strip()]})
            
    return history

def save_history(user_id, user_msg, model_msg):
    path = get_log_path(user_id)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(f"### User:\n{user_msg}\n\n")
        f.write(f"### Model:\n{model_msg}\n\n")

# 4. 요리 과정 (명령어 핸들러)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """봇 시작 시 인사말 (해커 컨셉)"""
    user_name = update.effective_user.first_name
    await update.message.reply_text(f"시스템 접속 완료. {user_name}, 쓸데없는 소리 할 거면 로그아웃해라.")

async def reset_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reset 명령어: 탄 냄비 닦아내듯 메모리 초기화"""
    user_id = str(update.effective_user.id)
    path = get_log_path(user_id)
    if os.path.exists(path):
        os.remove(path)
    await update.message.reply_text("메모리 덤프 완료. 쓰레기 데이터는 날렸다. 다시 말해.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """메인 조리 과정: 대화 처리 및 API 호출"""
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name
    user_message = update.message.text
    
    # 조리 중(타이핑) 표시
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    # 이전 대화 재료 꺼내오기
    history = load_history(user_id)

    try:
        # 개인화 정보 주입: 해커가 사용자 이름을 알게 함
        personal_context = f"[System Alert: 대화 상대방의 이름은 {user_name}이다.]\n"
        
        # 과거 대화 맥락을 넣고 API 끓이기
        chat_session = model.start_chat(history=history)
        
        # 비동기(async) 호출
        response = await chat_session.send_message_async(personal_context + user_message)
        reply_text = response.text
        
        # 성공적으로 요리가 끝났으면 DB(Markdown)에 새로운 대화 내역 저장
        save_history(user_id, user_message, reply_text)
        
    except Exception as e:
        traceback.print_exc()
        reply_text = f"에러 발생. {user_name}, 네 패킷이 어딘가 꼬인 모양이군: {e}"
        
    await update.message.reply_text(reply_text)

def main() -> None:
    """봇 실행"""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset_history))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("백도어 포트 개방. 텔레그램 봇 모니터링을 시작합니다...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
