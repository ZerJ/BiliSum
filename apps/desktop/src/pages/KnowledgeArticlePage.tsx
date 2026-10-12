import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { CornerUpLeftIcon, ExternalLinkIcon } from "../components/AppIcons";
import { MarkdownContent } from "../components/MarkdownContent";
import type { KnowledgeDocument } from "../types";

function formatDate(value?: string | null) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" }).format(date);
}

export function KnowledgeArticlePage() {
  const { documentId = "" } = useParams();
  const [article, setArticle] = useState<KnowledgeDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    void api.getKnowledgeArticle(documentId).then((payload) => {
      if (!cancelled) {
        setArticle(payload);
      }
    }).catch((reason) => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : "文章内容加载失败");
      }
    }).finally(() => {
      if (!cancelled) {
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  if (loading) {
    return <section className="knowledge-article-detail-state"><strong>正在读取文章...</strong></section>;
  }

  if (!article || error) {
    return (
      <section className="knowledge-article-detail-state">
        <strong>文章不可用</strong>
        <span>{error || "这篇文章可能已被删除。"}</span>
        <Link className="secondary-button" to="/knowledge?view=articles">返回文章列表</Link>
      </section>
    );
  }

  const publishedLabel = formatDate(article.published_at || article.updated_at);

  return (
    <section className="knowledge-article-detail-page">
      <header className="knowledge-article-detail-header">
        <Link className="knowledge-article-back" to="/knowledge?view=articles" aria-label="返回文章列表" title="返回文章列表">
          <CornerUpLeftIcon aria-hidden="true" />
        </Link>
        <div className="knowledge-article-title-block">
          <span>{article.source_type === "wechat_article" ? "微信公众号" : "网页文章"}</span>
          <h1>{article.title}</h1>
          <p>
            <span>{article.author || "作者未知"}</span>
            {publishedLabel ? <span>{publishedLabel}</span> : null}
            <span>{article.image_count} 张图片，已理解 {article.analyzed_image_count} 张</span>
          </p>
        </div>
        <a className="secondary-button knowledge-article-original" href={article.source_url} target="_blank" rel="noreferrer">
          <ExternalLinkIcon aria-hidden="true" />
          <span>打开原文</span>
        </a>
      </header>
      <div className="knowledge-article-reading-pane">
        {article.summary_markdown ? (
          <section className="knowledge-article-summary">
            <h2>正文总结</h2>
            <MarkdownContent
              className="knowledge-article-summary-markdown"
              content={article.summary_markdown}
            />
          </section>
        ) : null}
        <MarkdownContent className="knowledge-article-markdown" content={article.content_markdown} />
      </div>
    </section>
  );
}
