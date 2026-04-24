import React from "react";

export default function InputBar({ value, onChange, onSubmit, disabled }) {
  const handleKeyDown = (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSubmit();
    }
  };

  return (
    <div className="input-bar">
      <textarea
        className="chat-input"
        placeholder="Ask about your metadata, lineage, quality, or governance..."
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        rows={1}
      />
      <button className="send-btn" onClick={onSubmit} disabled={disabled || !value.trim()}>
        Send
      </button>
    </div>
  );
}
