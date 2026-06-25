import { useRef, useState } from "react";

import { Icon } from "./Icon";

interface Props {
  disabled: boolean;
  onSend: (message: string) => void;
  busy?: boolean;
  onStop?: () => void;
}

export function Composer({ disabled, onSend, busy = false, onStop }: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const autoGrow = (el: HTMLTextAreaElement) => {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  const canSend = value.trim().length > 0 && !disabled;

  const submit = () => {
    if (!canSend) return;
    onSend(value.trim());
    setValue("");
    if (ref.current) ref.current.style.height = "auto";
  };

  return (
    <div className="composer">
      <textarea
        ref={ref}
        className="composer__field"
        rows={1}
        aria-label="Ask about an incident"
        placeholder="Ask about an incident…"
        value={value}
        disabled={disabled}
        onChange={(e) => {
          setValue(e.target.value);
          autoGrow(e.target);
        }}
        onKeyDown={(e) => {
          // Cmd/Ctrl+Enter sends (matches the hint); plain Enter inserts a newline.
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer__actions">
        {busy && onStop ? (
          <button className="btn btn--stop" onClick={onStop} aria-label="Stop the investigation">
            <Icon name="alert" size={15} />
            <span>Stop</span>
          </button>
        ) : (
          <button
            className="btn btn--send"
            disabled={!canSend}
            onClick={submit}
            aria-label="Send message"
          >
            <Icon name="send" size={15} />
            <span>Send</span>
          </button>
        )}
        <span className="composer__hint">⌘↵ to send · ↵ for newline</span>
      </div>
    </div>
  );
}
