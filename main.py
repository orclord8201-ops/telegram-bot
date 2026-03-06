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

PERSONAS_FILE = "personas.json"

# 페르소나 데이터 로드 및 모델 딕셔너리 구축 (동적 라우팅용)
def load_personas():
    if not os.path.exists(PERSONAS_FILE):
        raise FileNotFoundError(f"{PERSONAS_FILE} 파일이 없습니다. 인격 데이터를 설정해주세요.")
    with open(PERSONAS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

personas_data = load_personas()
models = {}
for pid, pdata in personas_data.items():
    models[pid] = genai.GenerativeModel(
        model_name='gemini-2.5-flash',
        system_instruction=pdata["instruction"]
    )

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
SETTINGS_FILE = "user_settings.json"
MEMORY_FILE = "memory_db.json"
USAGE_FILE = "usage_db.json"

import datetime

# --- 사용량 추적 DB ---
def load_usage_db():
    if not os.path.exists(USAGE_FILE):
        return {}
    with open(USAGE_FILE, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_usage_db(db):
    with open(USAGE_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=4)

def record_usage(user_id, tokens):
    db = load_usage_db()
    uid = str(user_id)
    today = datetime.date.today().isoformat()
    
    if uid not in db:
        db[uid] = {}
    if today not in db[uid]:
        db[uid][today] = {"requests": 0, "tokens": 0}
        
    db[uid][today]["requests"] += 1
    db[uid][today]["tokens"] += tokens
    save_usage_db(db)

# --- 메모리 DB (활성 컨텍스트) ---
def load_memory_db():
    if not os.path.exists(MEMORY_FILE):
        return {}
    with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_memory_db(db):
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=4)


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
    # 기본값은 자비스(Jarvis)
    return settings.get(str(user_id), {}).get("persona", "jarvis")

def set_user_persona(user_id, persona):
    settings = load_settings()
    uid = str(user_id)
    if uid not in settings:
        settings[uid] = {}
    settings[uid]["persona"] = persona
    save_settings(settings)

# --- 식재료 보관함 (이중 로깅) ---
def get_log_path(user_id, user_name):
    # 영구 기록用の 마크다운 파일 경로 (logs/UserID_Name.md)
    safe_name = "".join(c for c in user_name if c.isalnum() or c in " _-")
    return os.path.join(LOG_DIR, f"{user_id}_{safe_name}.md")

def load_history(user_id):
    """API에 보낼 실제 대화 맥락을 memory_db에서 로드"""
    db = load_memory_db()
    return db.get(str(user_id), [])

def save_history(user_id, user_name, user_msg, model_msg):
    """메모리 DB에 맥락 업데이트 및 Markdown 로그에 보관용 기록 추가"""
    uid_str = str(user_id)
    
    # 1. 활성 메모리(memory_db) 업데이트
    db = load_memory_db()
    if uid_str not in db:
        db[uid_str] = []
    db[uid_str].append({"role": "user", "parts": [user_msg]})
    db[uid_str].append({"role": "model", "parts": [model_msg]})
    save_memory_db(db)
    
    # 2. 영구 보관용 마크다운(logs/) 업데이트
    path = get_log_path(user_id, user_name)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(f"### User:\n{user_msg}\n\n")
        f.write(f"### Model:\n{model_msg}\n\n")

# --- UI 컴포넌트 ---
def get_persona_keyboard():
    """personas.json 데이터를 기반으로 동적 키보드 생성"""
    keyboard = []
    row = []
    for pid, pdata in personas_data.items():
        row.append(InlineKeyboardButton(pdata["name"], callback_data=f'mode_{pid}'))
        if len(row) >= 2: # 2열 배치
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

# 4. 요리 과정 (명령어 핸들러)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """봇 시작 시 인사말 (현재 모드 안내) 및 인격 선택 버튼"""
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name
    current_mode = get_user_persona(user_id)
    
    # 설정된 페르소나 이름 가져오기 (없으면 ID 출력)
    mode_name = personas_data.get(current_mode, {}).get("name", current_mode.upper())
    
    msg = f"시스템 접속 완료. {user_name}, 현재 모드는 [{mode_name}]이다.\n"
    msg += "아래 버튼을 누르거나 /mode 명령어를 써서 인격을 변경할 수 있다."
    
    await update.message.reply_text(msg, reply_markup=get_persona_keyboard())

async def switch_persona(update: Update, context: ContextTypes.DEFAULT_TYPE, persona: str) -> None:
    """인격 전환 핸들러 공통 로직 (채팅 명령어용)"""
    if persona not in personas_data:
        await update.message.reply_text("존재하지 않는 인격입니다.")
        return
        
    user_id = str(update.effective_user.id)
    set_user_persona(user_id, persona)
    
    mode_name = personas_data[persona]["name"]
    await update.message.reply_text(f"컨텍스트 스위칭 완료. [{mode_name}] 모드로 재부팅되었다.")

async def cmd_hacker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await switch_persona(update, context, "hacker")

async def cmd_jammini(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await switch_persona(update, context, "jammini")

async def cmd_jarvis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await switch_persona(update, context, "jarvis")

async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """인격 선택 버튼을 다시 띄워주는 명령어"""
    await update.message.reply_text("변경할 인격을 선택해라:", reply_markup=get_persona_keyboard())

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """인라인 버튼 클릭 처리"""
    print(f"DEBUG: Button clicked by {update.callback_query.from_user.first_name}")
    query = update.callback_query
    await query.answer() # 로딩 표시 제거
    
    user_id = str(query.from_user.id)
    data = query.data
    
    if data.startswith('mode_'):
        pid = data.split('_')[1]
        if pid in personas_data:
            set_user_persona(user_id, pid)
            mode_name = personas_data[pid]["name"]
            await query.edit_message_text(text=f"[{mode_name}] 모드 장착 완료! 대화를 시작해봐.")
        else:
            await query.edit_message_text(text="오류: 알 수 없는 인격입니다.")

async def reset_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reset 명령어: 탄 냄비 닦아내듯 메모리 초기화"""
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name
    
    # 1. 활성 메모리 삭제
    db = load_memory_db()
    if user_id in db:
        del db[user_id]
        save_memory_db(db)
        
    # 2. Markdown 로그에 절취선 표시 (삭제하지 않음)
    path = get_log_path(user_id, user_name)
    with open(path, 'a', encoding='utf-8') as f:
        f.write("\n--- [MEMORY RESET] ---\n\n")
        
    await update.message.reply_text("메모리 덤프 완료. 쓰레기 데이터는 날렸다. 다시 말해.")

async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/usage 명령어: 오늘 사용량 확인"""
    user_id = str(update.effective_user.id)
    today = datetime.date.today().isoformat()
    db = load_usage_db()
    
    user_stats = db.get(user_id, {}).get(today, {"requests": 0, "tokens": 0})
    reqs = user_stats["requests"]
    toks = user_stats["tokens"]
    
    msg = f"📊 **오늘의 대화 사용량 ({today})**\n\n"
    msg += f"- 보낸 대화 횟수: {reqs}회\n"
    msg += f"- 사용된 총 토큰: {toks:,}개\n\n"
    msg += "💡 *안심하세요! 현재 적용된 gemini-2.5-flash 모델은 하루 최대 1,500회의 대화를 무료로 제공합니다. 아직 매우 넉넉합니다.*"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """메인 조리 과정: 대화 처리 및 API 호출"""
    user_id = str(update.effective_user.id)
    user_name = update.effective_user.first_name
    user_message = update.message.text
    print(f"DEBUG: Message received from {user_name}: {user_message}")
    
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
        
        print(f"DEBUG: Calling Gemini API (Persona: {current_persona})...")
        # 과거 대화 맥락을 넣고 API 끓이기
        chat_session = current_model.start_chat(history=history)
        
        # 비동기(async) 호출
        response = await chat_session.send_message_async(personal_context + user_message)
        reply_text = response.text
        
        # 사용량 기록
        total_tokens = response.usage_metadata.total_token_count
        record_usage(user_id, total_tokens)
        
        print(f"DEBUG: Response generated successfully. Tokens used: {total_tokens}")
        
        # 성공적으로 요리가 끝났으면 DB 및 Markdown에 새로운 대화 내역 저장
        save_history(user_id, user_name, user_message, reply_text)
        print(f"DEBUG: History saved to Memory DB and {get_log_path(user_id, user_name)}")
        
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
    application.add_handler(CommandHandler("usage", cmd_usage))
    
    # 인격 전환 명령어
    application.add_handler(CommandHandler("hacker", cmd_hacker))
    application.add_handler(CommandHandler("jammini", cmd_jammini))
    application.add_handler(CommandHandler("jarvis", cmd_jarvis))
    
    # 버튼 콜백 핸들러
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # 메시지 수신
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("멀티 페르소나 봇 활성화 완료. 텔레그램 모니터링을 시작합니다...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

