import streamlit as st
import pandas as pd
import sqlite3
import google.generativeai as genai
import os
import datetime

# --- 設定 & セットアップ ---
st.set_page_config(page_title="AIパーソナルトレーナー", layout="wide")

# ディレクトリとファイルパス
KNOWLEDGE_DIR = "knowledge"
DB_FILE = "training.db"
KNOWLEDGE_FILES = {
    "personality": "personality.txt",
    "bible": "training_bible.txt",
    "goals": "goals.txt"
}

# --- データベース管理 ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # トレーニングログテーブル
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT,
                    exercise TEXT,
                    weight REAL,
                    reps INTEGER,
                    sets INTEGER DEFAULT 1,
                    note TEXT
                )''')
    
    # 既存テーブルへのカラム追加（マイグレーション）
    try:
        c.execute("ALTER TABLE logs ADD COLUMN sets INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        # カラムが既に存在する場合は何もしない
        pass
    
    # 身体データテーブル
    c.execute('''CREATE TABLE IF NOT EXISTS body_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT,
                    weight REAL,
                    body_fat REAL,
                    note TEXT
                )''')
    
    conn.commit()
    conn.close()

def add_log_db(date, exercise, weight, reps, sets, note):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO logs (date, exercise, weight, reps, sets, note) VALUES (?, ?, ?, ?, ?, ?)",
              (date, exercise, weight, reps, sets, note))
    conn.commit()
    conn.close()

def add_body_stats_db(date, weight, body_fat, note):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO body_stats (date, weight, body_fat, note) VALUES (?, ?, ?, ?)",
              (date, weight, body_fat, note))
    conn.commit()
    conn.close()

def get_logs():
    conn = sqlite3.connect(DB_FILE)
    try:
        df = pd.read_sql_query("SELECT * FROM logs ORDER BY date DESC", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    return df

def get_exercises():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("SELECT DISTINCT exercise FROM logs")
        exercises = [row[0] for row in c.fetchall()]
    except:
        exercises = []
    conn.close()
    return exercises

def get_latest_body_weight():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("SELECT weight FROM body_stats ORDER BY date DESC LIMIT 1")
        result = c.fetchone()
    except:
        result = None
    conn.close()
    return result[0] if result else None

# --- AIツール関数 (Function Calling) ---

def save_training_log(exercise: str, weight: float, reps: int, sets: int = 1, date: str = None, note: str = ""):
    """
    トレーニングの記録をデータベースに保存します。セット数も記録できます。
    Args:
        exercise: 種目名 (例: ベンチプレス)
        weight: 重量(kg)
        reps: 回数
        sets: セット数 (デフォルトは1)
        date: 日付 (YYYY-MM-DD形式)。指定がなければ記録時の日付を使用します。
        note: メモや備考
    """
    if not date:
        date = datetime.date.today().strftime("%Y-%m-%d")
    
    add_log_db(date, exercise, weight, reps, sets, note)
    return f"【記録完了】{date} {exercise} {weight}kg {reps}回 × {sets}セット を保存しました。"

def save_body_stats(weight: float, body_fat: float = None, date: str = None):
    """
    体重や体脂肪率をデータベースに保存します。
    Args:
        weight: 体重(kg)
        body_fat: 体脂肪率(%)。不明な場合はNoneまたは0。
        date: 日付 (YYYY-MM-DD形式)。指定がなければ記録時の日付を使用します。
    """
    if not date:
        date = datetime.date.today().strftime("%Y-%m-%d")
        
    add_body_stats_db(date, weight, body_fat, "")
    return f"【記録完了】{date} 体重{weight}kg" + (f" 体脂肪率{body_fat}%" if body_fat else "") + " を保存しました。"

# ツール実行用のマップ
tools_map = {
    'save_training_log': save_training_log,
    'save_body_stats': save_body_stats
}

# --- ナレッジ読み込み ---
def load_knowledge():
    knowledge = {}
    if not os.path.exists(KNOWLEDGE_DIR):
        os.makedirs(KNOWLEDGE_DIR)
        
    for key, filename in KNOWLEDGE_FILES.items():
        try:
            with open(os.path.join(KNOWLEDGE_DIR, filename), "r", encoding="utf-8") as f:
                knowledge[key] = f.read().strip()
        except FileNotFoundError:
            with open(os.path.join(KNOWLEDGE_DIR, filename), "w", encoding="utf-8") as f:
                f.write("")
            knowledge[key] = ""
    return knowledge

# --- AIロジック ---
def get_ai_response(user_input, chat_history):
    knowledge = load_knowledge()
    current_weight = get_latest_body_weight()
    logs_df = get_logs()
    recent_logs = logs_df.head(5).to_string() if not logs_df.empty else "トレーニング記録はまだありません。"
    
    system_instruction = f"""
    【基本指示】
    あなたはユーザー専属のAIパーソナルトレーナーです。
    ユーザーとの会話を通じて、トレーニングの記録を行ったり、アドバイスを提供したりします。
    
    【重要：データ記録について】
    ユーザーがトレーニング内容や体重を報告した場合は、**必ず** 提供されたツール（Functions）を使用してデータベースに記録してください。
    - 「ベンチプレス60kg 10回 3セット」→ `save_training_log` (sets=3)
    - 「体重65kg」→ `save_body_stats`
    ツール呼び出しが成功したら、その結果（保存しました等）をユーザーに伝えてください。

    ---
    【ナレッジ：人格】
    {knowledge.get("personality", "")}

    【ナレッジ：トレーニングバイブル】
    {knowledge.get("bible", "")}

    【ナレッジ：目標】
    {knowledge.get("goals", "")}
    ---

    【ユーザーデータ参照】
    現在の体重: {current_weight} kg
    最近のトレーニングログ:
    {recent_logs}

    【行動指針】
    1. ナレッジファイル「人格」に基づいた口調で話してください。
    2. 「トレーニングバイブル」に基づき、エビデンスのない指導は避けてください。
    3. 会話の流れでツールを使用し、積極的に記録を代行してください。
    """

    try:
        api_key = None
        if "GOOGLE_API_KEY" in st.secrets:
            api_key = st.secrets["GOOGLE_API_KEY"]
        else:
            api_key = os.environ.get("GOOGLE_API_KEY")
        
        if not api_key:
            return "エラー: Google APIキーが見つかりません。.streamlit/secrets.tomlを確認してください。"

        genai.configure(api_key=api_key)
        model_name = "models/gemini-flash-latest" 
        tools = [save_training_log, save_body_stats]
        
        model = genai.GenerativeModel(model_name, system_instruction=system_instruction, tools=tools)
        
        history_for_model = []
        for msg in chat_history:
            role = "user" if msg["role"] == "user" else "model"
            if isinstance(msg["content"], str):
                 history_for_model.append({"role": role, "parts": [msg["content"]]})
            
        chat = model.start_chat(history=history_for_model)
        response = chat.send_message(user_input)
        
        # --- 修正版：頑丈なループ処理 ---
        # AIの返答にFunction Callingが含まれているかチェックし、あれば実行して結果を返します。
        # GeminiはテキストとFunction Callを同時に返すことがあるため、両方を処理します。

        while True:
            function_calls = []
            text_parts = []

            # レスポンス内の各パートを確認
            if response.parts:
                for part in response.parts:
                     # function_callがある場合
                    if part.function_call:
                        function_calls.append(part.function_call)
                    # textがある場合
                    if part.text:
                        text_parts.append(part.text)
            
            # テキストがあれば表示用履歴に追加 (ストリームではないので一括表示)
            if text_parts:
                combined_text = "".join(text_parts)
                # ループの途中でもテキストがあれば表示したいが、Streamlitの仕様上
                # 最後にまとめて返す形にするか、あるいは途中で表示するか。
                # ここではシンプルに「最終的なテキスト」として蓄積するロジックにするが、
                # 会話の自然さを保つには、ループ終了後にまとめて返すのが無難。
                pass 

            # ツール呼び出しがなければループ終了
            if not function_calls:
                break
            
            # 見つけたすべてのツールを実行
            responses_to_return = []
            for fc in function_calls:
                fn_name = fc.name
                args = dict(fc.args)
                
                api_response = "エラー: ツール実行失敗"
                if fn_name in tools_map:
                    try:
                        api_response = tools_map[fn_name](**args)
                    except Exception as e:
                        api_response = f"エラー: {str(e)}"
                
                # 結果を単純な辞書構造などで返す（google.api_coreの型を使わない）
                # Geminiライブラリの仕様に合わせる
                responses_to_return.append({
                    "function_response": {
                        "name": fn_name,
                        "response": {"result": api_response}
                    }
                })
            
            # 結果をまとめてAIに送り返し、次の反応を待つ
            response = chat.send_message(responses_to_return)
        
        # 最終的な回答からテキストのみを抽出して返す
        final_text = []
        if response.parts:
            for part in response.parts:
                if part.text:
                    final_text.append(part.text)
        
        return "".join(final_text)
        
    except Exception as e:
        return f"AIエラー: {str(e)}"


# --- メインアプリ ---
def main():
    init_db()
    st.title("💪 AIパーソナルトレーナー")

    tab1, tab2 = st.tabs(["💬 AIチャット", "📊 データ分析"])

    # --- タブ1: AIチャット ---
    with tab1:
        st.header("AIトレーナーとの会話")
        
        if "messages" not in st.session_state:
            st.session_state.messages = []

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if prompt := st.chat_input("トレーナーに報告・相談する..."):
            st.chat_message("user").markdown(prompt)
            st.session_state.messages.append({"role": "user", "content": prompt})
            
            with st.spinner("AIトレーナーが考えています..."):
                response = get_ai_response(prompt, st.session_state.messages[:-1])
                st.chat_message("assistant").markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response})

    # --- タブ2: データ分析 ---
    with tab2:
        st.header("パフォーマンス分析")
        logs = get_logs()
        if not logs.empty:
            exercises = logs['exercise'].unique()
            if len(exercises) > 0:
                selected_ex = st.selectbox("分析する種目を選択", exercises)
                ex_data = logs[logs['exercise'] == selected_ex]
                
                if not ex_data.empty:
                    col1, col2 = st.columns(2)
                    with col1:
                        pb = ex_data['weight'].max()
                        st.metric("自己ベスト (PB)", f"{pb} kg")
                    with col2:
                        total = len(ex_data)
                        st.metric("総セット数", f"{total} sets")
                    
                    st.subheader("重量の推移")
                    st.line_chart(ex_data.set_index('date')['weight'])
                    st.subheader("履歴データ")
                    st.dataframe(ex_data)
                else:
                    st.info("データがありません。")
            else:
                 st.info("種目データが見つかりません。")
        else:
            st.info("データがまだありません。AIチャットでトレーニングを報告してください！")

if __name__ == "__main__":
    main()