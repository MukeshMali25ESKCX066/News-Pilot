"use client";

import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  apiRequest,
  type AppSettings,
  type Article,
  type CollectionResult,
  type GeneratedArticleResult,
  type NewsEntry,
  type PublishResult,
  type Source,
  type Story,
  type WordPressStatus,
} from "@/lib/api";

type ToastMessage = { text: string; error: boolean };
type ArticleTextField =
  | "title"
  | "body"
  | "summary"
  | "seo_title"
  | "slug"
  | "meta_description"
  | "focus_keyphrase";

function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : "An unexpected error occurred.";
}

function plainText(value: string | null): string {
  return (value ?? "")
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function articleDate(value: string): string {
  const date = new Date(`${value.replace(" ", "T").replace(/Z$/, "")}Z`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function safeExternalUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Dashboard() {
  const [sources, setSources] = useState<Source[]>([]);
  const [news, setNews] = useState<NewsEntry[]>([]);
  const [stories, setStories] = useState<Story[]>([]);
  const [articles, setArticles] = useState<Article[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [toastMessage, setToastMessage] = useState<ToastMessage | null>(null);
  const [sourceName, setSourceName] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceCategory, setSourceCategory] = useState("");
  const [editingSource, setEditingSource] = useState<Source | null>(null);
  const [autoPublish, setAutoPublish] = useState(false);
  const [wordpressConnected, setWordpressConnected] = useState(false);
  const [automationMessage, setAutomationMessage] = useState(
    "Checking WordPress connection…",
  );
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [collecting, setCollecting] = useState(false);
  const [currentArticle, setCurrentArticle] = useState<Article | null>(null);
  const [tagsInput, setTagsInput] = useState("");
  const [wordpressMessage, setWordpressMessage] = useState(
    "Checking WordPress connection…",
  );
  const [savingDraft, setSavingDraft] = useState(false);
  const [pushingToWordpress, setPushingToWordpress] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sourceFormRef = useRef<HTMLFormElement>(null);

  const notify = useCallback((text: string, error = false) => {
    setToastMessage({ text, error });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToastMessage(null), 4000);
  }, []);

  const loadSources = useCallback(async () => {
    const result = await apiRequest<Source[]>("/sources");
    setSources(result);
  }, []);

  const loadNews = useCallback(async () => {
    const result = await apiRequest<NewsEntry[]>("/news");
    setNews(result);
  }, []);

  const loadStories = useCallback(async () => {
    const result = await apiRequest<Story[]>("/stories");
    setStories(result);
  }, []);

  const loadArticles = useCallback(async () => {
    const result = await apiRequest<Article[]>("/articles");
    setArticles(result);
  }, []);

  const loadAutomation = useCallback(async () => {
    const settings = await apiRequest<AppSettings>("/settings");
    setAutoPublish(settings.auto_publish);
    setWordpressConnected(settings.connected);
    setAutomationMessage(
      !settings.connected
        ? `${settings.error || "WordPress is not connected."} Automatic publishing ${settings.auto_publish ? "is enabled but may fail" : "is off"}.`
        : settings.auto_publish
          ? `Automatic publishing is ON for ${settings.site_url}. New generated articles will be public.`
          : `Connected to ${settings.site_url}. Automatic publishing is off.`,
    );
  }, []);

  const refreshDashboard = useCallback(async () => {
    try {
      await Promise.all([
        loadSources(),
        loadNews(),
        loadStories(),
        loadArticles(),
        loadAutomation(),
      ]);
    } catch (error) {
      notify(`Could not load dashboard: ${messageFrom(error)}`, true);
    } finally {
      setLoaded(true);
    }
  }, [loadArticles, loadAutomation, loadNews, loadSources, loadStories, notify]);

  useEffect(() => {
    void refreshDashboard();
    return () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, [refreshDashboard]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (currentArticle && dialog && !dialog.open) dialog.showModal();
    if (!currentArticle && dialog?.open) dialog.close();
  }, [currentArticle]);

  async function handleSourceSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = {
      name: sourceName.trim(),
      url: sourceUrl.trim(),
      category: sourceCategory.trim() || null,
      source_type: "rss",
    };
    const path = editingSource ? `/sources/${editingSource.id}` : "/sources";

    try {
      await apiRequest(path, {
        method: editingSource ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      setEditingSource(null);
      setSourceName("");
      setSourceUrl("");
      setSourceCategory("");
      sourceFormRef.current?.reset();
      notify(editingSource ? "Source updated." : "News source added.");
      await loadSources();
    } catch (error) {
      notify(`Could not save source: ${messageFrom(error)}`, true);
    }
  }

  function startEditingSource(source: Source) {
    setEditingSource(source);
    setSourceName(source.name);
    setSourceUrl(source.url);
    setSourceCategory(source.category ?? "");
    sourceFormRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function cancelSourceEdit() {
    setEditingSource(null);
    setSourceName("");
    setSourceUrl("");
    setSourceCategory("");
    sourceFormRef.current?.reset();
  }

  async function deleteSource(source: Source) {
    if (
      !window.confirm(
        "Delete this source and its collected news? Saved article drafts will remain.",
      )
    ) {
      return;
    }
    try {
      await apiRequest(`/sources/${source.id}`, { method: "DELETE" });
      notify("Source deleted.");
      await Promise.all([loadSources(), loadNews(), loadStories()]);
    } catch (error) {
      notify(`Could not delete source: ${messageFrom(error)}`, true);
    }
  }

  async function openArticle(articleId: number) {
    const article = await apiRequest<Article>(`/articles/${articleId}`);
    setCurrentArticle(article);
    setTagsInput(article.tags.join(", "));
    try {
      const wordpress = await apiRequest<WordPressStatus>("/wordpress/status");
      setWordpressConnected(wordpress.connected);
      setWordpressMessage(
        wordpress.connected
          ? `Connected to ${wordpress.site_url}`
          : wordpress.error ||
              "Not connected. Install and configure the NewsPilot Connector plugin.",
      );
    } catch (error) {
      setWordpressConnected(false);
      setWordpressMessage(
        `Could not check WordPress connection: ${messageFrom(error)}`,
      );
    }
  }

  function updateArticleField(field: ArticleTextField, value: string) {
    setCurrentArticle((article) =>
      article ? { ...article, [field]: value } : article,
    );
  }

  async function saveArticleDraft(): Promise<Article> {
    if (!currentArticle) throw new Error("No article is open.");
    if (!currentArticle.title.trim()) {
      throw new Error("Post title cannot be empty.");
    }
    const saved = await apiRequest<Article>(`/articles/${currentArticle.id}`, {
      method: "PUT",
      body: JSON.stringify({
        title: currentArticle.title,
        body: currentArticle.body ?? "",
        summary: currentArticle.summary ?? "",
        seo_title: currentArticle.seo_title ?? "",
        slug: currentArticle.slug ?? "",
        meta_description: currentArticle.meta_description ?? "",
        focus_keyphrase: currentArticle.focus_keyphrase ?? "",
        tags: tagsInput
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
      }),
    });
    setCurrentArticle(saved);
    setTagsInput(saved.tags.join(", "));
    return saved;
  }

  async function saveDraft() {
    setSavingDraft(true);
    try {
      await saveArticleDraft();
      notify("Draft saved in NewsPilot.");
      await loadArticles();
    } catch (error) {
      notify(`Could not save draft: ${messageFrom(error)}`, true);
    } finally {
      setSavingDraft(false);
    }
  }

  async function pushToWordpress() {
    if (!currentArticle) return;
    setPushingToWordpress(true);
    try {
      const saved = await saveArticleDraft();
      const result = await apiRequest<PublishResult>(
        `/articles/${saved.id}/push-to-wordpress`,
        { method: "POST" },
      );
      setCurrentArticle((article) =>
        article
          ? {
              ...article,
              wordpress_post_id: result.wordpress_post_id,
              wordpress_edit_url: result.wordpress_edit_url,
              wordpress_status: result.wordpress_status ?? "draft",
            }
          : article,
      );
      setWordpressMessage(
        "Post is connected to WordPress as a draft. You can update it from here.",
      );
      notify(result.status === "created" ? "Draft pushed to WordPress." : "WordPress post updated.");
      await loadArticles();
    } catch (error) {
      notify(`Could not push to WordPress: ${messageFrom(error)}`, true);
    } finally {
      setPushingToWordpress(false);
    }
  }

  function notifyGeneration(
    result: GeneratedArticleResult,
    action: "created" | "regenerated",
  ) {
    if (result.automatic_publish?.status === "failed") {
      notify(
        `Draft ${action}, but automatic WordPress publishing failed: ${result.automatic_publish.error}`,
        true,
      );
    } else if (result.automatic_publish?.status === "published") {
      notify(
        action === "created"
          ? "Article generated and published to WordPress."
          : "Article regenerated and published to WordPress.",
      );
    } else if (action === "regenerated") {
      notify(`Draft regenerated from ${result.related_count ?? 0} related report(s).`);
    } else if (result.related_count !== undefined) {
      notify(
        `Article draft created from ${result.related_count} related report(s) across ${result.source_count ?? 0} source(s).`,
      );
    } else {
      notify("Article draft created.");
    }
  }

  async function generateNewsArticle(entry: NewsEntry) {
    const action = `news-${entry.id}`;
    setBusyAction(action);
    try {
      const result = await apiRequest<GeneratedArticleResult>(
        `/news/${entry.id}/generate`,
        { method: "POST" },
      );
      notifyGeneration(result, "created");
      await loadArticles();
      await openArticle(result.article_id);
    } catch (error) {
      notify(`Could not create draft: ${messageFrom(error)}`, true);
    } finally {
      setBusyAction(null);
    }
  }

  async function generateStoryArticle(story: Story) {
    const action = `story-${story.title}`;
    setBusyAction(action);
    try {
      const result = await apiRequest<GeneratedArticleResult>("/articles/generate", {
        method: "POST",
        body: JSON.stringify({
          story_title: story.title,
          summary: story.summary || "",
          evidence_sources: story.items.map((entry) => entry.url).filter(Boolean),
        }),
      });
      notifyGeneration(result, "created");
      await loadArticles();
      await openArticle(result.article_id);
    } catch (error) {
      notify(messageFrom(error), true);
    } finally {
      setBusyAction(null);
    }
  }

  async function regenerateArticle(article: Article) {
    const action = `regenerate-${article.id}`;
    setBusyAction(action);
    try {
      const result = await apiRequest<GeneratedArticleResult>(
        `/articles/${article.id}/regenerate`,
        { method: "POST" },
      );
      notifyGeneration(result, "regenerated");
      await loadArticles();
      await openArticle(article.id);
    } catch (error) {
      notify(`Could not regenerate draft: ${messageFrom(error)}`, true);
    } finally {
      setBusyAction(null);
    }
  }

  async function collectNews() {
    setCollecting(true);
    try {
      const result = await apiRequest<CollectionResult>("/collect", {
        method: "POST",
      });
      if (result.errors.length) {
        const issues = result.errors
          .map((error) => `${error.source}: ${error.error}`)
          .join(" · ");
        notify(
          `Collected ${result.new_items} update(s), but some sources failed: ${issues}`,
          true,
        );
      } else {
        notify(`Collection complete: ${result.new_items} new update(s).`);
      }
      await Promise.all([loadStories(), loadSources(), loadNews()]);
    } catch (error) {
      notify(`Collection failed: ${messageFrom(error)}`, true);
    } finally {
      setCollecting(false);
    }
  }

  async function toggleAutoPublish(enabled: boolean) {
    const previous = autoPublish;
    if (
      enabled &&
      !window.confirm(
        "New generated articles will be published publicly to WordPress automatically. Continue?",
      )
    ) {
      return;
    }
    setAutoPublish(enabled);
    try {
      const settings = await apiRequest<AppSettings>("/settings", {
        method: "PUT",
        body: JSON.stringify({ auto_publish: enabled }),
      });
      setAutoPublish(settings.auto_publish);
      setWordpressConnected(settings.connected);
      setAutomationMessage(
        !settings.connected
          ? `${settings.error || "WordPress is not connected."} Automatic publishing is ${settings.auto_publish ? "enabled but may fail" : "off"}.`
          : settings.auto_publish
            ? `Automatic publishing is ON for ${settings.site_url}. New generated articles will be public.`
            : `Connected to ${settings.site_url}. Automatic publishing is off.`,
      );
      notify(
        settings.auto_publish
          ? "Automatic WordPress publishing enabled."
          : "Automatic WordPress publishing disabled.",
      );
    } catch (error) {
      setAutoPublish(previous);
      notify(`Could not update publishing setting: ${messageFrom(error)}`, true);
    }
  }

  function scrollToSection(sectionId: string) {
    document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth" });
  }

  const activeSourceCount = sources.filter((source) => Boolean(source.active)).length;
  return (
    <>
      <div className="layout">
        <aside className="sidebar">
          <div className="brand">
            <span className="brand-mark">N</span> NewsPilot AI
          </div>
          <div>
            <p className="nav-label">Workspace</p>
            <button className="nav-item active" type="button" onClick={() => scrollToSection("overview")}>
              Overview
            </button>
            <button className="nav-item" type="button" onClick={() => scrollToSection("news-panel")}>
              Collected news
            </button>
            <button className="nav-item" type="button" onClick={() => scrollToSection("sources-panel")}>
              News sources
            </button>
            <button className="nav-item" type="button" onClick={() => scrollToSection("stories-panel")}>
              Discovered stories
            </button>
            <button className="nav-item" type="button" onClick={() => scrollToSection("articles-panel")}>
              Article drafts
            </button>
          </div>
          <p className="sidebar-note">
            Your editorial workspace
            <br />
            Collect · Review · Draft
          </p>
        </aside>

        <main id="overview">
          <header className="topbar">
            <div>
              <p className="eyebrow">News automation workspace</p>
              <h1>Overview</h1>
            </div>
            <div className="connection">
              <span className="dot" />
              Backend connected
            </div>
          </header>

          <section className="welcome">
            <div>
              <h2>Your news desk, at a glance</h2>
              <p>
                Collect updates from your feeds, explore story clusters, and create
                drafts for editorial review.
              </p>
            </div>
            <button
              className="button primary"
              type="button"
              onClick={collectNews}
              disabled={collecting}
            >
              {collecting ? "Collecting…" : "↻  Collect news now"}
            </button>
          </section>

          <section className="metrics" aria-label="Workspace totals">
            <article className="metric">
              <div className="metric-label">Active sources</div>
              <div className="metric-value">{loaded ? activeSourceCount : "—"}</div>
            </article>
            <article className="metric">
              <div className="metric-label">Collected news</div>
              <div className="metric-value">{loaded ? news.length : "—"}</div>
            </article>
            <article className="metric">
              <div className="metric-label">Discovered stories</div>
              <div className="metric-value">{loaded ? stories.length : "—"}</div>
            </article>
            <article className="metric">
              <div className="metric-label">Article drafts</div>
              <div className="metric-value">{loaded ? articles.length : "—"}</div>
            </article>
          </section>

          <div className="content-grid">
            <div>
              <section className="panel" id="news-panel">
                <div className="panel-head">
                  <div>
                    <h2>Collected news · last 24 hours</h2>
                    <p>
                      Original-language feed text. Drafts use the RSS excerpt, not a
                      full-article rewrite.
                    </p>
                  </div>
                  <button
                    className="button"
                    type="button"
                    onClick={() => void loadNews().catch((error: unknown) => notify(messageFrom(error), true))}
                  >
                    Refresh list
                  </button>
                </div>
                <div className="panel-body">
                  <div className="list">
                    {!loaded ? (
                      <Empty>Loading collected news…</Empty>
                    ) : news.length === 0 ? (
                      <Empty>No news collected yet. Add an RSS feed and click Collect news now.</Empty>
                    ) : (
                      news.map((entry) => {
                        const sourceUrl = safeExternalUrl(entry.url);
                        return (
                          <div className="list-item news-item" key={entry.id}>
                            <div>
                              <div className="item-title" dir="auto">{entry.title}</div>
                              <div className="item-meta">
                                {entry.source_name}
                                {entry.source_category ? ` · ${entry.source_category}` : ""}
                                {entry.published_at ? ` · ${entry.published_at}` : ""}
                              </div>
                              {plainText(entry.summary) && (
                                <div className="news-summary" dir="auto">
                                  {plainText(entry.summary)}
                                </div>
                              )}
                            </div>
                            <div className="source-actions">
                              {sourceUrl && (
                                <a
                                  className="news-link"
                                  href={sourceUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  Open source ↗
                                </a>
                              )}
                              <button
                                className="button small"
                                type="button"
                                disabled={busyAction === `news-${entry.id}`}
                                onClick={() => void generateNewsArticle(entry)}
                              >
                                Generate article
                              </button>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>
              </section>

              <section className="panel" id="stories-panel">
                <div className="panel-head">
                  <div>
                    <h2>Discovered stories</h2>
                    <p>Related updates grouped into story clusters</p>
                  </div>
                  <button
                    className="button"
                    type="button"
                    onClick={() => void refreshDashboard()}
                  >
                    Refresh
                  </button>
                </div>
                <div className="panel-body">
                  <div className="list">
                    {!loaded ? (
                      <Empty>Loading stories…</Empty>
                    ) : stories.length === 0 ? (
                      <Empty>No stories yet. Collect news from your feeds to get started.</Empty>
                    ) : (
                      stories.map((story) => (
                        <div className="list-item" key={story.title}>
                          <div>
                            <div className="item-title">{story.title}</div>
                            <div className="item-meta">
                              {story.items?.length || 0} source update(s) · Score {story.score || 0}
                            </div>
                          </div>
                          <button
                            className="button"
                            type="button"
                            disabled={busyAction === `story-${story.title}`}
                            onClick={() => void generateStoryArticle(story)}
                          >
                            Create draft
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </section>

              <section className="panel" id="articles-panel">
                <div className="panel-head">
                  <div>
                    <h2>Article drafts</h2>
                    <p>Generated drafts awaiting editorial review</p>
                  </div>
                </div>
                <div className="panel-body">
                  <div className="list">
                    {!loaded ? (
                      <Empty>Loading drafts…</Empty>
                    ) : articles.length === 0 ? (
                      <Empty>Your generated drafts will appear here.</Empty>
                    ) : (
                      articles.map((article) => {
                        const wordPressStatus = article.wordpress_post_id
                          ? ` · WordPress ${article.wordpress_status || "draft"}`
                          : "";
                        return (
                          <div className="list-item" key={article.id}>
                            <div>
                              <div className="item-title">{article.title}</div>
                              <div className="item-meta">
                                {article.language.toUpperCase()} · {articleDate(article.created_at)}
                                {wordPressStatus}
                              </div>
                            </div>
                            <span className="badge">
                              {article.wordpress_post_id
                                ? `WP ${article.wordpress_status || "draft"}`
                                : article.status}
                            </span>
                            <div className="source-actions">
                              <button
                                className="button small"
                                type="button"
                                onClick={() =>
                                  void openArticle(article.id).catch((error: unknown) =>
                                    notify(`Could not open article: ${messageFrom(error)}`, true),
                                  )
                                }
                              >
                                Edit draft
                              </button>
                              <button
                                className="button small"
                                type="button"
                                disabled={busyAction === `regenerate-${article.id}`}
                                onClick={() => void regenerateArticle(article)}
                              >
                                Regenerate
                              </button>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>
              </section>
            </div>

            <div>
              <section className="panel" id="sources-panel">
                <div className="panel-head">
                  <div>
                    <h2>News sources</h2>
                    <p>Hindi and English RSS feeds are supported</p>
                  </div>
                </div>
                <div className="panel-body">
                  <form className="source-form" ref={sourceFormRef} onSubmit={handleSourceSubmit}>
                    <input
                      name="name"
                      placeholder="Source name"
                      aria-label="Source name"
                      value={sourceName}
                      onChange={(event) => setSourceName(event.target.value)}
                      required
                    />
                    <input
                      name="url"
                      type="url"
                      placeholder="RSS feed URL (Hindi supported)"
                      aria-label="RSS feed URL"
                      value={sourceUrl}
                      onChange={(event) => setSourceUrl(event.target.value)}
                      required
                    />
                    <input
                      name="category"
                      placeholder="Category (optional)"
                      aria-label="Category"
                      value={sourceCategory}
                      onChange={(event) => setSourceCategory(event.target.value)}
                    />
                    <div className="form-actions">
                      <button className="button blue" type="submit">
                        {editingSource ? "Save changes" : "Add news source"}
                      </button>
                      {editingSource && (
                        <button className="button" type="button" onClick={cancelSourceEdit}>
                          Cancel edit
                        </button>
                      )}
                    </div>
                  </form>
                  <div className="item-meta source-help">
                    Paste an RSS/Atom URL or a website that advertises a feed. Feed text is
                    shown in its original language.
                  </div>
                  <div className="list source-list">
                    {!loaded ? (
                      <Empty>Loading sources…</Empty>
                    ) : sources.length === 0 ? (
                      <Empty>No sources yet. Add an RSS feed above.</Empty>
                    ) : (
                      sources.map((source) => (
                        <div className="list-item" key={source.id}>
                          <div>
                            <div className="item-title">{source.name}</div>
                            <div className="item-meta">{source.category || source.url}</div>
                          </div>
                          <span className="badge">{source.source_type}</span>
                          <div className="source-actions">
                            <button
                              className="button small"
                              type="button"
                              onClick={() => startEditingSource(source)}
                            >
                              Edit
                            </button>
                            <button
                              className="button small danger"
                              type="button"
                              onClick={() => void deleteSource(source)}
                            >
                              Delete
                            </button>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </section>

              <section className="panel">
                <div className="panel-head">
                  <div>
                    <h2>Automation</h2>
                    <p>Collection runs while the server is on</p>
                  </div>
                </div>
                <div className="panel-body">
                  <div className="item-title">RSS collection interval</div>
                  <div className="item-meta">Configured in the server environment.</div>
                  <label className="auto-publish-control">
                    <input
                      type="checkbox"
                      checked={autoPublish}
                      disabled={!wordpressConnected && !autoPublish}
                      onChange={(event) => void toggleAutoPublish(event.target.checked)}
                    />
                    <span>Automatically publish generated articles to WordPress</span>
                  </label>
                  <div className="item-meta" aria-live="polite">{automationMessage}</div>
                </div>
              </section>
            </div>
          </div>
        </main>
      </div>

      <dialog
        className="article-dialog"
        ref={dialogRef}
        onClose={() => setCurrentArticle(null)}
      >
        {currentArticle && (
          <>
            <div className="dialog-head">
              <div>
                <p className="dialog-eyebrow">
                  {currentArticle.language.toUpperCase()} · NewsPilot {currentArticle.status}
                </p>
                <h2>Post editor</h2>
              </div>
              <button
                className="button"
                type="button"
                onClick={() => dialogRef.current?.close()}
              >
                Close
              </button>
            </div>
            <div className="wp-editor-grid">
              <section className="wp-editor-main" aria-label="WordPress-style post editor">
                <input
                  className="wp-title-input"
                  placeholder="Add title"
                  aria-label="Post title"
                  value={currentArticle.title}
                  onChange={(event) => updateArticleField("title", event.target.value)}
                />
                <textarea
                  className="wp-body-input"
                  placeholder="Start writing your post…"
                  aria-label="Post content"
                  value={currentArticle.body ?? ""}
                  onChange={(event) => updateArticleField("body", event.target.value)}
                />
                <section className="seo-panel" aria-label="Yoast SEO settings">
                  <h3>Yoast SEO</h3>
                  <p className="seo-help">
                    Review these generated search fields before saving or sending the post
                    to WordPress.
                  </p>
                  <div className="seo-fields">
                    <label className="seo-field">
                      SEO title
                      <input
                        maxLength={160}
                        value={currentArticle.seo_title ?? ""}
                        onChange={(event) => updateArticleField("seo_title", event.target.value)}
                      />
                    </label>
                    <label className="seo-field">
                      URL slug
                      <input
                        maxLength={200}
                        value={currentArticle.slug ?? ""}
                        onChange={(event) => updateArticleField("slug", event.target.value)}
                      />
                    </label>
                    <label className="seo-field full">
                      Meta description
                      <textarea
                        maxLength={320}
                        value={currentArticle.meta_description ?? ""}
                        onChange={(event) =>
                          updateArticleField("meta_description", event.target.value)
                        }
                      />
                      <span className="seo-counter">
                        {(currentArticle.meta_description ?? "").length}/160 characters
                      </span>
                    </label>
                    <label className="seo-field">
                      Focus keyphrase
                      <input
                        maxLength={160}
                        value={currentArticle.focus_keyphrase ?? ""}
                        onChange={(event) =>
                          updateArticleField("focus_keyphrase", event.target.value)
                        }
                      />
                    </label>
                    <label className="seo-field">
                      Tags (comma-separated)
                      <input
                        maxLength={300}
                        value={tagsInput}
                        onChange={(event) => setTagsInput(event.target.value)}
                      />
                    </label>
                    <label className="seo-field full">
                      Excerpt
                      <textarea
                        maxLength={500}
                        value={currentArticle.summary ?? ""}
                        onChange={(event) => updateArticleField("summary", event.target.value)}
                      />
                    </label>
                  </div>
                </section>
              </section>
              <aside className="wp-editor-sidebar">
                <h3>Publish</h3>
                <div className="wp-connection-status" aria-live="polite">
                  {wordpressMessage}
                </div>
                {currentArticle.wordpress_post_id &&
                  safeExternalUrl(currentArticle.wordpress_edit_url) && (
                  <a
                    className="button"
                    href={safeExternalUrl(currentArticle.wordpress_edit_url) ?? undefined}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open in WordPress
                  </a>
                )}
                <p className="item-meta">
                  {currentArticle.wordpress_status === "publish"
                    ? "This WordPress post is public. Updating it changes the live post."
                    : currentArticle.wordpress_post_id
                      ? "Push updates the linked WordPress post without publishing it."
                      : "Push creates a WordPress draft. It will not publish automatically."}
                </p>
              </aside>
            </div>
            <div className="dialog-actions">
              <button
                className="button"
                type="button"
                disabled={savingDraft || pushingToWordpress}
                onClick={() => void saveDraft()}
              >
                {savingDraft ? "Saving…" : "Save draft"}
              </button>
              <button
                className="button blue"
                type="button"
                disabled={!wordpressConnected || savingDraft || pushingToWordpress}
                onClick={() => void pushToWordpress()}
              >
                {pushingToWordpress
                  ? "Pushing…"
                  : currentArticle.wordpress_post_id
                    ? "Update WordPress post"
                    : "Push to WordPress"}
              </button>
            </div>
          </>
        )}
      </dialog>

      {toastMessage && (
        <div className={`toast${toastMessage.error ? " error" : ""}`} role="status">
          {toastMessage.text}
        </div>
      )}
    </>
  );
}
