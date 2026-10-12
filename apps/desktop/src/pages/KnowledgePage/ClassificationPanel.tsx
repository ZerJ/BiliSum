import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { FileTextIcon } from "../../components/AppIcons";
import type { KnowledgeContentItem } from "../../types";

const itemKey = (item: KnowledgeContentItem) => `${item.kind}:${item.id}`;

export function ClassificationPanel({ onUpdated }: { onUpdated?: () => void }) {
  const [items, setItems] = useState<KnowledgeContentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [membershipFilter, setMembershipFilter] = useState("included");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [category, setCategory] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    void api.listKnowledgeContent().then((payload) => {
      if (!cancelled) { setItems(payload); setSelected(new Set()); }
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "内容加载失败");
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [reload]);

  const categories = useMemo(() => [...new Set(items.map((item) => item.category).filter(Boolean))].sort(), [items]);
  const visible = useMemo(() => items.filter((item) =>
    (membershipFilter === "all" || (membershipFilter === "included" ? item.included : !item.included))
    &&
    (filter === "all" || (filter === "unfiled" ? !item.category : item.category === filter.slice(9)))
    && item.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [items, filter, membershipFilter, query]);
  const allSelected = visible.length > 0 && visible.every((item) => selected.has(itemKey(item)));

  async function save(nextCategory: string) {
    if (busy || !selected.size) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const targets = items.filter((item) => selected.has(itemKey(item)));
      const result = await api.classifyKnowledgeContent(targets.map(({ id, kind }) => ({ id, kind })), nextCategory);
      setItems((current) => current.map((item) => selected.has(itemKey(item)) ? { ...item, category: nextCategory } : item));
      setSelected(new Set());
      setFilter("all");
      setNotice(`已将 ${result.updated} 项内容${nextCategory ? `归入“${nextCategory}”` : "移回未分类"}`);
      onUpdated?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "分类保存失败");
    } finally { setBusy(false); }
  }

  async function setMembership(included: boolean) {
    if (busy || !selected.size || selected.size > 500) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const targets = items.filter((item) => selected.has(itemKey(item)));
      const result = await api.setKnowledgeContentMembership(
        targets.map(({ id, kind }) => ({ id, kind })),
        included,
      );
      setItems((current) => current.map((item) => selected.has(itemKey(item)) ? { ...item, included, category: included ? item.category : "" } : item));
      setSelected(new Set());
      setNotice(`已${included ? "加入" : "移出"} ${result.updated} 项内容`);
      onUpdated?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新知识库内容失败");
    } finally { setBusy(false); }
  }

  return (
    <div className="knowledge-classification">
      <div className="knowledge-section-head"><h2>内容分类</h2><span>{items.length} 项内容</span></div>
      <div className="knowledge-classification-toolbar">
        <input className="input-field" type="search" aria-label="搜索内容" placeholder="搜索标题" value={query} disabled={busy} onChange={(event) => { setQuery(event.target.value); setSelected(new Set()); }} />
        <select className="input-field" aria-label="按收录状态筛选" value={membershipFilter} disabled={busy} onChange={(event) => { setMembershipFilter(event.target.value); setSelected(new Set()); }}>
          <option value="included">当前知识库</option><option value="excluded">未收录素材</option><option value="all">全部素材</option>
        </select>
        <select className="input-field" aria-label="按分类筛选" value={filter} disabled={busy} onChange={(event) => { setFilter(event.target.value); setSelected(new Set()); }}>
          <option value="all">全部分类</option><option value="unfiled">未分类</option>
          {categories.map((name) => <option key={name} value={`category:${name}`}>{name}</option>)}
        </select>
        <button className="secondary-button" type="button" disabled={busy || loading} onClick={() => setReload((value) => value + 1)}>刷新</button>
      </div>
      <form className="knowledge-classification-toolbar" onSubmit={(event) => { event.preventDefault(); if (category.trim()) void save(category.trim()); }}>
        <span>已选 {selected.size} 项</span>
        <button className="secondary-button" disabled={busy || loading || !selected.size || selected.size > 500} type="button" onClick={() => void setMembership(true)}>加入知识库</button>
        <button className="secondary-button" disabled={busy || loading || !selected.size || selected.size > 500} type="button" onClick={() => void setMembership(false)}>移出知识库</button>
        <input className="input-field" list="knowledge-category-options" aria-label="目标分类" placeholder="选择或输入分类" maxLength={80} value={category} disabled={busy} onChange={(event) => setCategory(event.target.value)} />
        <datalist id="knowledge-category-options">{categories.map((name) => <option key={name} value={name} />)}</datalist>
        <button className="primary-button" disabled={busy || loading || !selected.size || selected.size > 500 || !category.trim() || items.some((item) => selected.has(itemKey(item)) && !item.included)} type="submit">{busy ? "保存中..." : "归入分类"}</button>
        <button className="secondary-button" disabled={busy || loading || !selected.size || selected.size > 500 || items.some((item) => selected.has(itemKey(item)) && !item.included)} type="button" onClick={() => void save("")}>移回未分类</button>
      </form>
      {selected.size > 500 ? <div role="alert">每次最多选择 500 项内容。</div> : null}
      {error ? <div className="knowledge-status-banner danger" role="alert">{error}</div> : null}
      {notice ? <div className="knowledge-status-banner success" role="status">{notice}</div> : null}
      {loading ? <div className="knowledge-empty-state">正在加载内容...</div> : <>
        <label className="knowledge-classification-select-all"><input type="checkbox" checked={allSelected} disabled={busy || !visible.length} onChange={() => setSelected(allSelected ? new Set() : new Set(visible.map(itemKey)))} />全选当前结果（{visible.length}）</label>
        <div className="knowledge-classification-list" aria-busy={busy}>
          {visible.map((item) => <div className="knowledge-classification-row" key={itemKey(item)}>
            <input type="checkbox" aria-label={`选择 ${item.title}`} checked={selected.has(itemKey(item))} disabled={busy} onChange={() => setSelected((current) => { const next = new Set(current); if (next.has(itemKey(item))) next.delete(itemKey(item)); else next.add(itemKey(item)); return next; })} />
            <div className="knowledge-classification-cover">{item.cover_url ? <img src={item.cover_url} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <FileTextIcon />}</div>
            <Link to={item.kind === "video" ? `/videos/${encodeURIComponent(item.id)}` : `/knowledge/articles/${encodeURIComponent(item.id)}`}>{item.title}<small>{item.kind === "video" ? "视频" : "文章"}</small></Link>
            <span className="knowledge-classification-category">{item.included ? (item.category || "未分类") : "未收录"}</span>
          </div>)}
        </div>
        {!visible.length && !error ? <div className="knowledge-empty-state">没有符合条件的内容。</div> : null}
      </>}
    </div>
  );
}
