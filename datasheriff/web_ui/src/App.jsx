import React, { useMemo, useState } from "react";
import { v4 as uuidv4 } from "uuid";

import ChatWindow from "./components/ChatWindow";
import InputBar from "./components/InputBar";
import { sendChatMessage } from "./api";

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(() => uuidv4());

  const history = useMemo(
    () => messages.map((item) => ({ role: item.role, content: item.content })),
    [messages]
  );

  const submitMessage = async (presetMessage) => {
    const messageText = (presetMessage ?? input).trim();
    if (!messageText || loading) {
      return;
    }

    const userMessage = { id: uuidv4(), role: "user", content: messageText };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);

    try {
      const data = await sendChatMessage({
        message: messageText,
        session_id: sessionId,
        history,
      });

      setSessionId(data.session_id || sessionId);
      setMessages((prev) => [
        ...prev,
        { id: uuidv4(), role: "assistant", content: data.response || "No response received." },
      ]);
    } catch (error) {
      const errorMessage =
        error?.response?.data?.detail ||
        error?.message ||
        "Something went wrong while contacting DataSheriff.";
      setMessages((prev) => [
        ...prev,
        {
          id: uuidv4(),
          role: "assistant",
          content: `I ran into an issue: ${errorMessage}`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <h1>DataSheriff 🤠</h1>
          <p>Your AI-powered data catalog agent</p>
        </div>
      </header>
      <section className="chat-panel">
        <ChatWindow messages={messages} loading={loading} onStarterClick={submitMessage} />
        <InputBar
          value={input}
          onChange={setInput}
          onSubmit={() => submitMessage()}
          disabled={loading}
        />
      </section>
    </main>
  );
}
