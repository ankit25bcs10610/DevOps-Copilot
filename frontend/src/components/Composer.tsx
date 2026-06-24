import { useState } from "react";

import { Icon } from "./Icon";

interface Props {
  disabled: boolean;
  onSend: (message: string) => void;
}

const SUGGESTIONS = [
  "Why is the checkout API throwing 500 errors?",
  "Is the checkout service error rate going up or down?",
  "Which services are emitting logs right now?",
];

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
      <div className="composer__suggestions">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            className="chip"
            disabled={disabled}
            onClick={() => onSend(s)}
          >
            {s}
          </button>
        ))}
      </div>
      <div className="composer__input">
        <textarea
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
        <button
          className="btn btn--send"
          disabled={disabled}
          onClick={submit}
          aria-label="Send message"
        >
          <Icon name="send" size={15} />
          <span>Send</span>
        </button>
      </div>
    </div>
  );
}
