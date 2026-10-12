import { useEffect, useState } from "react";

import { api } from "../../api";
import type { KnowledgeArticleImportResponse } from "../../types";

type ArticleImportDialogProps = {
  open: boolean;
  onClose(): void;
  onImported(result: KnowledgeArticleImportResponse): void;
};

export function ArticleImportDialog({ open, onClose, onImported }: ArticleImportDialogProps) {
  const [url, setUrl] = useState("");
  const [analyzeImages, setAnalyzeImages] = useState(true);
  const [summarize, setSummarize] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) {
      return;
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) {
        onClose();
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open, submitting]);

  if (!open) {
    return null;
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = url.trim();
    if (!value || submitting) {
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const result = await api.importKnowledgeArticle({
        url: value,
        analyze_images: analyzeImages,
        summarize,
        force_refresh: true,
      });
      setUrl("");
      onImported(result);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文章导入失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onClick={() => { if (!submitting) onClose(); }}>
      <form className="knowledge-article-import" role="dialog" aria-modal="true" aria-labelledby="article-import-title" onSubmit={handleSubmit} onClick={(event) => event.stopPropagation()}>
        <div className="knowledge-section-head">
          <div>
            <h3 id="article-import-title">导入文章</h3>
            <p>网页文章或微信公众号链接</p>
          </div>
          <button className="tertiary-button knowledge-dialog-close" type="button" aria-label="关闭" title="关闭" disabled={submitting} onClick={onClose}>×</button>
        </div>
        <label className="knowledge-article-field">
          <span>文章链接</span>
          <input
            className="input-field"
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://mp.weixin.qq.com/s/..."
            autoFocus
            required
          />
        </label>
        <label className="knowledge-article-toggle">
          <input type="checkbox" checked={analyzeImages} onChange={(event) => setAnalyzeImages(event.target.checked)} />
          <span>分析文章图片</span>
        </label>
        <label className="knowledge-article-toggle">
          <input type="checkbox" checked={summarize} onChange={(event) => setSummarize(event.target.checked)} />
          <span>生成正文总结</span>
        </label>
        {error ? <div className="knowledge-status-banner danger" role="alert">{error}</div> : null}
        <div className="knowledge-article-actions">
          <button className="secondary-button" type="button" disabled={submitting} onClick={onClose}>取消</button>
          <button className="primary-button" type="submit" disabled={submitting || !url.trim()}>{submitting ? "导入中..." : "导入"}</button>
        </div>
      </form>
    </div>
  );
}
