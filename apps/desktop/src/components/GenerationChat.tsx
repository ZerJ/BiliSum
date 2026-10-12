import { useRef, useState, type FormEvent } from "react";

export type ChatPreference = { noteMode: "text" | "visual"; generateMindmap: boolean };
type Message = { role: "user" | "assistant"; text: string };

export function GenerationChat({ onStart }: {
  onStart(event: FormEvent, source: string, preference: ChatPreference): Promise<boolean>;
}) {
  const [messages, setMessages] = useState<Message[]>([{ role: "assistant", text: "把视频链接发给我，我们一起选生成形式。支持 Bilibili、YouTube、抖音链接或 BV 号。" }]);
  const [input, setInput] = useState("");
  const [source, setSource] = useState("");
  const [stage, setStage] = useState<"source" | "note" | "confirm" | "submitted">("source");
  const [preference, setPreference] = useState<ChatPreference>({ noteMode: "text", generateMindmap: false });
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const choices = stage === "note" ? ["文字笔记", "图文笔记"] : stage === "confirm" ? ["开始生成", "重新选择"] : [];

  async function send(event: FormEvent, answer = input) {
    event.preventDefault();
    const value = answer.trim();
    if (!value || submitting.current) return;
    setInput("");
    const reply = (text: string) => setMessages((items) => [...items, { role: "user", text: value }, { role: "assistant", text }]);
    if (/^(取消|重来|重新选择)$/.test(value)) {
      setStage(source ? "note" : "source");
      reply(source ? "好，重新选：想要文字笔记还是图文笔记？" : "请发送视频链接。");
      return;
    }
    const link = value.match(/https?:\/\/[^\s<>"“”]+/i)?.[0]?.replace(/[，。！？）)]+$/, "") || value.match(/\bBV[a-zA-Z0-9]{10}\b/)?.[0];
    if (link) {
      setSource(link);
      setStage("note");
      reply("收到链接。想要方便阅读的文字笔记，还是带视频截图的图文笔记？");
    } else if (stage === "source") {
      reply("还没有识别到视频链接，请粘贴完整链接或 BV 号。");
    } else if (stage === "note") {
      const visual = /^(2|图文|图文笔记|带图|截图)$/.test(value);
      if (!visual && !/^(1|文字|文字笔记|纯文本|文本)$/.test(value)) {
        reply("可以选「文字笔记」或「图文笔记」，也可以回复 1 或 2。");
        return;
      }
      setPreference({ noteMode: visual ? "visual" : "text", generateMindmap: false });
      setStage("confirm");
      reply(`将生成${visual ? "图文笔记（附视频截图）" : "文字笔记"}。结果可在详情页导出为 Markdown 或 Obsidian 笔记。确认后开始处理；遇到合集或多 P 视频，还会请你选择范围。`);
    } else if (stage === "confirm" && /^(开始生成|确认|开始|好|yes)$/i.test(value)) {
      submitting.current = true;
      setBusy(true);
      reply("正在提交，处理进度和结果会在视频详情页展示。");
      try {
        if (await onStart(event, source, preference)) {
          setStage("submitted");
        } else {
          setMessages((items) => [...items, { role: "assistant", text: "暂未提交成功，请查看页面提示，处理后可重新点击「开始生成」。" }]);
        }
      } catch (error) {
        setMessages((items) => [...items, { role: "assistant", text: error instanceof Error ? error.message : "提交失败，请重试。" }]);
      } finally {
        submitting.current = false;
        setBusy(false);
      }
    } else if (stage === "submitted") {
      reply("已提交，请继续选择视频范围或查看处理进度。也可以发送另一个链接。");
    } else {
      reply("回复「开始生成」即可提交，或「重新选择」修改形式。也可以发送另一个链接。");
    }
  }

  return <div className="generation-chat">
    <div className="generation-chat-messages" role="log" aria-label="生成对话" aria-live="polite" ref={(node) => { if (node) node.scrollTop = node.scrollHeight; }}>
      {messages.map((message, index) => <div key={index} className={`generation-chat-message is-${message.role}`}><strong>{message.role === "user" ? "你" : "BiliSum"}</strong><p>{message.text}</p></div>)}
    </div>
    <div className="generation-chat-choices">{choices.map((choice) => <button className="secondary-button" type="button" key={choice} disabled={busy} onClick={(event) => void send(event, choice)}>{choice}</button>)}</div>
    <form className="generation-chat-composer" onSubmit={(event) => void send(event)}>
      <input className="input-field" aria-label="对话消息" placeholder={stage === "source" ? "发送视频链接…" : "输入回答，或发送新链接…"} value={input} disabled={busy} onChange={(event) => setInput(event.target.value)} />
      <button className="primary-button" type="submit" disabled={busy || !input.trim()}>{busy ? "提交中…" : "发送"}</button>
    </form>
  </div>;
}
