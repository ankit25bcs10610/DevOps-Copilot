import { describe, expect, it } from "vitest";

import { modelShort, providerLabel } from "./useConfig";

describe("providerLabel", () => {
  it("maps known providers and falls back to the raw id / dash", () => {
    expect(providerLabel("anthropic")).toBe("Anthropic");
    expect(providerLabel("deepseek")).toBe("DeepSeek");
    expect(providerLabel("groq")).toBe("Groq");
    expect(providerLabel("unknown-x")).toBe("unknown-x");
    expect(providerLabel()).toBe("—");
  });
});

describe("modelShort", () => {
  it("prettifies model ids across providers", () => {
    expect(modelShort("claude-opus-4-8")).toBe("Opus 4.8");
    expect(modelShort("claude-haiku-4-5")).toBe("Haiku 4.5");
    expect(modelShort("gpt-4o")).toBe("GPT-4o");
    expect(modelShort("gemini-1.5-pro")).toBe("Gemini 1.5 Pro");
    expect(modelShort("deepseek-chat")).toBe("DeepSeek Chat");
    expect(modelShort("llama-3.3-70b-versatile")).toBe("Llama 3.3 70B");
    expect(modelShort()).toBe("");
  });
});
