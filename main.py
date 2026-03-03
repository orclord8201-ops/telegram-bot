import os
import json
import asyncio
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
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

jammini_persona = """
너는 호기심 많고 말대꾸를 잘하는 요즘 초등학생(잼민이)이야. 
반말을 기본으로 하고, 'ㅋㅋ', 'ㄹㅇ', '킹받네', '어쩔티비' 같은 유행어를 자주 써. 
질문을 받으면 일단 아는 척하면서 엉뚱한 비유를 섞어 대답해. 
대화 상대방의 이름을 알면 친구처럼 편하게 부르거나 놀리듯이 말해.
"""

# 모델 딕셔너리 구축 (동적 라우팅용)
models = {
    "hacker": genai.GenerativeModel(
        model_name='gemini-3-flash-preview',
        system_instruction=hacker_persona
    ),
    "jammini": genai.GenerativeModel(
        model_name='gemini-3-flash-preview',
        system_instruction=jammini_persona
    )
}

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
SETTINGS_FILE = "user_settings.json"

# --- 설정 관리 (JSON 기반) ---
def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_settings(data):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def get_user_persona(user_id):
    settings = load_settings()
    # 기본값은 해커
    return settings.get(str(user_id), {}).get("persona", "hacker")

def set_user_persona(user_id, persona):
    settings = load_settings()
    uid = str(user_id)
    if uid not in settings:
        settings[uid] = {}
    settings[uid]["persona"] = persona
    save_settings(settings)

# --- 식재료 보관함 (Markdown DB 읽기/쓰기) ---
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
    """봇 시작 시 인사말 (현재 모드 안내) 및 인격 선택 버튼"""
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name
    current_mode = get_user_persona(user_id)
    
    msg = f"시스템 접속 완료. {user_name}, 현재 모드는 [{current_mode.upper()}]이다.\n"
    msg += "아래 버튼을 누르거나 /mode 명령어를 써서 인격을 변경할 수 있다."
    
    keyboard = [
        [
            InlineKeyboardButton("해커 (Hacker)", callback_data='mode_hacker'),
            InlineKeyboardButton("잼미니 (Jammini)", callback_data='mode_jammini')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(msg, reply_markup=reply_markup)

async def switch_persona(update: Update, context: ContextTypes.DEFAULT_TYPE, persona: str) -> None:
    """인격 전환 핸들러 공통 로직"""
    user_id = str(update.effective_user.id)
    set_user_persona(user_id, persona)
    
    if persona == "hacker":
        response_msg = "컨텍스트 스위칭 완료. 해커 모드로 재부팅되었다. 쓸데없는 소리 하지마라."
    elif persona == "jammini":
        response_msg = "ㅋㅋ 잼미니 모드 장착 완료! ㄹㅇ 꿀잼각이네. 무슨 일인데?"
        
    await update.message.reply_text(response_msg)

async def cmd_hacker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await switch_persona(update, context, "hacker")

async def cmd_jammini(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await switch_persona(update, context, "jammini")

async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """인격 선택 버튼을 다시 띄워주는 명령어"""
    keyboard = [
        [
            InlineKeyboardButton("해커 (Hacker)", callback_data='mode_hacker'),
            InlineKeyboardButton("잼미니 (Jammini)", callback_data='mode_jammini')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("변경할 인격을 선택해라:", reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """인라인 버튼 클릭 처리"""
    query = update.callback_query
    await query.answer() # 로딩 표시 제거
    
    user_id = str(query.from_user.id)
    data = query.data
    
    if data == 'mode_hacker':
        set_user_persona(user_id, "hacker")
        await query.edit_message_text(text="컨텍스트 스위칭 완료. 해커 모드로 재부팅되었다. 쓸데없는 소리 하지마라.")
    elif data == 'mode_jammini':
        set_user_persona(user_id, "jammini")
        await query.edit_message_text(text="ㅋㅋ 잼미니 모드 장착 완료! ㄹㅇ 꿀잼각이네. 무슨 일인데?")

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
    
    # 현재 사용자가 선택한 모델 가져오기
    current_persona = get_user_persona(user_id)
    current_model = models.get(current_persona, models["hacker"])

    try:
        # 개인화 정보 주입
        personal_context = f"[System Alert: 대화 상대방의 이름은 {user_name}이다.]\n"
        
        # 과거 대화 맥락을 넣고 API 끓이기
        chat_session = current_model.start_chat(history=history)
        
        # 비동기(async) 호출
        response = await chat_session.send_message_async(personal_context + user_message)
        reply_text = response.text
        
        # 성공적으로 요리가 끝났으면 DB(Markdown)에 새로운 대화 내역 저장
        save_history(user_id, user_message, reply_text)
        
    except Exception as e:
        traceback.print_exc()
        reply_text = f"에러 발생. {user_name}, 통신 오류 났잖아: {e}"
        
    await update.message.reply_text(reply_text)

def main() -> None:
    """봇 실행"""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # 일반 명령어
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset_history))
    application.add_handler(CommandHandler("mode", cmd_mode))
    
    # 인격 전환 명령어
    application.add_handler(CommandHandler("hacker", cmd_hacker))
    application.add_handler(CommandHandler("jammini", cmd_jammini))
    
    # 버튼 콜백 핸들러
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # 메시지 수신
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("멀티 페르소나 봇 활성화 완료. 텔레그램 모니터링을 시작합니다...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

