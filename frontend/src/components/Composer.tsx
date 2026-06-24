import { useState } from "react";

import { Icon } from "./Icon";

interface Props {
  disabled: boolean;
  onSend: (message: string) => void;
}

export function Composer({ disabled, onSend }: Props) {
  const [value, setValue] = useState("");

  const submit = () => {
    const text = value.trim();
    if (!text || disabled) return;
    onSend(text);
    setValue("");
  };

  return (
    <div className="composer">
      <textarea
        className="composer__field"
        rows={1}
        aria-label="Ask about an incident"
        placeholder="Ask about an incident…"
        value={value}
        disabled={disabled}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer__actions">
        <button
          className="btn btn--send"
          disabled={disabled}
          onClick={submit}
          aria-label="Send message"
        >
          <Icon name="send" size={15} />
          <span>Send</span>
        </button>
        <span className="composer__hint">⌘ Enter</span>
      </div>
    </div>
  );
}
