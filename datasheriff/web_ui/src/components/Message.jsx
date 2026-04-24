import React from "react";
import ReactMarkdown from "react-markdown";

export default function Message({ role, content }) {
  const isUser = role === "user";

  return (
    <div className={`message-row ${isUser ? "user" : "agent"}`}>
      <div className={`message-bubble ${isUser ? "user" : "agent"}`}>
        {isUser ? <p>{content}</p> : <ReactMarkdown>{content}</ReactMarkdown>}
      </div>
    </div>
  );
}
