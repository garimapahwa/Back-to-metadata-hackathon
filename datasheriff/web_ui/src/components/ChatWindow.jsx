import React from "react";

import Message from "./Message";

const STARTERS = [
  "Who owns the orders table?",
  "Find all untagged PII columns",
  "What breaks if I modify the customers table?",
  "Run a governance health report",
];

export default function ChatWindow({ messages, loading, onStarterClick }) {
  if (!messages.length) {
    return (
      <div className="chat-window empty">
        <div className="starter-card">
          <h2>Start an investigation</h2>
          <p>Try one of these prompts:</p>
          <div className="starter-grid">
            {STARTERS.map((item) => (
              <button key={item} className="starter-btn" onClick={() => onStarterClick(item)}>
                {item}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-window">
      {messages.map((message) => (
        <Message key={message.id} role={message.role} content={message.content} />
      ))}
      {loading && (
        <div className="message-row agent">
          <div className="message-bubble agent loading">🤠 Investigating...</div>
        </div>
      )}
    </div>
  );
}
