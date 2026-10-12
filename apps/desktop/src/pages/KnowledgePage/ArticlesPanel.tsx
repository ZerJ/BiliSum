import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api";
import { FileTextIcon, SearchIcon } from "../../components/AppIcons";
import type { KnowledgeDocumentSummary } from "../../types";

type ArticlesPanelProps = {
  refreshToken: number;
};

function formatArticleDate(value?: string | null) {
  if (!value) {
    return "时间未知";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "时间未知";
  }
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric" }).format(date);
}

export function ArticlesPanel({ refreshToken }: ArticlesPanelProps) {
  const [articles, setArticles] = useState<KnowledgeDocumentSummary[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    void api.listKnowledgeArticles().then((payload) => {
      if (!cancelled) {
        setArticles(payload);
      }
    }).catch((reason) => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : "文章列表加载失败");
      }
    }).finally(() => {
      if (!cancelled) {
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [refreshToken]);

  const visibleArticles = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    if (!keyword) {
      return articles;
    }
    return articles.filter((article) => `${article.title}\n${article.author}`.toLocaleLowerCase().includes(keyword));
  }, [articles, query]);

  return (
    <div className="knowledge-articles-view">
      <div className="knowledge-articles-heading">
        <div>
          <h2>已导入文章</h2>
          <p>{articles.length} 篇文章已加入知识库</p>
        </div>
        <label className="search-field knowledge-article-search">
          <span className="search-icon" aria-hidden="true"><SearchIcon /></span>
          <input
            className="input-field input-field-search"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索标题或作者"
          />
        </label>
      </div>

      {loading ? <div className="knowledge-empty-state">正在读取已导入文章...</div> : null}
      {!loading && error ? <div className="knowledge-status-banner danger" role="alert">{error}</div> : null}
      {!loading && !error && !visibleArticles.length ? (
        <div className="knowledge-empty-state">
          {query.trim() ? "没有匹配的文章。" : "还没有导入文章，使用右上角的“导入文章”添加第一篇。"}
        </div>
      ) : null}

      <div className="knowledge-article-grid">
        {visibleArticles.map((article) => (
          <Link
            key={article.document_id}
            className="knowledge-article-card"
            to={`/knowledge/articles/${encodeURIComponent(article.document_id)}`}
          >
            <div className="knowledge-article-cover" aria-hidden="true">
              {article.cover_url ? <img src={article.cover_url} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <FileTextIcon />}
            </div>
            <div className="knowledge-article-card-body">
              <span className="knowledge-article-source">{article.source_type === "wechat_article" ? "微信公众号" : "网页文章"}</span>
              <h3>{article.title}</h3>
              <p>{article.author || "作者未知"} · {formatArticleDate(article.published_at || article.updated_at)}</p>
              <div className="knowledge-article-card-meta">
                <span>{article.image_count} 张图片</span>
                {article.image_count ? <span>已理解 {article.analyzed_image_count}/{article.image_count}</span> : null}
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
