import os
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage

load_dotenv()

app = Flask(__name__)

# Low-cost model: gpt-4o-mini (cheapest, fast, good quality)
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.7,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# In-memory conversation store (replace with DB later)
# Key: session_id, Value: list of LangChain messages
conversations = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data.get("message", "").strip()
    session_id = data.get("session_id", "default")

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    # Initialize history if new session
    if session_id not in conversations:
        conversations[session_id] = []

    # Add user message
    conversations[session_id].append(HumanMessage(content=user_message))

    # Call LangChain / OpenAI
    response = llm.invoke(conversations[session_id])

    # Add AI response to history
    conversations[session_id].append(AIMessage(content=response.content))

    return jsonify({
        "reply": response.content,
        "session_id": session_id,
        "model": "gpt-4o-mini"
    })


@app.route("/clear", methods=["POST"])
def clear():
    data = request.get_json()
    session_id = data.get("session_id", "default")
    conversations[session_id] = []
    return jsonify({"status": "cleared"})


@app.route("/history", methods=["GET"])
def history():
    session_id = request.args.get("session_id", "default")
    msgs = conversations.get(session_id, [])
    return jsonify({
        "history": [
            {"role": "user" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
            for m in msgs
        ]
    })


if __name__ == "__main__":
    app.run(host='0.0.0.0',debug=True, port=8000)